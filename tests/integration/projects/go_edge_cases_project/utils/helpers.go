// --- Static Analysis Ground Truth ---
//
// Package: utils
// File:    utils/helpers.go
//
// Defined entities:
//   Functions (7): Add, Clamp, FormatLabel, CreateMultiplier,
//                  Compose, IsNonNil, Identity
//   Types (1):     HandlerFunc (function type alias)
//
// Expected references (lowercased):
//   utils.helpers.add
//   utils.helpers.clamp
//   utils.helpers.formatlabel
//   utils.helpers.createmultiplier
//   utils.helpers.compose
//   utils.helpers.isnonnilhandlerfunc
//   utils.helpers.identity
//   utils.helpers.handlerfunc
//
// Expected call edges:
//   (none within this file — all leaf functions)
//
// Corner cases:
//   - Function type alias (HandlerFunc)
//   - Higher-order function (CreateMultiplier returns a closure)
//   - Variadic function (Compose takes ...HandlerFunc)
//   - Named return values (Clamp)
//   - Nil check with interface{}
// ---

package utils

import "fmt"

// HandlerFunc is a function type alias for int→int handlers.
type HandlerFunc func(int) int

// Add returns the sum of a and b.
func Add(a, b int) int {
	return a + b
}

// Clamp restricts value to [min, max] using named returns.
func Clamp(value, min, max int) (result int) {
	if value < min {
		result = min
	} else if value > max {
		result = max
	} else {
		result = value
	}
	return
}

// FormatLabel formats a label string, defaulting to "unknown".
func FormatLabel(name string) string {
	if name == "" {
		return fmt.Sprintf("[%s]", "unknown")
	}
	return fmt.Sprintf("[%s]", name)
}

// CreateMultiplier is a higher-order function returning a closure.
func CreateMultiplier(factor int) HandlerFunc {
	return func(x int) int {
		return x * factor
	}
}

// Compose chains multiple HandlerFuncs left-to-right (variadic).
func Compose(fns ...HandlerFunc) HandlerFunc {
	return func(x int) int {
		result := x
		for _, fn := range fns {
			result = fn(result)
		}
		return result
	}
}

// IsNonNil checks if an interface value is non-nil (empty interface param).
func IsNonNil(value interface{}) bool {
	return value != nil
}

// Identity returns its argument unchanged (uses any — Go 1.18+ alias for interface{}).
func Identity(value any) any {
	return value
}
