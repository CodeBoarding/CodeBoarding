// --- Static Analysis Expected ---
//
// Defined entities:
//   Classes (1): OrphanClass
//   Methods (1): OrphanClass.orphanMethod
//   Functions (1): neverCalled
//   Constants (1): UNUSED_CONSTANT
//
// Expected call edges: none (no function here calls another project function)
//
// Class hierarchy:
//   OrphanClass — standalone, no superclass, no subclass
//
// Corner cases: entirely unreferenced module — nothing in the project imports or
//   calls any symbol defined here. Tests unused code / dead code detection.
// Package: src.unused | no imports, not imported by anyone
// ---

export class OrphanClass {
    orphanMethod(): string {
        return "never called";
    }
}

export function neverCalled(): number {
    return 42;
}

export const UNUSED_CONSTANT = "unused";
