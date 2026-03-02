// --- Static Analysis Ground Truth ---
//
// Package: utils
// File:    utils/constants.go
//
// Defined entities:
//   Named types (2): Priority, Status
//   Constants (7):    PriorityLow, PriorityMedium, PriorityHigh,
//                     StatusActive, StatusPending, StatusDone,
//                     MaxRetries
//   Variables (2):    DefaultTimeout, DefaultLabel
//
// Expected references (lowercased):
//   utils.constants.priority
//   utils.constants.status
//   utils.constants.prioritylow
//   utils.constants.prioritymedium
//   utils.constants.priorityhigh
//   utils.constants.statusactive
//   utils.constants.statuspending
//   utils.constants.statusdone
//   utils.constants.maxretries
//   utils.constants.defaulttimeout
//   utils.constants.defaultlabel
//
// Corner cases:
//   - iota enumeration
//   - Named types (Priority is an int, Status is a string)
//   - Package-level variables
// ---

package utils

// Priority represents task priority using iota.
type Priority int

const (
	PriorityLow    Priority = iota // 0
	PriorityMedium                 // 1
	PriorityHigh                   // 2
)

// Status represents task status as a named string type.
type Status string

const (
	StatusActive  Status = "active"
	StatusPending Status = "pending"
	StatusDone    Status = "done"
)

const MaxRetries = 3

var DefaultTimeout = 5000
var DefaultLabel = "untitled"
