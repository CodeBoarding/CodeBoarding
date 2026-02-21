// --- Static Analysis Expected ---
//
// Defined entities:
//   Classes (5): Task, UrgentTask, Event, Repository, EventEmitter
//   Class expression (1): Validator (anonymous class assigned to const)
//   Methods: Task.constructor, Task.getType, Task.serialize, Task.getLabel,
//            Task.title (getter), Task.title (setter),
//            UrgentTask.constructor, UrgentTask.getType, UrgentTask.isOverdue,
//            UrgentTask.serialize,
//            Event.constructor, Event.serialize, Event.dispose, Event.isDisposed,
//            Repository.add, Repository.get, Repository.getAll, Repository.findBy,
//            Repository.count,
//            Validator.addRule, Validator.validate,
//            EventEmitter.on, EventEmitter.emit, EventEmitter.off
//
// Expected call edges (from method bodies):
//   Task.constructor      → super()          (calls Entity constructor)
//   Task.getLabel         → formatLabel()    (cross-module call to utils)
//   UrgentTask.constructor → super()         (calls Task constructor)
//   UrgentTask.isOverdue  → Date()           (constructor call)
//   Repository.getAll     → Array.from()     (stdlib)
//   Repository.findBy     → getAll()         (self.getAll())
//   Repository.findBy     → filter()         (predicate as argument)
//   Validator.validate    → every()          (array method with callback)
//   EventEmitter.emit     → forEach()        (iteration with callback)
//
// Class hierarchy:
//   Task extends Entity implements Serializable
//   UrgentTask extends Task           — deeper chain (UrgentTask → Task → Entity)
//   Event implements Identifiable, Serializable, Disposable — multiple interfaces
//   Repository<T extends Entity>      — generic class with constraint
//   EventEmitter<T>                   — generic class
//   Validator                         — class expression, standalone
//
// Corner cases: private fields (#title), getter/setter, method override,
//   generic class with constraint, class expression (anonymous class),
//   multiple interface implementation, deep inheritance chain,
//   cross-module import of utility function
// Package: src.models | imports: src.utils (for formatLabel, Priority, Status)
// ---

import { Entity, Serializable, Identifiable, Disposable } from "./base";
import { Priority, Status, formatLabel } from "../utils";

export class Task extends Entity implements Serializable {
    #title: string;
    priority: Priority;
    status: Status;

    constructor(id: string, title: string, priority: Priority = Priority.Medium) {
        super(id);
        this.#title = title;
        this.priority = priority;
        this.status = Status.Pending;
    }

    get title(): string {
        return this.#title;
    }

    set title(value: string) {
        this.#title = value;
    }

    getType(): string {
        return "Task";
    }

    serialize(): string {
        return JSON.stringify({ id: this.id, title: this.#title, priority: this.priority });
    }

    getLabel(): string {
        return formatLabel(this.#title, this.priority);
    }
}

export class UrgentTask extends Task {
    readonly deadline: Date;

    constructor(id: string, title: string, deadline: Date) {
        super(id, title, Priority.Critical);
        this.deadline = deadline;
    }

    getType(): string {
        return "UrgentTask";
    }

    isOverdue(): boolean {
        return new Date() > this.deadline;
    }

    serialize(): string {
        return JSON.stringify({
            id: this.id,
            title: this.title,
            deadline: this.deadline.toISOString(),
        });
    }
}

export class Event implements Identifiable, Serializable, Disposable {
    readonly id: string;
    readonly name: string;
    private disposed = false;

    constructor(id: string, name: string) {
        this.id = id;
        this.name = name;
    }

    serialize(): string {
        return JSON.stringify({ id: this.id, name: this.name });
    }

    dispose(): void {
        this.disposed = true;
    }

    isDisposed(): boolean {
        return this.disposed;
    }
}

export class Repository<T extends Entity> {
    private items: Map<string, T> = new Map();

    add(item: T): void {
        this.items.set(item.id, item);
    }

    get(id: string): T | undefined {
        return this.items.get(id);
    }

    getAll(): T[] {
        return Array.from(this.items.values());
    }

    findBy(predicate: (item: T) => boolean): T[] {
        return this.getAll().filter(predicate);
    }

    count(): number {
        return this.items.size;
    }
}

export const Validator = class {
    #rules: Array<(value: string) => boolean> = [];

    addRule(rule: (value: string) => boolean): void {
        this.#rules.push(rule);
    }

    validate(value: string): boolean {
        return this.#rules.every(rule => rule(value));
    }
};

export class EventEmitter<T> {
    private listeners: Map<string, Array<(data: T) => void>> = new Map();

    on(event: string, listener: (data: T) => void): void {
        const list = this.listeners.get(event) || [];
        list.push(listener);
        this.listeners.set(event, list);
    }

    emit(event: string, data: T): void {
        const list = this.listeners.get(event) || [];
        list.forEach(listener => listener(data));
    }

    off(event: string): void {
        this.listeners.delete(event);
    }
}
