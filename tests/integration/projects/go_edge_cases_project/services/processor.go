// --- Static Analysis Ground Truth ---
//
// Package: services
// File:    services/processor.go
//
// Defined entities:
//   Functions (13): Double, IsHighPriority, Dispatch,
//                   ProcessTask, ProcessTaskChain, SummarizeTasks,
//                   ApplyMultiplier, ComputeNested, SafeGetLabel,
//                   CreateTask, GetTaskInfo, DescribeTask,
//                   SetupEventProcessing
//   Variables (2):  taskHandlers (map dispatch), DefaultHandler (func var)
//   Types (1):      TaskSummary (struct for SummarizeTasks return)
//   Init (1):       init() — registers a default handler in taskHandlers
//
// Expected references (lowercased):
//   services.processor.double
//   services.processor.ishighpriority
//   services.processor.dispatch
//   services.processor.processtask
//   services.processor.processtaskchain
//   services.processor.summarizetasks
//   services.processor.applymultiplier
//   services.processor.computenested
//   services.processor.safegetlabel
//   services.processor.createtask
//   services.processor.gettaskinfo
//   services.processor.describetask
//   services.processor.setupeventprocessing
//   services.processor.taskhandlers
//   services.processor.defaulthandler
//   services.processor.tasksummary
//   services.processor.init
//
// Expected call edges:
//   Double          → utils.Add
//   ComputeNested   → utils.Add, utils.Clamp
//   ApplyMultiplier → utils.CreateMultiplier
//   CreateTask      → models.NewTask
//   ProcessTask     → Task.GetLabel (cross-package)
//   SafeGetLabel    → Task.GetLabel
//   DescribeTask    → Task.GetLabel
//   SetupEventProcessing → EventEmitter.On, EventEmitter.Emit
//   DefaultHandler  → utils.Add
//
// Corner cases:
//   - init() function (auto-runs, multiple per file allowed)
//   - Map-based dispatch (taskHandlers)
//   - First-class functions (passing func as value)
//   - Closure capturing outer variable (ApplyMultiplier)
//   - Defer statement (DescribeTask uses defer)
//   - Multiple return values (GetTaskInfo)
//   - Cross-package calls to utils and models
//   - Package-level var with function value (DefaultHandler)
// ---

package services

import (
	"fmt"

	"example.com/edgecases/models"
	"example.com/edgecases/utils"
)

// TaskSummary holds aggregated task information.
type TaskSummary struct {
	Total        int
	HighPriority int
}

// taskHandlers is a map-based dispatch table.
var taskHandlers = map[utils.Status]func(*models.Task) string{
	utils.StatusActive:  func(t *models.Task) string { return "Active: " + t.TaskName },
	utils.StatusPending: func(t *models.Task) string { return "Pending: " + t.TaskName },
}

// DefaultHandler is a package-level function variable calling utils.Add.
var DefaultHandler = func(x int) int {
	return utils.Add(x, 1)
}

// init registers a fallback handler. Exercises Go's init() pattern.
func init() {
	taskHandlers[utils.StatusDone] = func(t *models.Task) string {
		return "Done: " + t.TaskName
	}
}

// Double calls utils.Add (cross-package edge).
func Double(x int) int {
	return utils.Add(x, x)
}

// IsHighPriority checks if a task has high priority.
func IsHighPriority(t *models.Task) bool {
	return t.Priority == utils.PriorityHigh
}

// Dispatch uses map-based dispatch (dict lookup pattern).
func Dispatch(t *models.Task) string {
	handler, ok := taskHandlers[t.Status]
	if !ok {
		return "Unknown: " + t.TaskName
	}
	return handler(t)
}

// ProcessTask calls Task.GetLabel (cross-package method call).
func ProcessTask(t *models.Task) string {
	label := t.GetLabel()
	return "Processed: " + label
}

// ProcessTaskChain filters and maps over tasks (first-class func usage).
func ProcessTaskChain(tasks []*models.Task) []string {
	var results []string
	for _, t := range tasks {
		if IsHighPriority(t) {
			results = append(results, ProcessTask(t))
		}
	}
	return results
}

// SummarizeTasks returns a struct with multiple fields.
func SummarizeTasks(tasks []*models.Task) TaskSummary {
	summary := TaskSummary{Total: len(tasks)}
	for _, t := range tasks {
		if IsHighPriority(t) {
			summary.HighPriority++
		}
	}
	return summary
}

// ApplyMultiplier uses a closure from utils.CreateMultiplier.
func ApplyMultiplier(factor, value int) int {
	fn := utils.CreateMultiplier(factor)
	return fn(value)
}

// ComputeNested calls utils.Add and utils.Clamp (nested cross-pkg calls).
func ComputeNested(a, b int) int {
	return utils.Clamp(utils.Add(a, b), 0, 100)
}

// SafeGetLabel safely gets a label, handling nil task.
func SafeGetLabel(t *models.Task) string {
	if t == nil {
		return "none"
	}
	return t.GetLabel()
}

// CreateTask is a factory function calling models.NewTask.
func CreateTask(name string) *models.Task {
	return models.NewTask(name, name, utils.PriorityMedium, utils.StatusActive)
}

// GetTaskInfo demonstrates multiple return values.
func GetTaskInfo(t *models.Task) (string, int) {
	return t.TaskName, int(t.Priority)
}

// DescribeTask uses defer and calls Task.GetLabel.
func DescribeTask(t *models.Task) string {
	defer fmt.Println("described task")
	return fmt.Sprintf("Task %s [%d]", t.GetLabel(), t.Priority)
}

// SetupEventProcessing registers and fires events (cross-package).
func SetupEventProcessing(emitter *models.EventEmitter) {
	emitter.On("task", func(data interface{}) {
		fmt.Println("task event:", data)
	})
	emitter.Emit("task", map[string]string{"action": "created"})
}
