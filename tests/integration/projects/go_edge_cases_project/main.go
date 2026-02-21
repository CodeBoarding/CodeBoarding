// --- Static Analysis Ground Truth ---
//
// Package: main
// File:    main.go
//
// Defined entities:
//   Functions (1): main
//
// Expected references (lowercased):
//   main.main
//
// Expected call edges (from main):
//   main → models.NewDog
//   main → models.NewCat
//   main → models.NewDuck
//   main → models.NewTask
//   main → models.NewRepository
//   main → models.NewEventEmitter
//   main → services.CreateTask
//   main → services.Dispatch
//   main → services.BuildQuery
//   main → services.ProcessTask
//   main → services.ProcessTaskChain
//   main → services.SummarizeTasks
//   main → services.ApplyMultiplier
//   main → services.ComputeNested
//   main → services.Double
//   main → services.IsHighPriority
//   main → services.SafeGetLabel
//   main → services.GetTaskInfo
//   main → services.DescribeTask
//   main → services.SetupEventProcessing
//   main → utils.CreateMultiplier
//   main → utils.Compose
//   main → utils.FormatLabel
//   main → utils.Identity
//   main → utils.Add
//   main → Dog.Speak  (interface method call)
//   main → Cat.Speak
//   main → Duck.Speak
//   main → Duck.Swim
//   main → Repository.Add
//   main → Repository.Count
//   main → Task.Serialize
//   main → Task.IsOverdue
//   main → Task.Dispose
//   main → Task.IsDisposed
//
// Corner cases:
//   - Interface variable (Speaker) calling concrete method
//   - Blank identifier discard (_ = ...)
//   - Multiple return value unpacking
//   - Cross-package calls to models, services, utils
//   - Calling methods on embedded struct (Task.GetType via Entity)
// ---

package main

import (
	"fmt"

	"example.com/edgecases/models"
	"example.com/edgecases/services"
	"example.com/edgecases/utils"
)

func main() {
	// --- Models: constructors and interface satisfaction ---
	dog := models.NewDog("1", "Rex", "Labrador")
	cat := models.NewCat("2", "Whiskers", true)
	duck := models.NewDuck("3")

	// Interface variable — concrete method resolved at runtime
	var speaker models.Speaker = dog
	fmt.Println(speaker.Speak())

	fmt.Println(cat.Speak())
	fmt.Println(duck.Speak())
	fmt.Println(duck.Swim())

	// --- Models: Task with embedding ---
	t1 := services.CreateTask("alpha")
	t2 := models.NewTask("2", "beta", utils.PriorityMedium, utils.StatusActive)
	t3 := models.NewTask("3", "gamma", utils.PriorityHigh, utils.StatusActive)

	fmt.Println(t2.Serialize())
	fmt.Println(t3.IsOverdue())
	t1.Dispose()
	fmt.Println(t1.IsDisposed())

	// Calling embedded Entity method through Task
	fmt.Println(t1.GetType())

	// --- Repository ---
	repo := models.NewRepository()
	repo.Add(t1.ID, t1)
	repo.Add(t2.ID, t2)
	fmt.Println("Count:", repo.Count())

	// --- EventEmitter ---
	emitter := models.NewEventEmitter()
	services.SetupEventProcessing(emitter)

	// --- Services ---
	services.Dispatch(t1)
	q := services.BuildQuery()
	services.ProcessTask(t1)
	services.ProcessTaskChain([]*models.Task{t1, t2, t3})
	services.SummarizeTasks([]*models.Task{t1, t2, t3})
	services.ApplyMultiplier(3, 7)
	services.ComputeNested(10, 20)
	services.Double(5)
	services.IsHighPriority(t3)
	services.SafeGetLabel(t1)
	services.DescribeTask(t3)

	// Multiple return values
	name, prio := services.GetTaskInfo(t2)
	fmt.Println(name, prio)

	// --- Utils ---
	triple := utils.CreateMultiplier(3)
	pipeline := utils.Compose(func(x int) int { return x + 1 }, triple)
	utils.FormatLabel("test")
	utils.Identity(42)
	fmt.Println(utils.Add(1, 2))

	fmt.Println(q, pipeline(5))
}
