// --- Static Analysis Expected ---
//
// Defined entities:
//   Functions (7): add, clamp, formatLabel, createMultiplier, compose, isNonNull, identity
//   Inner function (1): multiplier (closure returned by createMultiplier)
//
// Expected call edges:
//   (none from within this module — all leaf functions)
//
// Corner cases:
//   - Higher-order function (createMultiplier returns closure)
//   - Generic composition (compose)
//   - Arrow function (identity)
//   - Nullish coalescing (??)
//   - Rest parameter in compose
// ---

/**
 * @param {number} a
 * @param {number} b
 * @returns {number}
 */
export function add(a, b) {
    return a + b;
}

/**
 * @param {number} value
 * @param {number} min
 * @param {number} max
 * @returns {number}
 */
export function clamp(value, min, max) {
    return Math.min(Math.max(value, min), max);
}

/**
 * @param {string | null | undefined} name
 * @returns {string}
 */
export function formatLabel(name) {
    return `[${name ?? "unknown"}]`;
}

/**
 * Higher-order function returning a closure.
 * @param {number} factor
 * @returns {(x: number) => number}
 */
export function createMultiplier(factor) {
    /** @param {number} x */
    function multiplier(x) {
        return x * factor;
    }
    return multiplier;
}

/**
 * Compose multiple single-arg functions left-to-right.
 * @param {...Function} fns
 * @returns {Function}
 */
export function compose(...fns) {
    return (x) => fns.reduce((acc, fn) => fn(acc), x);
}

/**
 * @param {*} value
 * @returns {boolean}
 */
export function isNonNull(value) {
    return value != null;
}

/** @type {<T>(x: T) => T} */
export const identity = (x) => x;
