namespace EdgeCases.Models;

public enum Priority
{
    Low,
    Medium,
    High,
}

public class Task
{
    public string Title { get; }
    public Priority Priority { get; }

    public Task(string title, Priority priority)
    {
        this.Title = title;
        this.Priority = priority;
    }

    public bool IsHighPriority()
    {
        return this.Priority == Priority.High;
    }

    public string GetLabel()
    {
        return $"{this.Title} [{this.Priority}]";
    }
}
