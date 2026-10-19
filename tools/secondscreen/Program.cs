using System.Diagnostics;
using System.Globalization;
using System.IO.Ports;

namespace SecondScreen;

internal static class Program
{
    // The Deck's panel is 1280x800 on the LCD and OLED models; anything wider gets
    // letterboxed by Fit, so these are ceilings rather than a target to hit.
    private const int DeckWidth = 1280;
    private const int DeckHeight = 800;

    private static int Main(string[] args)
    {
        try
        {
            // Use a high resolution waitable timer for pacing. The default system timer
            // ticks at about 15.6 ms, which no amount of correct arithmetic can
            // hit a 60 fps rate through.
            Native.PrepareTimer();
            try
            {
                return Run(args);
            }
            finally
            {
                Native.ReleaseTimer();
            }
        }
        catch (ArgumentException e)
        {
            Console.Error.WriteLine($"second-screen: {e.Message}");
            Console.Error.WriteLine();
            Console.Error.WriteLine(Usage);
            return 2;
        }
    }

    private const string Usage = """
        SecondScreen - send a window or display to a Steam Deck over USB

        usage:
          SecondScreen --port COM5 [--fps 60] [--quality 60] [--window 0x1234]
                        [--screen 0] [--cursor] [--width N --height N]
          SecondScreen --out frames.bin [--count 60] ...     write frames to a file instead
          SecondScreen --benchmark [--fps 60] ...            measure the pipeline, send nothing
          SecondScreen --list-screens
          SecondScreen --selftest                             check the wire format vector
        """;

    private static int Run(string[] args)
    {
        var options = Options.Parse(args);
        if (options.SelfTest) return Frame.SelfTest();
        if (options.ListScreens) return ListScreens();

        if (options.OutPath is null && options.Port is null && !options.Benchmark)
            throw new ArgumentException("need --port COMx, --out <file> or --benchmark");

        var flags = 0;
        if (options.Window != IntPtr.Zero) flags |= Frame.FlagWindow;
        if (options.Cursor) flags |= Frame.FlagCursor;

        using var capturer = new Capturer(options.SourceWidth, options.SourceHeight,
                                         options.Screen, options.Window, options.Cursor,
                                         options.Quality, options.ScaleMode, options.RegionX, options.RegionY);
        // In "off" mode the capture goes out at its own size and the Deck scales it.
        // libjpeg can halve an image in the DCT domain while decoding, which is
        // nearly free, so this trades host CPU and USB bandwidth for Deck CPU that
        // barely moves.
        var (width, height) = options.ScaleMode == Capturer.ScaleMode.None
            ? (options.SourceWidth, options.SourceHeight)
            : Capturer.Fit(options.SourceWidth, options.SourceHeight, options.Width, options.Height);
        capturer.SetTarget(width, height);

        if (options.Benchmark) return Benchmark.Run(capturer, options.Count == 0 ? 120 : options.Count,
                                                   options.Fps, options.Quality);

        Console.Error.WriteLine($"second-screen: pacing with the {Native.TimerKind}");
        Console.Error.WriteLine($"second-screen: sending {options.SourceWidth}x{options.SourceHeight} as {width}x{height} at {options.Fps} fps, quality {options.Quality}");

        Stream? sink = options.OutPath is null ? null : new FileStream(options.OutPath, FileMode.Create, FileAccess.Write);
        SerialPort? port = null;
        var dropped = 0;
        try
        {
            if (options.Port is not null)
            {
                port = new SerialPort(options.Port, options.Baud)
                {
                    // The link is a byte pipe, not a line protocol: no framing and no
                    // flow control of its own. Backpressure is handled below by
                    // dropping frames, because a stall here would stall the capture.
                    Handshake = Handshake.None,
                    DtrEnable = false,
                    RtsEnable = false,
                    WriteTimeout = 2000,
                };
                port.Open();
            }

            var period = Stopwatch.StartNew();
            var sequence = 0u;

            void Emit(byte[] jpeg)
            {
                var flagsNow = flags;
                if (dropped > 0)
                {
                    flagsNow |= Frame.FlagLossy;
                    dropped = 0;
                }
                var packet = Frame.Pack((ushort)width, (ushort)height, jpeg, flagsNow, sequence);
                if (sequence == 0)
                {
                    // Report the real rate rather than one predicted from a constant:
                    // how many bytes a JPEG takes depends on how busy the picture is,
                    // so an estimate is wrong by several times either way.
                    if (options.Fps > 0)
                    {
                        var perSecond = packet.Length * (double)options.Fps / (1024.0 * 1024.0);
                        Console.Error.WriteLine($"second-screen: {packet.Length / 1024.0:F1} KiB per frame, about {perSecond:F2} MiB/s on the wire");
                    }
                    else
                    {
                        Console.Error.WriteLine($"second-screen: {packet.Length / 1024.0:F1} KiB per frame, unthrottled");
                    }
                }
                if (sink is not null)
                {
                    sink.Write(packet, 0, packet.Length);
                    // Deliberately not flushed per frame: this is a file, and the OS
                    // buffer is exactly what buffering is for. The COM port path does
                    // have to push each frame out, which is the real cost of the link.
                }
                else
                {
                    port!.Write(packet, 0, packet.Length);
                }
                sequence++;
                if (sequence % (options.Fps * 4) == 0)
                {
                    var achieved = sequence * 1000.0 / period.ElapsedMilliseconds;
                    Console.Error.WriteLine($"second-screen: {sequence} frames, {achieved:F1} fps achieved"
                        + (dropped > 0 ? $", {dropped} dropped for backlog" : ""));
                }
            }

            if (options.Serial)
            {
                RunSerial(capturer, options, Emit);
                return 0;
            }

            var pipeline = new Pipeline(capturer, options.Fps, Emit, options.Count);
            pipeline.Run();
            Console.Error.WriteLine($"second-screen: stopped after {pipeline.Sent} sent, {pipeline.Dropped} dropped");
            return 0;
        }
        finally
        {
            port?.Dispose();
            sink?.Dispose();
        }
    }

    /// <summary>
    /// One thread: capture, encode, send, sleep.
    /// </summary>
    /// <remarks>
    /// This is the default, and being able to justify that took some measuring.
    /// Overlapping the capture and the encode is the obvious thing to do and on
    /// paper it wins: serial is 2.7 ms of work per frame against a 16.7 ms budget
    /// at 60 fps, so there is nothing to gain. Worse, splitting the work across two
    /// threads costs far more than it saves, because Windows gives a thread about
    /// 15.6 ms of quantum: both workers finish in milliseconds and then wait to be
    /// scheduled again, which measured 19.6 ms a frame against 2.7 ms for the same
    /// work in one thread. The pipelined path stays for the cases where it does
    /// help -- a slow capture, or a link that blocks on write.
    /// </remarks>
    private static void RunSerial(Capturer capturer, Options options, Action<byte[]> emit)
    {
        var period = Stopwatch.StartNew();
        double cap = 0, enc = 0, send = 0, slept = 0;
        for (var i = 0; i < options.Count; i++)
        {
            var t0 = period.Elapsed.TotalMilliseconds;
            var ok = capturer.Capture(0);
            var t1 = period.Elapsed.TotalMilliseconds;
            if (ok) emit(capturer.Encode(0));
            var t2 = period.Elapsed.TotalMilliseconds;
            cap += t1 - t0;
            enc += t2 - t1;
            if (options.Fps > 0) Native.PreciseSleep(1000.0 / options.Fps - (t2 - t0));
            slept += period.Elapsed.TotalMilliseconds - t2;
        }
        Console.Error.WriteLine($"second-screen: serial over {options.Count}:"
            + $" capture {cap / options.Count:F2} ms,"
            + $" encode {enc / options.Count:F2} ms,"
            + $" sleep {slept / options.Count:F2} ms,"
            + $" total {(period.Elapsed.TotalMilliseconds / options.Count):F2} ms/frame");
    }

    private static int ListScreens()
    {
        var all = Native.VirtualScreen(-1);
        Console.WriteLine($"virtual desktop: {all.Width}x{all.Height} at {all.Left},{all.Top}");
        Console.WriteLine($"primary        : {Native.VirtualScreen(0).Width}x{Native.VirtualScreen(0).Height}");
        Console.Error.WriteLine("enumerate individual outputs with System.Windows.Forms.Screen.AllScreens if you need them");
        return 0;
    }

    private sealed class Options
    {
        public string? Port;
        public string? OutPath;
        public int Fps = 60;
        public int Quality = 60;
        public int Width = DeckWidth;
        public int Height = DeckHeight;
        public int Screen;
        public int Count;
        public bool Cursor;
        public bool SelfTest;
        public bool ListScreens;
        public bool Benchmark;
        public bool Serial;
        public bool UseRegion;
        public int RegionX, RegionY, RegionWidth, RegionHeight;
        public IntPtr Window;
        public int SourceWidth;
        public int SourceHeight;
        public int Baud = 2_000_000;
        public Capturer.ScaleMode ScaleMode = Capturer.ScaleMode.Fast;
        public int MaxBacklog = 512 * 1024;

        public static Options Parse(string[] args)
        {
            var options = new Options();
            for (var i = 0; i < args.Length; i++)
            {
                string Next()
                {
                    if (i + 1 >= args.Length) throw new ArgumentException($"{args[i]} needs a value");
                    return args[++i];
                }

                switch (args[i])
                {
                    case "--port": options.Port = Next(); break;
                    case "--out": options.OutPath = Next(); break;
                    case "--fps": options.Fps = int.Parse(Next(), CultureInfo.InvariantCulture); break;
                    case "--quality": options.Quality = int.Parse(Next(), CultureInfo.InvariantCulture); break;
                    case "--width": options.Width = int.Parse(Next(), CultureInfo.InvariantCulture); break;
                    case "--height": options.Height = int.Parse(Next(), CultureInfo.InvariantCulture); break;
                    case "--screen": options.Screen = int.Parse(Next(), CultureInfo.InvariantCulture); break;
                    case "--count": options.Count = int.Parse(Next(), CultureInfo.InvariantCulture); break;
                    case "--baud": options.Baud = int.Parse(Next(), CultureInfo.InvariantCulture); break;
                    case "--window": options.Window = new IntPtr(long.Parse(Next(), NumberStyles.HexNumber)); break;
                    case "--scale": options.ScaleMode = Next().ToLowerInvariant() switch
                    {
                        "off" or "none" => Capturer.ScaleMode.None,
                        "high" => Capturer.ScaleMode.High,
                        "fast" => Capturer.ScaleMode.Fast,
                        var other => throw new ArgumentException($"--scale must be off, fast or high, not {other}"),
                    }; break;
                    case "--region":
                    {
                        var parts = Next().Split(',');
                        if (parts.Length != 4) throw new ArgumentException("--region wants X,Y,W,H");
                        options.RegionX = int.Parse(parts[0], CultureInfo.InvariantCulture);
                        options.RegionY = int.Parse(parts[1], CultureInfo.InvariantCulture);
                        options.RegionWidth = int.Parse(parts[2], CultureInfo.InvariantCulture);
                        options.RegionHeight = int.Parse(parts[3], CultureInfo.InvariantCulture);
                        options.UseRegion = true;
                        break;
                    }
                    case "--cursor": options.Cursor = true; break;
                    case "--benchmark": options.Benchmark = true; break;
                    case "--serial": options.Serial = true; break;
                    case "--selftest": options.SelfTest = true; break;
                    case "--list-screens": options.ListScreens = true; break;
                    default: throw new ArgumentException($"unknown option {args[i]}");
                }
            }

            if (options.SelfTest || options.ListScreens) return options;

            if (options.Fps != 0 && (options.Fps < 1 || options.Fps > 240))
                    throw new ArgumentException("--fps must be 0 (unthrottled) or 1..240");
            if (options.Quality is < 10 or > 95) throw new ArgumentException("--quality must be 10..95");

            if (options.Window != IntPtr.Zero)
            {
                if (!Native.GetWindowRect(options.Window, out var rect))
                    throw new ArgumentException($"--window {options.Window:X} is not a window");
                options.SourceWidth = rect.Width;
                options.SourceHeight = rect.Height;
            }
            else
            {
                var rect = Native.VirtualScreen(options.Screen);
                options.SourceWidth = rect.Width;
                options.SourceHeight = rect.Height;
                options.RegionX = rect.Left;
                options.RegionY = rect.Top;
            }
            if (options.UseRegion)
            {
                options.SourceWidth = options.RegionWidth;
                options.SourceHeight = options.RegionHeight;
            }
            if (options.SourceWidth <= 0 || options.SourceHeight <= 0)
                throw new ArgumentException("source capture size is empty");
            return options;
        }
    }
}