<?php
// --- Static Analysis Ground Truth ---
//
// File: src/services/processor.php
//
// Defined entities:
//   Functions (13): double, isHighPriority, dispatch,
//                   processTask, processTaskChain, summarizeTasks,
//                   applyMultiplier, computeNested, safeGetLabel,
//                   createTask, getTaskInfo, describeTask,
//                   setupEventProcessing
//
// Expected references (lowercased):
//   src.services.processor.double
//   src.services.processor.ishighpriority
//   src.services.processor.dispatch
//   src.services.processor.processtask
//   src.services.processor.processtaskchain
//   src.services.processor.summarizetasks
//   src.services.processor.applymultiplier
//   src.services.processor.computenested
//   src.services.processor.safegetlabel
//   src.services.processor.createtask
//   src.services.processor.gettaskinfo
//   src.services.processor.describetask
//   src.services.processor.setupeventprocessing
//
// Expected call edges:
//   double          → Utils\add
//   computeNested   → Utils\add, Utils\clamp
//   applyMultiplier → Utils\createMultiplier
//   createTask      → Task (new Task)
//   processTask     → Task::getLabel
//   safeGetLabel    → Task::getLabel
//   describeTask    → Task::getLabel
//   setupEventProcessing → EventEmitter::on, EventEmitter::emit
//
// Corner cases:
//   - Array-based dispatch table (dispatch function)
//   - Arrow functions as array values (fn() =>)
//   - match expression (PHP 8.0+) in describeTask
//   - Null-safe operator (?->) in safeGetLabel
//   - Named arguments in computeNested (min:, max:)
//   - Cross-namespace function calls (Utils\add, Utils\clamp)
//   - Cross-namespace class instantiation (new Task)
//   - Array destructuring return (getTaskInfo)
//   - Closure as callback argument (setupEventProcessing)
// ---

namespace App\Services;

use App\Models\Task;
use App\Models\EventEmitter;
use App\Utils\Priority;
use App\Utils\Status;
use function App\Utils\add;
use function App\Utils\clamp;
use function App\Utils\createMultiplier;

function double(int $x): int {
    return add($x, $x);
}

function isHighPriority(Task $t): bool {
    return $t->priority === Priority::High;
}

function dispatch(Task $t): string {
    $handlers = [
        Status::Active->value  => fn(Task $t): string => 'Active: ' . $t->taskName,
        Status::Pending->value => fn(Task $t): string => 'Pending: ' . $t->taskName,
        Status::Done->value    => fn(Task $t): string => 'Done: ' . $t->taskName,
    ];
    return ($handlers[$t->status->value] ?? fn(Task $t): string => 'Unknown: ' . $t->taskName)($t);
}

function processTask(Task $t): string {
    $label = $t->getLabel();
    return 'Processed: ' . $label;
}

function processTaskChain(array $tasks): array {
    $results = [];
    foreach ($tasks as $t) {
        if (isHighPriority($t)) {
            $results[] = processTask($t);
        }
    }
    return $results;
}

function summarizeTasks(array $tasks): array {
    $summary = ['total' => count($tasks), 'high_priority' => 0];
    foreach ($tasks as $t) {
        if (isHighPriority($t)) {
            $summary['high_priority']++;
        }
    }
    return $summary;
}

function applyMultiplier(int $factor, int $value): int {
    $fn = createMultiplier($factor);
    return $fn($value);
}

function computeNested(int $a, int $b): int {
    return clamp(add($a, $b), min: 0, max: 100);
}

function safeGetLabel(?Task $t): string {
    return $t?->getLabel() ?? 'none';
}

function createTask(string $name): Task {
    return new Task(
        id: uniqid(),
        taskName: $name,
        priority: Priority::Medium,
        status: Status::Active,
    );
}

function getTaskInfo(Task $t): array {
    return [$t->taskName, $t->priority->value];
}

function describeTask(Task $t): string {
    $priorityLabel = match ($t->priority) {
        Priority::High   => 'HIGH',
        Priority::Medium => 'MEDIUM',
        Priority::Low    => 'LOW',
    };
    return sprintf('Task %s [%s]', $t->getLabel(), $priorityLabel);
}

function setupEventProcessing(EventEmitter $emitter): void {
    $emitter->on('task', function (mixed $data): void {
        echo 'task event: ' . print_r($data, true);
    });
    $emitter->emit('task', ['action' => 'created']);
}
