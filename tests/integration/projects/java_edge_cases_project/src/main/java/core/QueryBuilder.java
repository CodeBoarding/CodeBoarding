package core;

// --- Static Analysis Expected ---
//
// Defined entities:
//   Class (1): QueryBuilder
//   Methods (5): from(String), where(String), orderBy(String), limit(int), build()
//
// Edge cases covered:
//   - Builder pattern with method chaining (each method returns this)
//   - Fluent API style
//   - Terminal operation (build) producing result
//
// Expected call edges (from Main or Services via chaining):
//   Caller → from(String)
//   Caller → where(String)
//   Caller → orderBy(String)
//   Caller → limit(int)
//   Caller → build()
//
// ---

public class QueryBuilder {

    private String table;
    private String whereClause;
    private String orderBy;
    private int limit;

    public QueryBuilder from(String table) {
        this.table = table;
        return this;
    }

    public QueryBuilder where(String clause) {
        this.whereClause = clause;
        return this;
    }

    public QueryBuilder orderBy(String column) {
        this.orderBy = column;
        return this;
    }

    public QueryBuilder limit(int n) {
        this.limit = n;
        return this;
    }

    public String build() {
        StringBuilder sb = new StringBuilder("SELECT * FROM " + table);
        if (whereClause != null) sb.append(" WHERE ").append(whereClause);
        if (orderBy != null) sb.append(" ORDER BY ").append(orderBy);
        if (limit > 0) sb.append(" LIMIT ").append(limit);
        return sb.toString();
    }
}
