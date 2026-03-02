// --- Static Analysis Expected ---
//
// Defined entities:
//   Classes (1): OrphanClass
//   Methods (1): orphanMethod
//   Functions (1): neverCalled
//   Constants (1): UNUSED_CONSTANT
//
// This module is never imported — exercises dead-code detection.
// ---

export class OrphanClass {
    orphanMethod() {
        return "orphan";
    }
}

export function neverCalled() {
    return 42;
}

export const UNUSED_CONSTANT = "dead";
