<?php
// --- Static Analysis Ground Truth ---
//
// File: main.php
//
// Defined entities:
//   Functions (1): main
//
// Expected references (lowercased):
//   main.main
//
// Expected call edges (from main):
//   main → Dog          (constructor)
//   main → Cat          (constructor)
//   main → Duck         (constructor)
//   main → Task         (constructor)
//   main → Repository   (constructor)
//   main → EventEmitter (constructor)
//   main → createTask, dispatch, buildQuery, processTask
//   main → processTaskChain, summarizeTasks, applyMultiplier, computeNested
//   main → double, isHighPriority, safeGetLabel, getTaskInfo
//   main → describeTask, setupEventProcessing
//   main → createMultiplier, compose, formatLabel, identity, add
//   main → Dog::speak, Cat::speak, Duck::speak, Duck::swim
//   main → Task::serialize, Task::isOverdue, Task::dispose, Task::isDisposed
//   main → Entity::getType, Entity::getId
//   main → Repository::add, Repository::count
//   main → Priority::High->label()
//
// Corner cases:
//   - Group use declaration (use App\Models\{Dog, Cat, ...})
//   - use function imports for standalone namespace functions
//   - require_once for file inclusion (no Composer autoload)
//   - Array destructuring ([$name, $prio] = getTaskInfo(...))
//   - Arrow function as argument (fn(int $x): int => $x + 1)
//   - Enum method call (Priority::High->label())
//   - Ternary with method call result
// ---

require_once __DIR__ . '/src/utils/constants.php';
require_once __DIR__ . '/src/utils/helpers.php';
require_once __DIR__ . '/src/models/base.php';
require_once __DIR__ . '/src/models/entities.php';
require_once __DIR__ . '/src/services/builder.php';
require_once __DIR__ . '/src/services/processor.php';

use App\Models\Dog;
use App\Models\Cat;
use App\Models\Duck;
use App\Models\Task;
use App\Models\Repository;
use App\Models\EventEmitter;
use App\Utils\Priority;
use App\Utils\Status;
use function App\Services\buildQuery;
use function App\Services\createTask;
use function App\Services\dispatch;
use function App\Services\processTask;
use function App\Services\processTaskChain;
use function App\Services\summarizeTasks;
use function App\Services\applyMultiplier;
use function App\Services\computeNested;
use function App\Services\double;
use function App\Services\isHighPriority;
use function App\Services\safeGetLabel;
use function App\Services\getTaskInfo;
use function App\Services\describeTask;
use function App\Services\setupEventProcessing;
use function App\Utils\add;
use function App\Utils\createMultiplier;
use function App\Utils\compose;
use function App\Utils\formatLabel;
use function App\Utils\identity;

function main(): void {
    // --- Models: constructors and interface satisfaction ---
    $dog = new Dog('1', 'Rex', 'Labrador');
    $cat = new Cat('2', 'Whiskers');
    $duck = new Duck('3');

    // Interface method calls
    echo $dog->speak() . "\n";
    echo $cat->speak() . "\n";
    echo $duck->speak() . "\n";
    echo $duck->swim() . "\n";

    // --- Models: Task with inheritance ---
    $t1 = createTask('alpha');
    $t2 = new Task('2', 'beta', Priority::Medium, Status::Active);
    $t3 = new Task('3', 'gamma', Priority::High, Status::Active);

    print_r($t2->serialize());
    echo $t3->isOverdue() ? 'overdue' : 'ok';
    $t1->dispose();
    echo $t1->isDisposed() ? 'disposed' : 'active';

    // Calling inherited Entity method through Task
    echo $t1->getType();

    // --- Repository ---
    $repo = new Repository();
    $repo->add($t1->getId(), $t1);
    $repo->add($t2->getId(), $t2);
    echo 'Count: ' . $repo->count();

    // --- EventEmitter ---
    $emitter = new EventEmitter();
    setupEventProcessing($emitter);

    // --- Services ---
    dispatch($t1);
    $q = buildQuery();
    processTask($t1);
    processTaskChain([$t1, $t2, $t3]);
    summarizeTasks([$t1, $t2, $t3]);
    applyMultiplier(3, 7);
    computeNested(10, 20);
    double(5);
    isHighPriority($t3);
    safeGetLabel($t1);
    describeTask($t3);

    // Array destructuring (multiple return values)
    [$name, $prio] = getTaskInfo($t2);
    echo $name . ' ' . $prio;

    // --- Utils ---
    $triple = createMultiplier(3);
    $pipeline = compose(fn(int $x): int => $x + 1, $triple);
    formatLabel('test');
    identity(42);
    echo add(1, 2);
    echo $q . "\n";
    echo $pipeline(5) . "\n";

    // Enum method call
    echo Priority::High->label() . "\n";
}

main();
