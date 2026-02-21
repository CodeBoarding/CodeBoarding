// --- Static Analysis Expected ---
//
// Defined entities:
//   Functions (5): add, clamp, formatLabel, createMultiplier, compose
//   Arrow functions (2): isNonNull (type guard), identity (generic)
//   Inner arrow (1): multiplier (inside createMultiplier closure)
//
// Expected call edges (from function bodies):
//   createMultiplier → multiplier     (returns inner arrow function)
//   compose          → f, g           (calls both function arguments)
//
// Corner cases: higher-order function returning closure, generic function,
//   type guard as arrow function, function composition, pure leaf utilities
// Package: src.utils | imported_by: src.services, src
// ---

import { Priority } from "./constants";

export function add(a: number, b: number): number {
    return a + b;
}

export function clamp(value: number, min: number, max: number): number {
    return Math.min(Math.max(value, min), max);
}

export function formatLabel(name: string, priority: Priority): string {
    return `[${Priority[priority]}] ${name}`;
}

export function createMultiplier(factor: number): (x: number) => number {
    const multiplier = (x: number): number => x * factor;
    return multiplier;
}

export function compose<T>(f: (x: T) => T, g: (x: T) => T): (x: T) => T {
    return (x: T) => f(g(x));
}

export const isNonNull = <T>(value: T | null | undefined): value is T => {
    return value != null;
};

export function identity<T>(x: T): T {
    return x;
}
