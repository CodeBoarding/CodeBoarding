// --- Static Analysis Expected ---
//
// Defined entities:
//   Functions (15): double, isHighPriority, handleActive, handlePending,
//                   dispatch, processTask, processTaskChain, summarizeTasks,
//                   applyMultiplier, computeNested, safeGetLabel,
//                   createTask, getTaskInfo, describeTask,
//                   filterEntities, setupEventProcessing
//   Constants (2): taskHandlers, DEFAULT_HANDLER
//
// Expected call edges:
//   double → add
//   computeNested → add, clamp
//   applyMultiplier → createMultiplier
//   createTask → Task
//   processTask → getLabel (from entities)
//   safeGetLabel → getLabel (from entities)
//   describeTask → getLabel (from entities)
//   setupEventProcessing → on, emit (from EventEmitter)
//   DEFAULT_HANDLER → add
//
// Corner cases:
//   - Dict-based dispatch (taskHandlers object)
//   - Arrow functions as handlers
//   - map/filter with function references
//   - Optional chaining (?.)
//   - Destructuring parameters
//   - Closure (applyMultiplier calls createMultiplier)
//   - Cross-module calls (utils.helpers, models.entities)
// ---

import { add, clamp, createMultiplier } from "../utils_24/helpers.js";
import { Task, getLabel } from "../models_24/entities.js";
import { Priority, Status } from "../utils_24/constants.js";

/**
 * @param {number} x
 * @returns {number}
 */
export function double(x) {
    return add(x, x);
}

/**
 * @param {Task} task
 * @returns {boolean}
 */
export function isHighPriority(task) {
    return task.priority === Priority.HIGH;
}

/** @param {Task} task */
function handleActive(task) {
    return `Active: ${task.name}`;
}

/** @param {Task} task */
function handlePending(task) {
    return `Pending: ${task.name}`;
}

/** @type {Record<string, (task: Task) => string>} */
export const taskHandlers = {
    [Status.ACTIVE]: handleActive,
    [Status.PENDING]: handlePending,
};

/**
 * Dict-based dispatch.
 * @param {Task} task
 * @returns {string}
 */
export function dispatch(task) {
    const handler = taskHandlers[task.status];
    return handler ? handler(task) : `Unknown: ${task.name}`;
}

/**
 * @param {Task} task
 * @returns {string}
 */
export function processTask(task) {
    const label = getLabel(task);
    return `Processed: ${label}`;
}

/**
 * Process chain: map + filter.
 * @param {Task[]} tasks
 * @returns {string[]}
 */
export function processTaskChain(tasks) {
    return tasks.filter(isHighPriority).map((t) => processTask(t));
}

/**
 * @param {Task[]} tasks
 * @returns {{ total: number, highPriority: number }}
 */
export function summarizeTasks(tasks) {
    return {
        total: tasks.length,
        highPriority: tasks.filter(isHighPriority).length,
    };
}

/**
 * Closure: calls createMultiplier from utils.
 * @param {number} factor
 * @param {number} value
 * @returns {number}
 */
export function applyMultiplier(factor, value) {
    const fn = createMultiplier(factor);
    return fn(value);
}

/**
 * Nested calls to utility functions.
 * @param {number} a
 * @param {number} b
 * @returns {number}
 */
export function computeNested(a, b) {
    return clamp(add(a, b), 0, 100);
}

/**
 * Safe label access via optional chaining.
 * @param {Task | null} task
 * @returns {string}
 */
export function safeGetLabel(task) {
    return task ? getLabel(task) : "none";
}

/**
 * Factory function creating a Task.
 * @param {string} name
 * @returns {Task}
 */
export function createTask(name) {
    return new Task(name, name);
}

/**
 * Destructuring parameter.
 * @param {{ name: string, priority: number }} task
 * @returns {string}
 */
export function getTaskInfo({ name, priority }) {
    return `${name}:${priority}`;
}

/**
 * @param {Task} task
 * @returns {string}
 */
export function describeTask(task) {
    return `Task ${getLabel(task)} [${task.priority}]`;
}

/**
 * Module-level constant that references a helper.
 * @type {(x: number) => number}
 */
export const DEFAULT_HANDLER = (x) => add(x, 1);

/**
 * @template T
 * @param {T[]} items
 * @param {(item: T) => boolean} predicate
 * @returns {T[]}
 */
export function filterEntities(items, predicate) {
    return items.filter(predicate);
}

/**
 * Uses EventEmitter methods on/emit.
 * @param {import("../models_24/entities.js").EventEmitter} emitter
 */
export function setupEventProcessing(emitter) {
    emitter.on("task", (data) => {
        console.log("task event:", data);
    });
    emitter.emit("task", { action: "created" });
}
