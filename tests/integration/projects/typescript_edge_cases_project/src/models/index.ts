// --- Static Analysis Expected ---
//
// Barrel re-export file (TypeScript idiom).
// Re-exports all public symbols from models submodules.
//
// Corner cases: barrel/index re-exports, type-only re-exports
// Package: src.models
// ---

export { Entity } from "./base";
export type { Identifiable, Serializable, Disposable } from "./base";
export { Task, UrgentTask, Event, Repository, Validator, EventEmitter } from "./entities";
