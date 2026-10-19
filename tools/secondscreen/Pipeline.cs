using System.Diagnostics;

namespace SecondScreen;

/// <summary>
/// Overlap the capture with the encode, so a frame costs the slower of the two
/// rather than their sum.
/// </summary>
/// <remarks>
/// Measured serially on a 2048x1152 capture going to 1280x720: capture 15 ms,
/// scale 5 ms, encode 1 ms -- 21 ms a frame and 47 fps, which misses 60. The two
/// halves do not depend on each other, so putting them on two threads makes a
/// frame cost about the capture alone and 60 fps falls out.
///
/// Backpressure is deliberate rather than an accident. There are only two slots;
/// if the consumer has not drained one, the producer takes it anyway and that
/// frame is lost. A queue would instead let latency grow without bound, and a
/// second screen showing a map a second late is worse than one that skips.
/// </remarks>
internal sealed class Pipeline
{
    // Slot states. The distinction between CAPTURING and CONSUMING is not
    // decoration: with only "empty" and "full" the producer would start writing a
    // new frame into a buffer the consumer was still reading, which is both torn
    // output and a fight over GDI+'s locks that pushed a frame from 16.7 ms to
    // 18.7 ms and lost 60 fps.
    private const int Empty = -1;
    private const int Capturing = 0;
    private const int Full = 1;
    private const int Consuming = 2;

    private readonly Capturer _capturer;
    private readonly Action<byte[]> _emit;
    private readonly int _fps;
    private readonly Stopwatch _period = Stopwatch.StartNew();

    private readonly int[] _state = new int[Capturer.Slots];
    private readonly object _lock = new();

    private int _captureSlot;
    private int _consumeSlot;
    private volatile bool _stopped;
    private volatile int _limit;

    // Where the time goes, in microseconds. Worth keeping: the difference between
    // the achieved rate and the requested one lived in here for a while.
    private long _captureTicks, _sleepTicks, _waitTicks, _encodeTicks;
    private int _produced, _consumed;

    private static double Ms(long ticks) => ticks * 1000.0 / Stopwatch.Frequency;

    public Pipeline(Capturer capturer, int fps, Action<byte[]> emit, int limit = 0)
    {
        _capturer = capturer;
        _fps = fps;
        _emit = emit;
        _limit = limit;
        for (var i = 0; i < _state.Length; i++) _state[i] = Empty;
    }

    public int Dropped { get; private set; }

    public int Sent => _consumed;

    /// <summary>Capture on this thread, encode on another, until stopped.</summary>
    public void Run()
    {
        var consumer = new Thread(Consume) { Name = "second-screen-encode", IsBackground = true };
        consumer.Start();
        try
        {
            Produce();
        }
        finally
        {
            _stopped = true;
            lock (_lock) Monitor.PulseAll(_lock);
            consumer.Join(TimeSpan.FromSeconds(2));
            Report();
        }
    }

    private void Produce()
    {
        // The producer sets the rate. Leaving it unpaced looks harmless and is not:
        // it captures flat out, burns a core and takes GDI's process-wide locks away
        // from the encoder doing real work, which inflates the consumer's own encode
        // time until the two stop overlapping at all.
        while (!_stopped)
        {
            int slot;
            var stole = false;
            lock (_lock)
            {
                // Prefer a slot the consumer has finished with. Steal a full one
                // only when there is nothing free, which is exactly the case where
                // the consumer is behind and dropping is the right answer.
                slot = IndexOf(Empty);
                if (slot < 0)
                {
                    slot = IndexOf(Full);
                    stole = slot >= 0;
                }
                // With one producer and two slots at least one is always available.
                if (slot < 0) slot = _captureSlot;
                if (stole) Dropped++;
                _state[slot] = Capturing;
                _captureSlot = (slot + 1) % Capturer.Slots;
            }

            var start = _period.Elapsed.TotalMilliseconds;
            _capturer.Capture(slot);
            Interlocked.Add(ref _captureTicks, (long)(_capturer.CaptureMs * 1000));
            Interlocked.Increment(ref _produced);

            lock (_lock)
            {
                _state[slot] = Full;
                Monitor.PulseAll(_lock);
            }

            if (_fps > 0)
            {
                var sleep = 1000.0 / _fps - (_period.Elapsed.TotalMilliseconds - start);
                Native.PreciseSleep(sleep);
                Interlocked.Add(ref _sleepTicks,
                    (long)(_period.Elapsed.TotalMilliseconds - start - sleep - _capturer.CaptureMs) * 1000);
            }
        }
    }

    private void Consume()
    {
        while (true)
        {
            int slot;
            var waitStart = _period.Elapsed.TotalMilliseconds;
            lock (_lock)
            {
                slot = IndexOf(Full);
                while (slot < 0)
                {
                    if (_stopped) return;
                    Monitor.Wait(_lock);
                    slot = IndexOf(Full);
                }
                _state[slot] = Consuming;
            }

            // Outside the lock, and while the slot is marked Consuming so the
            // producer knows not to touch it.
            var encodeStart = _period.Elapsed.TotalMilliseconds;
            var jpeg = _capturer.Encode(slot);
            _emit(jpeg);

            lock (_lock)
            {
                _state[slot] = Empty;
                Monitor.PulseAll(_lock); // wake the producer, it may have been stealing
            }
            _consumeSlot = slot;
            Interlocked.Increment(ref _consumed);
            Interlocked.Add(ref _waitTicks, (long)((encodeStart - waitStart) * 1000));
            Interlocked.Add(ref _encodeTicks, (long)((_period.Elapsed.TotalMilliseconds - encodeStart) * 1000));

            if (_limit > 0 && _consumed >= _limit)
            {
                // Tell the producer to stop too. Without this it captures forever,
                // Run() never returns from Produce(), and the process hangs.
                _stopped = true;
                lock (_lock) Monitor.PulseAll(_lock);
                return;
            }
        }
    }

    private int IndexOf(int wanted)
    {
        for (var i = 0; i < _state.Length; i++)
            if (_state[i] == wanted) return i;
        return -1;
    }

    private void Report()
    {
        if (_produced == 0) return;
        Console.Error.WriteLine($"second-screen: produced {_produced}, sent {_consumed}, dropped {Dropped}"
            + $" | capture {Ms(Interlocked.Read(ref _captureTicks)) / _produced:F2} ms,"
            + $" sleep {Ms(Interlocked.Read(ref _sleepTicks)) / _produced:F2} ms,"
            + $" encode+send {Ms(Interlocked.Read(ref _encodeTicks)) / Math.Max(1, _consumed):F2} ms");
    }
}