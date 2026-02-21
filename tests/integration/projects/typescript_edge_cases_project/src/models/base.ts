// --- Static Analysis Expected ---
//
// Defined entities:
//   Interfaces (3): Identifiable, Serializable, Disposable
//   Classes (1): Entity (abstract)
//   Methods (3): Entity.constructor, Entity.getType (abstract), Entity.toString
//
// Expected call edges (from method bodies):
//   toString → getType   (this.getType() inside template literal)
//
// Class hierarchy:
//   Entity implements Identifiable — abstract base, superclass of Task, UrgentTask
//   Identifiable                   — interface, no runtime existence
//   Serializable                   — interface with method signature
//   Disposable                     — interface with method signature
//
// Corner cases: interfaces (TS-only), abstract class, abstract method,
//   implements keyword, readonly property, toString calling abstract method
// Package: src.models | imports: none (no cross-package imports)
// ---

export interface Identifiable {
    readonly id: string;
}

export interface Serializable {
    serialize(): string;
}

export interface Disposable {
    dispose(): void;
}

export abstract class Entity implements Identifiable {
    readonly id: string;

    constructor(id: string) {
        this.id = id;
    }

    abstract getType(): string;

    toString(): string {
        return `${this.getType()}(${this.id})`;
    }
}
