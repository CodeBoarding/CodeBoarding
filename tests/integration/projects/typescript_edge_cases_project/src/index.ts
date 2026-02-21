// --- Static Analysis Expected ---
//
// Defined entities:
//   Functions (1): main
//
// Expected call edges (from main() body — 25+ calls total):
//   Constructors:     Task(), UrgentTask(), Event(), Repository(), EventEmitter()
//   Services:         createTask(), dispatch(), buildQuery(), processTask(),
//                     processTaskChain(), summarizeTasks(), applyMultiplier(),
//                     computeNested(), double(), isHighPriority(), safeGetLabel(),
//                     getTaskInfo(), describeTask(), filterEntities(),
//                     setupEventProcessing()
//   Utils:            createMultiplier(), formatLabel(), compose(),
//                     identity(), isNonNull()
//   Method calls:     repo.add(), repo.get(), validator.addRule(),
//                     validator.validate(), event.dispose(), task1.serialize(),
//                     urgent.serialize()
//   Closure call:     doubler() — calling return value of createMultiplier()
//
// Corner cases: cross-module calls spanning src.models, src.services, src.utils;
//   calling a closure stored in a local variable; constructor calls;
//   generic class instantiation; class expression instantiation (new Validator());
//   array .filter with type guard; promise .then callback
// Package: src | imports: src.models, src.services, src.utils
// ---

import { Task, UrgentTask, Event, Repository, Validator, EventEmitter } from "./models";
import {
    Priority,
    Status,
    add,
    createMultiplier,
    formatLabel,
    compose,
    isNonNull,
    identity,
} from "./utils";
import {
    QueryBuilder,
    buildQuery,
    double,
    isHighPriority,
    dispatch,
    processTask,
    processTaskChain,
    summarizeTasks,
    applyMultiplier,
    computeNested,
    safeGetLabel,
    createTask,
    getTaskInfo,
    describeTask,
    filterEntities,
    setupEventProcessing,
} from "./services";

export function main(): void {
    // Constructors
    const task1 = createTask("1", "Fix bug", Priority.High);
    const task2 = new Task("2", "Add feature", Priority.Medium);
    const urgent = new UrgentTask("3", "Hotfix", new Date("2025-12-31"));
    const event = new Event("e1", "deployment");

    // Repository usage (generic class)
    const repo = new Repository<Task>();
    repo.add(task1);
    repo.add(task2);
    const found = repo.get("1");

    // Validator (class expression usage)
    const validator = new Validator();
    validator.addRule((s) => s.length > 0);
    validator.validate("test");

    // EventEmitter (generic class)
    const emitter = new EventEmitter<string>();
    setupEventProcessing(emitter);

    // Array method chain
    const tasks = [task1, task2, urgent];
    const summary = summarizeTasks(tasks);

    // Dict dispatch
    const label = dispatch(task1);

    // Builder pattern
    const query = buildQuery(["status=active", "priority>=2"], "name");

    // Async usage
    processTask(task1).then(r => console.log(r));
    processTaskChain(task2).then(r => console.log(r));

    // Higher-order / closure
    const doubler = createMultiplier(2);
    const doubled = doubler(21);
    const values = applyMultiplier([1, 2, 3], 3);

    // Nested calls
    const nested = computeNested(10, 20);

    // Arrow functions
    const d = double(5);
    const isHigh = isHighPriority(task1);

    // Optional chaining
    const safeLabel = safeGetLabel(task1);
    const nullLabel = safeGetLabel(null);

    // Destructuring
    const { type, label: taskLabel } = getTaskInfo(task1);

    // Template literal with call
    const desc = describeTask(urgent);

    // Compose (HOF)
    const addTen = compose((x: number) => x + 5, (x: number) => x + 5);
    const result = addTen(10);

    // Generic function
    const filtered = filterEntities(tasks, isHighPriority);

    // Formatter (cross-module utility)
    const formatted = formatLabel("test", Priority.High);

    // Identity (generic)
    const same = identity(42);

    // isNonNull type guard
    const items = [1, null, 3, undefined, 5].filter(isNonNull);

    // Event disposal
    event.dispose();

    // Serialization
    const serialized = task1.serialize();
    const urgentSerialized = urgent.serialize();

    console.log(summary, label, query, doubled, values, nested, d, desc, formatted, same, items, serialized);
}

main();
