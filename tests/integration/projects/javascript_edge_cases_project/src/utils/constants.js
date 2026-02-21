// --- Static Analysis Expected ---
//
// Defined entities:
//   Constants (5): Priority, Status, MAX_RETRIES, DEFAULT_TIMEOUT, DEFAULT_LABEL
//   Type aliases (via JSDoc): Handler
//
// Expected references:
//   src.utils.constants.priority
//   src.utils.constants.status
//   src.utils.constants.max_retries
//   src.utils.constants.default_timeout
//   src.utils.constants.default_label
//   src.utils.constants.handler
// ---

/** @enum {number} */
export const Priority = Object.freeze({
    LOW: 0,
    MEDIUM: 1,
    HIGH: 2,
});

/** @enum {string} */
export const Status = Object.freeze({
    ACTIVE: "active",
    PENDING: "pending",
    DONE: "done",
});

export const MAX_RETRIES = 3;
export const DEFAULT_TIMEOUT = 5000;
export const DEFAULT_LABEL = "untitled";

/**
 * @callback Handler
 * @param {number} x
 * @returns {number}
 */
export const Handler = undefined;
