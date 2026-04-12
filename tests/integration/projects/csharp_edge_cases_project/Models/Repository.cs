using System.Collections.Generic;

namespace EdgeCases.Models;

public class Repository<T>
{
    private readonly List<T> items;

    public Repository()
    {
        this.items = new List<T>();
    }

    public void Add(T item)
    {
        this.items.Add(item);
    }

    public int Count()
    {
        return this.items.Count;
    }

    public IEnumerable<T> GetAll()
    {
        return this.items;
    }
}
