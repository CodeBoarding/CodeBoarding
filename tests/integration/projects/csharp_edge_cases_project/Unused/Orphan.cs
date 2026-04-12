namespace EdgeCases.Unused;

public class Orphan
{
    public int Value { get; }

    public Orphan(int value)
    {
        this.Value = value;
    }

    public string OrphanMethod()
    {
        return "never called";
    }
}
