// --- Static Analysis Expected ---
//
// Defined entities:
//   Classes (6): Task, UrgentTask, Event, Repository, Validator, EventEmitter
//   Functions (1): getLabel (standalone, uses formatLabel)
//
// Methods on Task:     constructor, getLabel, getType, serialize, isOverdue, dispose, isDisposed, title (getter)
// Methods on UrgentTask: constructor
// Methods on Event:    constructor
// Methods on Repository: constructor, add, get, getAll, findBy, count
// Methods on Validator:  constructor, addRule, validate
// Methods on EventEmitter: constructor, on, emit, off
//
// Expected call edges:
//   Task constructor → Entity constructor (super())
//   UrgentTask constructor → Task constructor (super())
//   getLabel → formatLabel (from utils)
//   findBy → getAll
//
// Corner cases:
//   - Single inheritance (Task extends Entity)
//   - Two-level inheritance (UrgentTask extends Task extends Entity)
//   - #private fields
//   - Getter (title)
//   - Generic-like Repository via JSDoc
//   - Map-based storage
//   - Callback-based EventEmitter
//   - Spread in arrays
//   - Optional chaining (?.)
// ---

import { Entity } from "./base.js";
import { formatLabel } from "../utils_20/helpers.js";
import { Priority, Status, DEFAULT_LABEL } from "../utils_20/constants.js";

export class Task extends Entity {
    /** @type {number} */
    priority;

    /** @type {string} */
    status;

    /** @type {boolean} */
    #disposed = false;

    /**
     * @param {string} id
     * @param {string} name
     * @param {number} [priority]
     * @param {string} [status]
     */
    constructor(id, name, priority = Priority.MEDIUM, status = Status.ACTIVE) {
        super(id, "Task");
        this.name = name;
        this.priority = priority;
        this.status = status;
    }

    /** @returns {string} */
    get title() {
        return `${this.name} (${this.status})`;
    }

    /** @returns {string} */
    getLabel() {
        return formatLabel(this.name);
    }

    /** @returns {string} */
    getType() {
        return "Task";
    }

    /** @returns {object} */
    serialize() {
        return { id: this.id, name: this.name, priority: this.priority, status: this.status };
    }

    /** @returns {boolean} */
    isOverdue() {
        return this.status === Status.ACTIVE && this.priority === Priority.HIGH;
    }

    dispose() {
        this.#disposed = true;
    }

    /** @returns {boolean} */
    isDisposed() {
        return this.#disposed;
    }
}

export class UrgentTask extends Task {
    /**
     * @param {string} id
     * @param {string} name
     */
    constructor(id, name) {
        super(id, name, Priority.HIGH, Status.ACTIVE);
    }
}

export class Event {
    /** @type {string} */
    name;

    /** @type {Date} */
    date;

    /**
     * @param {string} name
     * @param {Date} date
     */
    constructor(name, date) {
        this.name = name;
        this.date = date;
    }
}

/**
 * Generic-like repository using JSDoc.
 * @template T
 */
export class Repository {
    /** @type {Map<string, T>} */
    #items = new Map();

    add(id, item) {
        this.#items.set(id, item);
    }

    get(id) {
        return this.#items.get(id);
    }

    getAll() {
        return [...this.#items.values()];
    }

    /**
     * @param {(item: T) => boolean} predicate
     * @returns {T[]}
     */
    findBy(predicate) {
        return this.getAll().filter(predicate);
    }

    /** @returns {number} */
    count() {
        return this.#items.size;
    }
}

export class Validator {
    /** @type {Array<(value: any) => boolean>} */
    #rules = [];

    /**
     * @param {(value: any) => boolean} rule
     */
    addRule(rule) {
        this.#rules.push(rule);
    }

    /**
     * @param {*} value
     * @returns {boolean}
     */
    validate(value) {
        return this.#rules.every((rule) => rule(value));
    }
}

export class EventEmitter {
    /** @type {Map<string, Function[]>} */
    #listeners = new Map();

    /**
     * @param {string} event
     * @param {Function} callback
     */
    on(event, callback) {
        if (!this.#listeners.has(event)) {
            this.#listeners.set(event, []);
        }
        this.#listeners.get(event)?.push(callback);
    }

    /**
     * @param {string} event
     * @param {*} data
     */
    emit(event, data) {
        const handlers = this.#listeners.get(event) ?? [];
        handlers.forEach((h) => h(data));
    }

    /**
     * @param {string} event
     * @param {Function} callback
     */
    off(event, callback) {
        const handlers = this.#listeners.get(event);
        if (handlers) {
            this.#listeners.set(
                event,
                handlers.filter((h) => h !== callback),
            );
        }
    }
}

/**
 * Standalone function that calls a method on Task.
 * @param {Task} task
 * @returns {string}
 */
export function getLabel(task) {
    return formatLabel(task?.name ?? DEFAULT_LABEL);
}
