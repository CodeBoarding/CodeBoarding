// --- Static Analysis Expected ---
//
// Barrel re-export file (TypeScript idiom).
// Re-exports all public symbols from services submodules.
//
// Corner cases: barrel/index re-exports across multiple submodules
// Package: src.services
// ---

export { QueryBuilder, buildQuery } from "./builder";
export type { QueryResult } from "./builder";
export {
    double,
    isHighPriority,
    taskHandlers,
    dispatch,
    processTask,
    processTaskChain,
    summarizeTasks,
    applyMultiplier,
    computeNested,
    safeGetLabel,
    createTask,
    getTaskInfo,
    describeTask,
    DEFAULT_HANDLER,
    filterEntities,
    setupEventProcessing,
} from "./processor";
