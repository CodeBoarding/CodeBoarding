using EdgeCases.Models;
using EdgeCases.Services;
using EdgeCases.Utils;

namespace EdgeCases;

public static class Program
{
    public static void Main(string[] args)
    {
        Dog dog = new Dog("Rex", "Labrador");
        Cat cat = new Cat("Whiskers", indoor: true);

        Animal speaker = dog;
        System.Console.WriteLine(speaker.Speak());
        System.Console.WriteLine(cat.Speak());

        Task task1 = new Task("alpha", Priority.High);
        Task task2 = new Task("beta", Priority.Low);

        TaskService service = new TaskService();
        service.Dispatch(task1);
        service.ProcessTask(task2);
        service.SummarizeTasks(new Task[] { task1, task2 });

        Repository<Task> repo = new Repository<Task>();
        repo.Add(task1);
        repo.Add(task2);
        System.Console.WriteLine(repo.Count());

        QueryBuilder qb = new QueryBuilder();
        string query = qb.Where("status = active").Limit(10).Build();
        System.Console.WriteLine(query);

        System.Console.WriteLine(Formatter.FormatLabel("done"));
        System.Console.WriteLine(Formatter.Clamp(100, 0, 50));
    }
}
