// --- Static Analysis Expected ---
//
// Defined entities:
//   Arrow functions (2): double, isHighPriority
//   Object literal (1): taskHandlers (with methods handlePending, handleActive)
//   Functions (12): dispatch, processTask, processTaskChain, summarizeTasks,
//                   applyMultiplier, computeNested, safeGetLabel, createTask,
//                   getTaskInfo, describeTask, filterEntities, setupEventProcessing
//   IIFE result (1): DEFAULT_HANDLER
//
// Expected call edges (from function bodies):
//   double              → add()              (cross-module call to utils)
//   dispatch            → handler()          (dynamic dispatch via object lookup)
//   processTask         → getLabel()         (method call on task)
//   processTaskChain    → serialize()        (method call inside .then())
//   summarizeTasks      → isHighPriority     (passed to .filter())
//   summarizeTasks      → getLabel()         (called inside .map())
//   applyMultiplier     → createMultiplier() (cross-module HOF call)
//   computeNested       → add()              (nested: inner call)
//   computeNested       → clamp()            (nested: outer call)
//   safeGetLabel        → getLabel()         (optional chaining call)
//   createTask          → Task()             (constructor call)
//   describeTask        → getType()          (call inside template literal)
//   describeTask        → getLabel()         (call inside template literal)
//   getTaskInfo         → getType()          (method call on task)
//   getTaskInfo         → getLabel()         (method call on task)
//   setupEventProcessing→ emitter.on()       (method call with callback)
//   setupEventProcessing→ emitter.emit()     (method call)
//
// Corner cases: arrow function as const, object literal methods, dict-based dispatch,
//   async/await, promise chain (.then/.catch), array method chain (.filter/.map/.reduce),
//   higher-order function usage (closure from another module), nested function calls,
//   optional chaining call (?.), default parameter values, template literal with calls,
//   IIFE (immediately invoked function expression), generic function, callback registration
// Package: src.services | imports: src.models, src.utils
// ---

import { Task, UrgentTask, Repository, EventEmitter, Event } from "../models";
import { Priority, Status, MAX_RETRIES } from "../utils";
import { add, clamp, createMultiplier, isNonNull, formatLabel } from "../utils";
import type { Handler } from "../utils";

export const double = (x: number): number => add(x, x);

export const isHighPriority = (task: Task): boolean => {
    return task.priority >= Priority.High;
};

export const taskHandlers = {
    handlePending(task: Task): string {
        return `Pending: ${task.title}`;
    },
    handleActive(task: Task): string {
        return `Active: ${task.title}`;
    },
};

const dispatchMap: Record<string, (task: Task) => string> = {
    [Status.Pending]: (t) => taskHandlers.handlePending(t),
    [Status.Active]: (t) => taskHandlers.handleActive(t),
    [Status.Done]: (t) => `Done: ${t.title}`,
    [Status.Failed]: (t) => `Failed: ${t.title}`,
};

export function dispatch(task: Task): string {
    const handler = dispatchMap[task.status];
    return handler(task);
}

export async function processTask(task: Task): Promise<string> {
    const label = task.getLabel();
    const result = await Promise.resolve(label);
    return result;
}

export function processTaskChain(task: Task): Promise<string> {
    return Promise.resolve(task)
        .then(t => t.serialize())
        .then(json => `processed:${json}`)
        .catch(() => "error");
}

export function summarizeTasks(tasks: Task[]): string {
    const summary = tasks
        .filter(isHighPriority)
        .map(t => t.getLabel())
        .reduce((acc, label) => `${acc}, ${label}`, "");
    return summary;
}

export function applyMultiplier(values: number[], factor: number): number[] {
    const multiplier = createMultiplier(factor);
    return values.map(multiplier);
}

export function computeNested(a: number, b: number): number {
    return clamp(add(a, b), 0, 100);
}

export function safeGetLabel(task: Task | null): string | undefined {
    return task?.getLabel();
}

export function createTask(
    id: string,
    title: string,
    priority: Priority = Priority.Medium,
): Task {
    return new Task(id, title, priority);
}

export function getTaskInfo(task: Task): { type: string; label: string } {
    const type = task.getType();
    const label = task.getLabel();
    return { type, label };
}

export function describeTask(task: Task): string {
    return `Task ${task.getType()}: ${task.getLabel()}`;
}

export const DEFAULT_HANDLER: Handler<number> = ((initial: number) => {
    const base = add(initial, 10);
    return (x: number) => add(x, base);
})(0);

export function filterEntities<T extends { id: string }>(
    items: T[],
    predicate: (item: T) => boolean,
): T[] {
    return items.filter(predicate);
}

export function setupEventProcessing(emitter: EventEmitter<string>): void {
    emitter.on("task", (data: string) => {
        console.log(data);
    });
    emitter.emit("task", "started");
}
