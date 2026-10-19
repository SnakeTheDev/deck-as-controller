using System.Diagnostics;

namespace SecondScreen;

internal static class Benchmark
{
    /// <summary>
    /// Measure the pipeline stage by stage, so it is obvious which part is the
    /// ceiling rather than guessing.
    /// </summary>
    /// <remarks>
    /// Reports the median and the worst frame, not the mean. A mean hides exactly
    /// what matters here: one 40 ms frame in ten is the difference between holding
    /// 60 fps and dropping to 25, and it disappears completely in the average.
    /// </remarks>
    public static int Run(Capturer capturer, int count, int fps, int quality)
    {
        // Warm up: the first frame pays for JIT, GDI+ handle creation and the
        // codec being touched for the first time, and none of that is steady state.
        for (var i = 0; i < 3; i++) _ = capturer.Grab();

        var captures = new long[count];
        var scales = new long[count];
        var encodes = new long[count];
        var sizes = new int[count];
        var wall = new long[count];

        for (var i = 0; i < count; i++)
        {
            var clock = Stopwatch.StartNew();
            var jpeg = capturer.Grab();
            wall[i] = clock.ElapsedTicks * 1000 / Stopwatch.Frequency;
            captures[i] = capturer.CaptureMs;
            scales[i] = capturer.ScaleMs;
            encodes[i] = capturer.EncodeMs;
            sizes[i] = jpeg.Length;
        }

        Console.WriteLine($"{capturer.SourceWidth}x{capturer.SourceHeight} -> "
                          + $"{capturer.TargetWidth}x{capturer.TargetHeight}, quality {quality}, {count} frames");
        Console.WriteLine();
        Console.WriteLine($"  stage      median     p95     worst");
        foreach (var (name, values) in new (string, long[])[]
                 { ("capture", captures), ("scale", scales), ("encode", encodes), ("total", wall) })
        {
            Console.WriteLine($"  {name,-9} {Median(values),6:F1} {Percentile(values, 0.95),6:F1} {values.Max(),7:F1}  ms");
        }

        var averageSize = sizes.Average();
        var budget = 1000.0 / fps;
        var medianTotal = Median(wall);
        var worstTotal = wall.Max();

        Console.WriteLine();
        Console.WriteLine($"  payload    {averageSize / 1024.0:F1} KiB/frame, "
                          + $"{averageSize * fps / (1024.0 * 1024.0):F2} MiB/s at {fps} fps");
        Console.WriteLine($"  budget     {budget:F1} ms/frame at {fps} fps");
        Console.WriteLine($"  verdict    {(worstTotal <= budget ? "HOLDS" : "MISSES")} "
                          + $"- worst frame {worstTotal:F1} ms is {worstTotal / budget:F2}x the budget");

        if (worstTotal > budget)
        {
            var fastest = wall.OrderBy(v => v).ElementAt(count / 10);
            Console.WriteLine($"  at {1000.0 / fastest:F0} fps worst case ({fastest:F1} ms/frame)");
        }
        return 0;
    }

    private static double Median(long[] values)
    {
        var sorted = values.OrderBy(v => v).ToArray();
        return sorted.Length % 2 == 1
            ? sorted[sorted.Length / 2]
            : (sorted[sorted.Length / 2 - 1] + sorted[sorted.Length / 2]) / 2.0;
    }

    private static double Percentile(long[] values, double fraction)
    {
        var sorted = values.OrderBy(v => v).ToArray();
        var index = (int)Math.Clamp(fraction * (sorted.Length - 1), 0, sorted.Length - 1);
        return sorted[index];
    }
}