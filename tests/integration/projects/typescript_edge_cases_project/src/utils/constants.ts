// --- Static Analysis Expected ---
//
// Defined entities:
//   Enums (2): Priority, Status
//   Constants (3): MAX_RETRIES, DEFAULT_TIMEOUT, DEFAULT_LABEL
//   Type aliases (2): Handler, AsyncHandler
//
// Expected call edges: none (leaf module — enums, constants, types only)
//
// Corner cases: enum with numeric values, enum with string values,
//   type aliases for function signatures, module-level constants
// Package: src.utils | imported_by: src.models, src.services, src
// ---

export enum Priority {
    Low = 0,
    Medium = 1,
    High = 2,
    Critical = 3,
}

export enum Status {
    Pending = "pending",
    Active = "active",
    Done = "done",
    Failed = "failed",
}

export const MAX_RETRIES = 3;
export const DEFAULT_TIMEOUT = 5000;
export const DEFAULT_LABEL = "untitled";

export type Handler<T> = (input: T) => T;
export type AsyncHandler<T> = (input: T) => Promise<T>;
