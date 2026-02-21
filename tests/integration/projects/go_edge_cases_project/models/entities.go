// --- Static Analysis Ground Truth ---
//
// Package: models
// File:    models/entities.go
//
// Defined entities:
//   Structs (6):    Dog, Cat, Duck, Task, Repository, EventEmitter
//   Functions (6):  NewDog, NewCat, NewDuck, NewTask, NewRepository, NewEventEmitter
//   Methods:
//     Dog:          Speak, Name (value receivers — satisfies Speaker)
//     Cat:          Speak, Name (value receivers — satisfies Speaker)
//     Duck:         Speak, Name, Swim (value receivers — satisfies Speaker+Swimmer)
//     Task:         GetLabel, Serialize, IsOverdue, Dispose, IsDisposed, Title
//     Repository:   Add, Get, GetAll, FindBy, Count
//     EventEmitter: On, Emit, Off
//
// Expected references (lowercased):
//   models.entities.dog
//   models.entities.cat
//   models.entities.duck
//   models.entities.task
//   models.entities.repository
//   models.entities.eventemitter
//   models.entities.newdog
//   models.entities.newcat
//   models.entities.newduck
//   models.entities.newtask
//   models.entities.newrepository
//   models.entities.neweventemitter
//   models.entities.speak  (on Dog, Cat, Duck)
//   models.entities.name   (on Dog, Cat, Duck)
//   models.entities.swim   (on Duck)
//   models.entities.getlabel
//   models.entities.serialize
//   models.entities.isoverdue
//   models.entities.dispose
//   models.entities.isdisposed
//   models.entities.title
//   models.entities.add
//   models.entities.get
//   models.entities.getall
//   models.entities.findby
//   models.entities.count
//   models.entities.on
//   models.entities.emit
//   models.entities.off
//
// Expected call edges:
//   NewDog  → SetType  (cross-file: calls Entity.SetType from base.go)
//   NewCat  → SetType
//   NewDuck → SetType
//   NewTask → SetType
//   GetLabel → FormatLabel (cross-package: calls utils.FormatLabel)
//   FindBy   → GetAll
//
// Corner cases:
//   - Struct embedding (Task embeds Entity — composition, Go's "inheritance")
//   - Dog/Cat/Duck implicitly satisfy Speaker interface (no "implements")
//   - Duck satisfies both Speaker and Swimmer
//   - Constructor functions (NewXxx pattern)
//   - Pointer receivers for mutation, value receivers for reads
//   - Map-based storage in Repository
//   - Callback/listener pattern in EventEmitter
//   - Unexported fields (#disposed equivalent via unexported bool)
//   - Cross-package import of utils.FormatLabel
// ---

package models

import (
	"example.com/edgecases/utils"
)

// --- Dog ---

// Dog is a concrete type satisfying Speaker.
type Dog struct {
	Entity
	breed string
}

func NewDog(id, name, breed string) *Dog {
	d := &Dog{Entity: Entity{ID: id}, breed: breed}
	d.SetType("Dog")
	return d
}

func (d Dog) Speak() string { return "Woof!" }
func (d Dog) Name() string  { return d.ID }

// --- Cat ---

// Cat is a concrete type satisfying Speaker.
type Cat struct {
	Entity
	indoor bool
}

func NewCat(id, name string, indoor bool) *Cat {
	c := &Cat{Entity: Entity{ID: id}, indoor: indoor}
	c.SetType("Cat")
	return c
}

func (c Cat) Speak() string { return "Meow!" }
func (c Cat) Name() string  { return c.ID }

// --- Duck ---

// Duck satisfies both Speaker and Swimmer (multiple interface satisfaction).
type Duck struct {
	Entity
}

func NewDuck(id string) *Duck {
	d := &Duck{Entity: Entity{ID: id}}
	d.SetType("Duck")
	return d
}

func (d Duck) Speak() string { return "Quack!" }
func (d Duck) Name() string  { return d.ID }
func (d Duck) Swim() string  { return "Splash!" }

// --- Task ---

// Task embeds Entity and adds task-specific fields.
type Task struct {
	Entity
	TaskName string
	Priority utils.Priority
	Status   utils.Status
	disposed bool
}

func NewTask(id, name string, priority utils.Priority, status utils.Status) *Task {
	t := &Task{
		Entity:   Entity{ID: id},
		TaskName: name,
		Priority: priority,
		Status:   status,
	}
	t.SetType("Task")
	return t
}

// GetLabel calls utils.FormatLabel (cross-package edge).
func (t Task) GetLabel() string {
	return utils.FormatLabel(t.TaskName)
}

// Serialize returns a map representation.
func (t Task) Serialize() map[string]interface{} {
	return map[string]interface{}{
		"id":       t.ID,
		"name":     t.TaskName,
		"priority": t.Priority,
		"status":   t.Status,
	}
}

// IsOverdue checks if the task is overdue.
func (t Task) IsOverdue() bool {
	return t.Status == utils.StatusActive && t.Priority == utils.PriorityHigh
}

// Dispose marks the task as disposed (pointer receiver for mutation).
func (t *Task) Dispose() {
	t.disposed = true
}

// IsDisposed returns whether the task has been disposed.
func (t Task) IsDisposed() bool {
	return t.disposed
}

// Title returns a formatted title string.
func (t Task) Title() string {
	return t.TaskName + " (" + string(t.Status) + ")"
}

// --- Repository ---

// Repository is a generic-like container using interface{}.
type Repository struct {
	items map[string]interface{}
}

func NewRepository() *Repository {
	return &Repository{items: make(map[string]interface{})}
}

func (r *Repository) Add(id string, item interface{}) {
	r.items[id] = item
}

func (r *Repository) Get(id string) (interface{}, bool) {
	v, ok := r.items[id]
	return v, ok
}

func (r *Repository) GetAll() []interface{} {
	result := make([]interface{}, 0, len(r.items))
	for _, v := range r.items {
		result = append(result, v)
	}
	return result
}

// FindBy calls GetAll internally (intra-file edge).
func (r *Repository) FindBy(predicate func(interface{}) bool) []interface{} {
	var result []interface{}
	for _, item := range r.GetAll() {
		if predicate(item) {
			result = append(result, item)
		}
	}
	return result
}

func (r *Repository) Count() int {
	return len(r.items)
}

// --- EventEmitter ---

// EventEmitter implements an observer pattern with callbacks.
type EventEmitter struct {
	listeners map[string][]func(interface{})
}

func NewEventEmitter() *EventEmitter {
	return &EventEmitter{listeners: make(map[string][]func(interface{}))}
}

func (e *EventEmitter) On(event string, callback func(interface{})) {
	e.listeners[event] = append(e.listeners[event], callback)
}

func (e *EventEmitter) Emit(event string, data interface{}) {
	for _, cb := range e.listeners[event] {
		cb(data)
	}
}

func (e *EventEmitter) Off(event string) {
	delete(e.listeners, event)
}
