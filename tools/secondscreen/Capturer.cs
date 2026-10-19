using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;

namespace SecondScreen;

/// <summary>
/// Grabs the host's display, a window or a region of one, scales it for the Deck's
/// panel and encodes it as JPEG.
/// </summary>
/// <remarks>
/// GDI's <c>CopyFromScreen</c> rather than DXGI Desktop Duplication. Desktop
/// Duplication is the better capture -- it sees overlays and hardware protected
/// video that GDI cannot -- but it needs a long-lived duplication handle per
/// output and a good deal of P/Invoke. For the intended use, which is showing a
/// map or a guide next to a windowed game, GDI gets there with none of that.
///
/// This class allocates almost nothing per frame. The first cut looked harmless and
/// cost more than the capture did: it looked the JPEG encoder up with LINQ over
/// every codec the system has, built a fresh <see cref="EncoderParameters"/>, and
/// allocated three <see cref="Bitmap"/>s per frame. At 15 fps that is invisible. At
/// 60 it is most of the budget. Everything reusable is now built once.
/// </remarks>
internal sealed class Capturer : IDisposable
{
    /// <summary>How to get from the capture size to the send size.</summary>
    /// <remarks>
    /// This turned out to matter more than anything else. Measured on a 2048x1152
    /// capture going to 1280x720, a high quality bicubic resize cost 35 ms of a
    /// 52 ms frame while the JPEG encode cost 1 ms. GDI+ does that in software,
    /// one pixel at a time. Plain bilinear costs 5 ms and on a 1.6:1 downscale
    /// there is nothing to see in the difference.
    /// </remarks>
    public enum ScaleMode
    {
        /// <summary>Downscale here, as fast as GDI+ will go. The default.</summary>
        Fast,
        /// <summary>Downscale here, nicest. Roughly four times the cost of Fast.</summary>
        High,
        /// <summary>Send the capture at its own size; the Deck scales while decoding.</summary>
        None,
    }

    /// <summary>
    /// Two capture buffers, so a capture can start while the previous frame is
    /// still being encoded. See <see cref="Pipeline"/>.
    /// </summary>
    public const int Slots = 2;

    private static readonly ImageCodecInfo Jpeg =
        ImageCodecInfo.GetImageEncoders().First(e => e.FormatID == ImageFormat.Jpeg.Guid);

    private readonly int _screen;
    private readonly IntPtr _window;
    private readonly bool _cursor;
    private readonly Point _origin;
    private readonly EncoderParameters _quality;
    private readonly ScaleMode _scaleMode;

    private readonly Bitmap[] _source = new Bitmap[Slots];
    private readonly Graphics[] _sourceGraphics = new Graphics[Slots];
    private Bitmap _scaled;
    private Graphics _scaledGraphics;
    private readonly MemoryStream _buffer;

    public Capturer(int width, int height, int screen, IntPtr window, bool cursor, int quality,
                     ScaleMode scaleMode = ScaleMode.Fast, int originX = 0, int originY = 0)
    {
        SourceWidth = width;
        SourceHeight = height;
        _screen = screen;
        _window = window;
        _cursor = cursor;
        _scaleMode = scaleMode;
        _origin = new Point(originX, originY);
        _quality = new EncoderParameters(1);
        _quality.Param[0] = new EncoderParameter(System.Drawing.Imaging.Encoder.Quality, (long)quality);

        for (var i = 0; i < Slots; i++)
        {
            _source[i] = new Bitmap(width, height, PixelFormat.Format32bppArgb);
            _sourceGraphics[i] = Graphics.FromImage(_source[i]);
        }
        _buffer = new MemoryStream(1 << 18);
        SetTarget(width, height);
    }

    public int SourceWidth { get; }

    public int SourceHeight { get; }

    public bool IsWindow => _window != IntPtr.Zero;

    public long CaptureMs { get; private set; }

    public long ScaleMs { get; private set; }

    public long EncodeMs { get; private set; }

    public int TargetWidth { get; private set; }

    public int TargetHeight { get; private set; }

    /// <summary>Resize the encode target. Call once before the loop, not per frame.</summary>
    public void SetTarget(int fitWidth, int fitHeight)
    {
        if (fitWidth == TargetWidth && fitHeight == TargetHeight) return;
        TargetWidth = fitWidth;
        TargetHeight = fitHeight;
        // The destination has to match the size the host asked for, so rebuild it.
        // This happens once at start-up, never in the steady state. The first call
        // comes from the constructor, before either field exists.
        _scaledGraphics?.Dispose();
        _scaled?.Dispose();
        _scaled = new Bitmap(fitWidth, fitHeight, PixelFormat.Format32bppArgb);
        _scaledGraphics = Graphics.FromImage(_scaled);
        if (_scaleMode == ScaleMode.High)
        {
            _scaledGraphics.InterpolationMode = InterpolationMode.HighQualityBicubic;
            _scaledGraphics.PixelOffsetMode = PixelOffsetMode.HighQuality;
            _scaledGraphics.CompositingQuality = CompositingQuality.HighQuality;
        }
        else
        {
            _scaledGraphics.InterpolationMode = InterpolationMode.Bilinear;
            _scaledGraphics.PixelOffsetMode = PixelOffsetMode.HighSpeed;
            _scaledGraphics.CompositingQuality = CompositingQuality.HighSpeed;
        }
        _scaledGraphics.SmoothingMode = SmoothingMode.HighSpeed;
    }

    /// <summary>Capture into one slot. Returns false if the source went away.</summary>
    public bool Capture(int slot)
    {
        var clock = System.Diagnostics.Stopwatch.StartNew();
        try
        {
            var graphics = _sourceGraphics[slot];
            if (_window != IntPtr.Zero)
            {
                // PW_RENDERFULLCONTENT reaches windows that are occluded, which
                // CopyFromScreen cannot.
                var hdc = graphics.GetHdc();
                try
                {
                    if (!Native.PrintWindow(_window, hdc, 2))
                        throw new InvalidOperationException($"PrintWindow failed for window 0x{_window:X}");
                }
                finally
                {
                    graphics.ReleaseHdc(hdc);
                }
            }
            else
            {
                graphics.CopyFromScreen(_origin, Point.Empty, _source[slot].Size);
            }
            if (!_cursor) Native.DrawNoCursor();
            return true;
        }
        catch (Exception e) when (e is System.Runtime.InteropServices.ExternalException or InvalidOperationException)
        {
            // A window closing, or a display changing mode, should not take the
            // whole sender down.
            Console.Error.WriteLine($"second-screen: capture failed ({e.Message}); retrying");
            return false;
        }
        finally
        {
            CaptureMs = clock.ElapsedMilliseconds;
        }
    }

    /// <summary>Scale and encode a captured slot. The slow half of a frame.</summary>
    public byte[] Encode(int slot)
    {
        var clock = System.Diagnostics.Stopwatch.StartNew();
        _scaledGraphics.DrawImage(_source[slot], new Rectangle(0, 0, TargetWidth, TargetHeight));
        var split = clock.ElapsedMilliseconds;
        _buffer.SetLength(0);
        _scaled.Save(_buffer, Jpeg, _quality);
        ScaleMs = split;
        EncodeMs = clock.ElapsedMilliseconds - split;
        return _buffer.GetBuffer().AsSpan(0, (int)_buffer.Length).ToArray();
    }

    /// <summary>Capture and encode in one go. Only for the benchmark and for windows.</summary>
    public byte[] Grab()
    {
        if (!Capture(0))
            throw new InvalidOperationException("capture failed");
        return Encode(0);
    }

    /// <summary>Largest box with the source's aspect ratio that fits the target.</summary>
    public static (int Width, int Height) Fit(int sourceWidth, int sourceHeight, int targetWidth, int targetHeight)
    {
        var scale = Math.Min((double)targetWidth / sourceWidth, (double)targetHeight / sourceHeight);
        // Round down, and never below 2px: encoders reject a zero-sized dimension and
        // a 1px one produces a frame too small to be worth sending.
        var width = Math.Max(2, (int)Math.Floor(sourceWidth * scale));
        var height = Math.Max(2, (int)Math.Floor(sourceHeight * scale));
        return (width, height);
    }

    public void Dispose()
    {
        _scaledGraphics.Dispose();
        _scaled.Dispose();
        foreach (var graphics in _sourceGraphics) graphics.Dispose();
        foreach (var bitmap in _source) bitmap.Dispose();
        _buffer.Dispose();
        _quality.Dispose();
    }
}