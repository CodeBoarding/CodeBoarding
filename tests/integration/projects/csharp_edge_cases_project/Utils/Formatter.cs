namespace EdgeCases.Utils;

public static class Formatter
{
    public static string FormatLabel(string label)
    {
        return $"[{label.ToUpperInvariant()}]";
    }

    public static int Clamp(int value, int min, int max)
    {
        if (value < min)
        {
            return min;
        }
        if (value > max)
        {
            return max;
        }
        return value;
    }
}
