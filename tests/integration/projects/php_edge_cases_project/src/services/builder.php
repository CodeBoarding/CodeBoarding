<?php
// --- Static Analysis Ground Truth ---
//
// File: src/services/builder.php
//
// Defined entities:
//   Class (1):     QueryBuilder
//   Methods (4):   where, orderBy, limit, build
//   Functions (1): buildQuery
//
// Expected references (lowercased):
//   src.services.builder.querybuilder
//   src.services.builder.where
//   src.services.builder.orderby
//   src.services.builder.limit
//   src.services.builder.build
//   src.services.builder.buildquery
//
// Expected call edges:
//   buildQuery → QueryBuilder (new QueryBuilder)
//   buildQuery → where
//   buildQuery → orderBy
//   buildQuery → limit
//   buildQuery → build
//
// Corner cases:
//   - Method chaining (fluent API) — each method returns static
//   - Builder pattern
//   - Standalone namespace function + class in same file
//   - Return type static (PHP 8.0+)
// ---

namespace App\Services;

class QueryBuilder {
    private array $conditions = [];
    private string $order = '';
    private int $max = 0;

    public function where(string $condition): static {
        $this->conditions[] = $condition;
        return $this;
    }

    public function orderBy(string $field): static {
        $this->order = $field;
        return $this;
    }

    public function limit(int $n): static {
        $this->max = $n;
        return $this;
    }

    public function build(): string {
        $sql = 'SELECT *';
        if (!empty($this->conditions)) {
            $sql .= ' WHERE ' . implode(' AND ', $this->conditions);
        }
        if ($this->order !== '') {
            $sql .= ' ORDER BY ' . $this->order;
        }
        if ($this->max > 0) {
            $sql .= ' LIMIT ' . $this->max;
        }
        return $sql;
    }
}

function buildQuery(): string {
    return (new QueryBuilder())
        ->where("status = 'active'")
        ->orderBy('priority')
        ->limit(10)
        ->build();
}
