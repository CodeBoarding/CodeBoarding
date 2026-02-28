// --- Static Analysis Ground Truth ---
//
// Package: services
// File:    services/builder.go
//
// Defined entities:
//   Structs (1):   QueryBuilder
//   Methods (4):   Where, OrderBy, Limit, Build (all pointer receivers)
//   Functions (1): BuildQuery
//
// Expected references (lowercased):
//   services.builder.querybuilder
//   services.builder.where
//   services.builder.orderby
//   services.builder.limit
//   services.builder.build
//   services.builder.buildquery
//
// Expected call edges:
//   BuildQuery → Where
//   BuildQuery → OrderBy
//   BuildQuery → Limit
//   BuildQuery → Build
//
// Corner cases:
//   - Method chaining via pointer receivers returning *QueryBuilder
//   - Builder pattern (fluent API)
//   - strings.Builder usage (stdlib)
// ---

package services

import (
	"fmt"
	"strings"
)

// QueryBuilder demonstrates method chaining with pointer receivers.
type QueryBuilder struct {
	conditions []string
	order      string
	max        int
}

// Where adds a condition and returns self for chaining.
func (qb *QueryBuilder) Where(condition string) *QueryBuilder {
	qb.conditions = append(qb.conditions, condition)
	return qb
}

// OrderBy sets the order field and returns self.
func (qb *QueryBuilder) OrderBy(field string) *QueryBuilder {
	qb.order = field
	return qb
}

// Limit sets the max results and returns self.
func (qb *QueryBuilder) Limit(n int) *QueryBuilder {
	qb.max = n
	return qb
}

// Build produces the final query string.
func (qb *QueryBuilder) Build() string {
	var sb strings.Builder
	sb.WriteString("SELECT *")
	if len(qb.conditions) > 0 {
		sb.WriteString(" WHERE ")
		sb.WriteString(strings.Join(qb.conditions, " AND "))
	}
	if qb.order != "" {
		sb.WriteString(fmt.Sprintf(" ORDER BY %s", qb.order))
	}
	if qb.max > 0 {
		sb.WriteString(fmt.Sprintf(" LIMIT %d", qb.max))
	}
	return sb.String()
}

// BuildQuery is a convenience function demonstrating method chaining.
func BuildQuery() string {
	return (&QueryBuilder{}).
		Where("status = 'active'").
		OrderBy("priority").
		Limit(10).
		Build()
}
