// --- Static Analysis Expected ---
//
// Defined entities:
//   Classes (1): QueryBuilder
//   Methods (4): where, orderBy, limit, build
//   Functions (1): buildQuery
//
// Expected call edges:
//   buildQuery → QueryBuilder (new)
//   buildQuery → where
//   buildQuery → orderBy
//   buildQuery → limit
//   buildQuery → build
//
// Corner cases:
//   - Method chaining (fluent API / builder pattern)
//   - Private field with #
//   - Returning `this` for chaining
// ---

export class QueryBuilder {
    /** @type {string[]} */
    #conditions = [];

    /** @type {string | null} */
    #order = null;

    /** @type {number | null} */
    #max = null;

    /**
     * @param {string} condition
     * @returns {QueryBuilder}
     */
    where(condition) {
        this.#conditions.push(condition);
        return this;
    }

    /**
     * @param {string} field
     * @returns {QueryBuilder}
     */
    orderBy(field) {
        this.#order = field;
        return this;
    }

    /**
     * @param {number} n
     * @returns {QueryBuilder}
     */
    limit(n) {
        this.#max = n;
        return this;
    }

    /** @returns {string} */
    build() {
        let q = "SELECT *";
        if (this.#conditions.length) {
            q += ` WHERE ${this.#conditions.join(" AND ")}`;
        }
        if (this.#order) {
            q += ` ORDER BY ${this.#order}`;
        }
        if (this.#max != null) {
            q += ` LIMIT ${this.#max}`;
        }
        return q;
    }
}

/**
 * Convenience function demonstrating method chaining.
 * @returns {string}
 */
export function buildQuery() {
    return new QueryBuilder().where("status = 'active'").orderBy("priority").limit(10).build();
}
