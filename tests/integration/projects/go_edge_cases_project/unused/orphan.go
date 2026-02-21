// --- Static Analysis Ground Truth ---
//
// Package: unused
// File:    unused/orphan.go
//
// Defined entities:
//   Structs (1):   OrphanClass
//   Methods (1):   OrphanMethod (value receiver)
//   Functions (1): NeverCalled
//   Constants (1): UnusedConstant
//
// Expected references (lowercased):
//   unused.orphan.orphanclass
//   unused.orphan.orphanmethod
//   unused.orphan.nevercalled
//   unused.orphan.unusedconstant
//
// This package is never imported — exercises dead-code detection.
// No expected call edges.
// ---

package unused

// OrphanClass is a struct no one uses.
type OrphanClass struct {
	Value string
}

// OrphanMethod is a method no one calls.
func (o OrphanClass) OrphanMethod() string {
	return "orphan"
}

// NeverCalled is a function no one calls.
func NeverCalled() int {
	return 42
}

// UnusedConstant is a constant no one references.
const UnusedConstant = "dead"
