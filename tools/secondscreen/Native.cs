using System.Runtime.InteropServices;

namespace SecondScreen;

internal static class Native
{
    [StructLayout(LayoutKind.Sequential)]
    public struct Rect
    {
        public int Left, Top, Right, Bottom;

        public int Width => Right - Left;

        public int Height => Bottom - Top;
    }

    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool PrintWindow(IntPtr hwnd, IntPtr hdc, uint flags);

    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hwnd, out Rect rect);

    [DllImport("user32.dll")]
    public static extern int GetSystemMetrics(int index);

    private const int SM_CXSCREEN = 0;
    private const int SM_CYSCREEN = 1;
    private const int SM_CXVIRTUALSCREEN = 78;
    private const int SM_CYVIRTUALSCREEN = 79;
    private const int SM_XVIRTUALSCREEN = 76;
    private const int SM_YVIRTUALSCREEN = 77;

    public static Rect VirtualScreen(int index)
    {
        if (index < 0)
        {
            return new Rect
            {
                Left = GetSystemMetrics(SM_XVIRTUALSCREEN),
                Top = GetSystemMetrics(SM_YVIRTUALSCREEN),
                Right = GetSystemMetrics(SM_XVIRTUALSCREEN) + GetSystemMetrics(SM_CXVIRTUALSCREEN),
                Bottom = GetSystemMetrics(SM_YVIRTUALSCREEN) + GetSystemMetrics(SM_CYVIRTUALSCREEN),
            };
        }
        return new Rect { Left = 0, Top = 0, Right = GetSystemMetrics(SM_CXSCREEN), Bottom = GetSystemMetrics(SM_CYSCREEN) };
    }

    [DllImport("user32.dll")]
    private static extern IntPtr SetCursor(IntPtr cursor);

    private static readonly IntPtr NoCursor = IntPtr.Zero;

    /// <summary>
    /// Best effort cursor removal. A blank cursor handle only lasts until the next
    /// mouse move, so this is a nicety rather than a guarantee; it cannot be done
    /// properly without capturing the pointer ourselves, which GDI does not expose.
    /// </summary>
    public static void DrawNoCursor() => SetCursor(NoCursor);

    // A high resolution waitable timer, rather than Thread.Sleep. The system timer
    // ticks at about 15.6 ms, so Thread.Sleep(6) returns after 15.6 ms and every
    // frame costs at least that no matter how the pacing is computed. Asking for
    // the process-wide 1 ms resolution via timeBeginPeriod helps but is still only
    // 1 ms and upsets every other timer on the machine; the high resolution
    // waitable timer is per-timer, waits to roughly half a millisecond, and leaves
    // the rest of the system alone.
    private const uint CREATE_WAITABLE_TIMER_HIGH_RESOLUTION = 0x00000002;
    private const uint TIMER_ALL_ACCESS = 0x001F0003;

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateWaitableTimerExW(IntPtr attributes, string? name, uint flags, uint access);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetWaitableTimerEx(IntPtr timer, ref long due, int period, IntPtr completionRoutine,
                                                 IntPtr arg, uint flags);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CancelWaitableTimer(IntPtr timer);

    [DllImport("kernel32.dll")]
    private static extern bool CloseHandle(IntPtr handle);

    [DllImport("kernel32.dll")]
    private static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);

    private static IntPtr? _timer;
    private static bool _periodHeld;

    /// <summary>Which pacing mechanism is in use, for the banner.</summary>
    public static string TimerKind { get; private set; } = "none";

    /// <summary>Create the pacing timer, falling back through the options in order.</summary>
    public static void PrepareTimer()
    {
        // Best: a per-timer high resolution waitable timer.
        var handle = CreateWaitableTimerExW(IntPtr.Zero, null, CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, TIMER_ALL_ACCESS);
        if (IsUsable(handle))
        {
            _timer = handle;
            TimerKind = "high resolution waitable timer";
            return;
        }

        // Next: ask for a 1 ms system timer and use an ordinary waitable timer.
        // Less tidy -- it is a process-wide setting that costs a little power and
        // affects every other timer -- but without it Thread.Sleep rounds up to
        // 15.6 ms and a 60 fps target is simply unreachable.
        if (TimeBeginPeriod(1) == 0) _periodHeld = true;
        handle = CreateWaitableTimerExW(IntPtr.Zero, null, 0, TIMER_ALL_ACCESS);
        if (IsUsable(handle))
        {
            _timer = handle;
            TimerKind = _periodHeld ? "1 ms system timer" : "waitable timer (no resolution request)";
            return;
        }

        // Last resort. Frames will be quantised to the system timer tick.
        TimerKind = _periodHeld ? "Thread.Sleep at 1 ms resolution" : "Thread.Sleep (no resolution request)";
    }

    private static bool IsUsable(IntPtr handle) => handle != IntPtr.Zero && handle != new IntPtr(-1);

    public static void ReleaseTimer()
    {
        if (_timer is null) return;
        CloseHandle(_timer.Value);
        _timer = null;
        if (_periodHeld)
        {
            TimeEndPeriod(1);
            _periodHeld = false;
        }
        TimerKind = "none";
    }

    [DllImport("winmm.dll", EntryPoint = "timeBeginPeriod")]
    private static extern uint TimeBeginPeriod(uint milliseconds);

    [DllImport("winmm.dll", EntryPoint = "timeEndPeriod")]
    private static extern uint TimeEndPeriod(uint milliseconds);

    /// <summary>Wait about <paramref name="milliseconds"/>, precisely enough to hit a frame rate.</summary>
    /// <remarks>
    /// Spins the last fraction of a millisecond, because even a high resolution
    /// timer can come back a little early and at 60 fps one millisecond is six
    /// percent of the frame.
    /// </remarks>
    public static void PreciseSleep(double milliseconds)
    {
        if (milliseconds <= 0) return;
        var start = System.Diagnostics.Stopwatch.GetTimestamp();
        var ticks = (long)(milliseconds / 1000.0 * System.Diagnostics.Stopwatch.Frequency);

        // Below about 20 ms, spin the whole interval.
        //
        // A high resolution timer fires on time, but a thread *blocked* on it is
        // still only rescheduled on the scheduler quantum -- about 15.6 ms on
        // Windows. Waiting 5 ms and being handed back 6.9 ms later is what capped
        // the sender at 55 fps, and no amount of timer precision fixes it, because
        // the problem is being put to sleep at all. A spinning thread is never
        // descheduled.
        //
        // This costs a busy core, which is the right trade for a process whose whole
        // job is putting frames on a cable on time. Pass --no-spin to give the core
        // back and accept the lower ceiling.
        if (milliseconds > SpinThreshold)
        {
            var sleepable = (milliseconds - 4) / 2;
            if (_timer is { } timer)
            {
                var due = -(long)(sleepable * 10_000); // 100 ns units, negative is relative
                if (!SetWaitableTimerEx(timer, ref due, 0, IntPtr.Zero, IntPtr.Zero, 0))
                {
                    Thread.Sleep((int)sleepable);
                }
                else
                {
                    WaitForSingleObject(timer, unchecked((uint)-1));
                }
            }
            else
            {
                Thread.Sleep((int)sleepable);
            }
        }

        while (System.Diagnostics.Stopwatch.GetTimestamp() - start < ticks)
        {
            Thread.SpinWait(40);
        }
    }

    /// <summary>Intervals at or below this are spun rather than slept.</summary>
    public static double SpinThreshold { get; set; } = 20;
}