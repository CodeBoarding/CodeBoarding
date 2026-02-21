// --- Static Analysis Expected ---
//
// Defined entities:
//   Interfaces (1): QueryResult
//   Classes (1): QueryBuilder
//   Methods (5): QueryBuilder.where, QueryBuilder.orderBy, QueryBuilder.limit,
//                QueryBuilder.withPriority, QueryBuilder.build
//   Functions (1): buildQuery
//
// Expected call edges (from function/method bodies):
//   buildQuery → QueryBuilder    (constructor call: new QueryBuilder())
//   buildQuery → where           (builder.where(c))
//   buildQuery → orderBy         (builder.orderBy(sortBy))
//   buildQuery → limit           (builder.limit(50))
//   buildQuery → build           (builder.build())
//
// Class hierarchy:
//   QueryBuilder — standalone, no inheritance (builder pattern)
//
// Corner cases: method chaining / fluent API (each method returns this),
//   factory function constructing and chaining, interface as return type
// Package: src.services | imports: src.utils (for Priority)
// ---

import { Priority } from "../utils";

export interface QueryResult {
    filters: string[];
    sort: string | null;
    limit: number | null;
    priority: Priority | null;
}

export class QueryBuilder {
    private filters: string[] = [];
    private sortKey: string | null = null;
    private limitCount: number | null = null;
    private priorityFilter: Priority | null = null;

    where(condition: string): QueryBuilder {
        this.filters.push(condition);
        return this;
    }

    orderBy(key: string): QueryBuilder {
        this.sortKey = key;
        return this;
    }

    limit(n: number): QueryBuilder {
        this.limitCount = n;
        return this;
    }

    withPriority(priority: Priority): QueryBuilder {
        this.priorityFilter = priority;
        return this;
    }

    build(): QueryResult {
        return {
            filters: this.filters,
            sort: this.sortKey,
            limit: this.limitCount,
            priority: this.priorityFilter,
        };
    }
}

export function buildQuery(conditions: string[], sortBy?: string): QueryResult {
    const builder = new QueryBuilder();
    for (const c of conditions) {
        builder.where(c);
    }
    if (sortBy) {
        builder.orderBy(sortBy);
    }
    return builder.limit(50).build();
}
