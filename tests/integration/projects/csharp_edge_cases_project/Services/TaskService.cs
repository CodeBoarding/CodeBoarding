using EdgeCases.Models;
using EdgeCases.Utils;

namespace EdgeCases.Services;

public class TaskService
{
    public void Dispatch(Task task)
    {
        string label = task.GetLabel();
        System.Console.WriteLine(Formatter.FormatLabel(label));
    }

    public string ProcessTask(Task task)
    {
        if (task.IsHighPriority())
        {
            return Formatter.FormatLabel(task.GetLabel());
        }
        return task.GetLabel();
    }

    public int SummarizeTasks(Task[] tasks)
    {
        int highCount = 0;
        foreach (Task t in tasks)
        {
            if (t.IsHighPriority())
            {
                highCount++;
            }
        }
        return Formatter.Clamp(highCount, 0, tasks.Length);
    }
}
