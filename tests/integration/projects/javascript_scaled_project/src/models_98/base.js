// --- Static Analysis Expected ---
//
// Defined entities:
//   Classes (1): Entity
//   Methods (3): constructor, getType, toString
//   Interfaces (simulated via JSDoc):
//     Identifiable (id property)
//     Serializable (serialize method)
//     Disposable (dispose, isDisposed)
//
// Expected call edges:
//   toString → getType  (this.getType())
//
// Corner cases:
//   - #private field (ES2022 private class field)
//   - Getter property
//   - Static counter field
//   - JSDoc-based "interface" documentation (duck typing)
// ---

/**
 * @typedef {Object} Identifiable
 * @property {string} id
 */

/**
 * @typedef {Object} Serializable
 * @property {() => object} serialize
 */

/**
 * @typedef {Object} Disposable
 * @property {() => void} dispose
 * @property {() => boolean} isDisposed
 */

let _counter = 0;

export class Entity {
    /** @type {string} */
    #type;

    /** @type {string} */
    id;

    /** @type {number} */
    static instanceCount = 0;

    /**
     * @param {string} id
     * @param {string} type
     */
    constructor(id, type) {
        this.id = id;
        this.#type = type;
        _counter++;
        Entity.instanceCount++;
    }

    /** @returns {string} */
    getType() {
        return this.#type;
    }

    /** @returns {string} */
    toString() {
        return `${this.getType()}:${this.id}`;
    }
}
