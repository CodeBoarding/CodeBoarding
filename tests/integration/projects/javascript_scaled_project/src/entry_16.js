// --- Static Analysis Expected ---
//
// This is the entry point. It imports and calls symbols from all modules
// (except unused/) to exercise cross-module call edges.
//
// Expected call edges (from main):
//   main → createTask, Task, UrgentTask, Event, Repository, Validator,
//          EventEmitter, dispatch, buildQuery, processTask, processTaskChain,
//          summarizeTasks, applyMultiplier, computeNested, double,
//          isHighPriority, safeGetLabel, getTaskInfo, describeTask,
//          filterEntities, setupEventProcessing, createMultiplier,
//          compose, formatLabel, identity
// ---

import {
    Task,
    UrgentTask,
    Event,
    Repository,
    Validator,
    EventEmitter,
} from "./models_16/index.js";

import {
    createTask,
    dispatch,
    buildQuery,
    processTask,
    processTaskChain,
    summarizeTasks,
    applyMultiplier,
    computeNested,
    double,
    isHighPriority,
    safeGetLabel,
    getTaskInfo,
    describeTask,
    filterEntities,
    setupEventProcessing,
} from "./services_16/index.js";

import { createMultiplier, compose, formatLabel, identity } from "./utils_16/index.js";

export function main() {
    // --- Models ---
    const t1 = createTask("alpha");
    const t2 = new Task("2", "beta");
    const t3 = new UrgentTask("3", "gamma");
    const ev = new Event("launch", new Date());

    const repo = new Repository();
    repo.add(t1.id, t1);
    repo.add(t2.id, t2);
    repo.add(t3.id, t3);

    const validator = new Validator();
    validator.addRule((v) => v != null);

    const emitter = new EventEmitter();

    // --- Services ---
    dispatch(t1);
    const q = buildQuery();
    processTask(t1);
    processTaskChain([t1, t2, t3]);
    summarizeTasks([t1, t2, t3]);
    applyMultiplier(3, 7);
    computeNested(10, 20);
    double(5);
    isHighPriority(t3);
    safeGetLabel(t1);
    getTaskInfo(t2);
    describeTask(t3);
    filterEntities([t1, t2], (t) => t.priority > 0);
    setupEventProcessing(emitter);

    // --- Utils ---
    const triple = createMultiplier(3);
    const pipeline = compose((x) => x + 1, triple);
    formatLabel("test");
    identity(42);

    console.log(q, ev.name, repo.count(), validator.validate("ok"), pipeline(5));
}

