namespace SecondScreen;

/// <summary>
/// The C# half of the frame format implemented in <c>py_modules/sdcd/frame.py</c>.
/// </summary>
/// <remarks>
/// These two must agree byte for byte; <c>tests/test_frame.py</c> pins the same
/// vector that <see cref="SelfTest"/> checks here, so drift in either one is caught.
/// Everything is explicitly little endian and every integer is written by hand,
/// because the struct layout of a C# struct is exactly the sort of thing that
/// silently disagrees with a Python one on a different runtime.
/// </remarks>
internal static class Frame
{
    public static readonly byte[] Magic = { (byte)'S', (byte)'D', (byte)'2' };
    public const byte Version = 1;
    public const int HeaderSize = 21;

    public const int FlagWindow = 1 << 0;
    public const int FlagCursor = 1 << 1;
    public const int FlagLossy = 1 << 2;

    /// <summary>Build one framed packet for the wire.</summary>
    public static byte[] Pack(ushort width, ushort height, byte[] payload, int flags = 0, uint sequence = 0)
    {
        ArgumentNullException.ThrowIfNull(payload);
        if (payload.Length == 0) throw new ArgumentException("refusing to send an empty frame", nameof(payload));

        var packet = new byte[HeaderSize + payload.Length];
        Magic.CopyTo(packet, 0);
        packet[3] = Version;
        packet[4] = (byte)flags;
        WriteUInt16(packet, 5, width);
        WriteUInt16(packet, 7, height);
        WriteUInt32(packet, 9, (uint)payload.Length);
        WriteUInt32(packet, 13, Crc32(payload));
        WriteUInt32(packet, 17, sequence);
        payload.CopyTo(packet, HeaderSize);
        return packet;
    }

    private static void WriteUInt16(byte[] buffer, int offset, ushort value)
    {
        buffer[offset] = (byte)value;
        buffer[offset + 1] = (byte)(value >> 8);
    }

    private static void WriteUInt32(byte[] buffer, int offset, uint value)
    {
        buffer[offset] = (byte)value;
        buffer[offset + 1] = (byte)(value >> 8);
        buffer[offset + 2] = (byte)(value >> 16);
        buffer[offset + 3] = (byte)(value >> 24);
    }

    // CRC-32 (IEEE 802.3, the same polynomial zlib and binascii.crc32 use). The BCL
    // has System.IO.Hashing but not in the net8.0 baseline, and System.IO.Compression's
    // CRC in .NET 9 is a different polynomial, so keep our own and keep it simple.
    private static readonly uint[] CrcTable = BuildCrcTable();

    private static uint[] BuildCrcTable()
    {
        var table = new uint[256];
        for (uint i = 0; i < 256; i++)
        {
            var value = i;
            for (int bit = 0; bit < 8; bit++)
                value = (value & 1) != 0 ? 0xedb88320 ^ (value >> 1) : value >> 1;
            table[i] = value;
        }
        return table;
    }

    public static uint Crc32(byte[] data)
    {
        var crc = 0xffffffffu;
        foreach (var b in data)
            crc = CrcTable[(crc ^ b) & 0xff] ^ (crc >> 8);
        return crc ^ 0xffffffffu;
    }

    /// <summary>
    /// Print the reference vector so it can be compared with the Python one.
    /// </summary>
    public static int SelfTest()
    {
        var vector = Pack(800, 600, "abc"u8.ToArray(), sequence: 1);
        Console.WriteLine(Convert.ToHexString(vector).ToLowerInvariant());
        return 0;
    }
}