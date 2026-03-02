// --- Static Analysis Ground Truth ---
//
// Package: models
// File:    models/base.go
//
// Defined entities:
//   Interfaces (3): Speaker, Swimmer, Disposable
//   Structs (1):    Entity
//   Methods (3):    Entity.GetType (value receiver),
//                   Entity.String (value receiver),
//                   Entity.SetType (pointer receiver)
//   Variables (1):  entityCount (unexported package-level)
//
// Expected references (lowercased):
//   models.base.speaker
//   models.base.swimmer
//   models.base.disposable
//   models.base.entity
//   models.base.gettype          (or models.base.(*entity).gettype — depends on gopls)
//   models.base.string           (or models.base.(*entity).string)
//   models.base.settype
//   models.base.entitycount
//
// Expected call edges:
//   String → GetType   (e.GetType() inside String())
//
// Corner cases:
//   - Interface definitions (implicit satisfaction — no "implements")
//   - Interface embedding (Swimmer embeds Speaker conceptually via separate check)
//   - Pointer vs value receivers on the same struct
//   - Unexported package-level variable (entityCount)
//   - Stringer interface (String() method)
//   - Struct tags
// ---

package models

import "fmt"

// Speaker is an interface for things that speak.
type Speaker interface {
	Speak() string
	Name() string
}

// Swimmer is an interface for things that swim.
type Swimmer interface {
	Swim() string
}

// Disposable represents something that can be cleaned up.
type Disposable interface {
	Dispose()
	IsDisposed() bool
}

var entityCount int

// Entity is the base struct with common fields. Uses struct tags.
type Entity struct {
	ID       string `json:"id"`
	typeName string // unexported field
}

// GetType returns the entity type (value receiver).
func (e Entity) GetType() string {
	return e.typeName
}

// String implements the Stringer interface. Calls GetType().
func (e Entity) String() string {
	return fmt.Sprintf("%s:%s", e.GetType(), e.ID)
}

// SetType sets the type name (pointer receiver).
func (e *Entity) SetType(t string) {
	e.typeName = t
	entityCount++
}
