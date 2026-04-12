using System.Collections.Generic;

namespace EdgeCases.Services;

public class QueryBuilder
{
    private readonly List<string> conditions;
    private int max;

    public QueryBuilder()
    {
        this.conditions = new List<string>();
        this.max = 0;
    }

    public QueryBuilder Where(string condition)
    {
        this.conditions.Add(condition);
        return this;
    }

    public QueryBuilder Limit(int value)
    {
        this.max = value;
        return this;
    }

    public string Build()
    {
        string joined = string.Join(" AND ", this.conditions);
        return $"WHERE {joined} LIMIT {this.max}";
    }
}
