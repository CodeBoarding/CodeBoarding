<?php
// --- Static Analysis Ground Truth ---
//
// File: src/models/entities.php
//
// Defined entities:
//   Classes (6): Dog, Cat, Duck, Task, Repository, EventEmitter
//   Methods:
//     Dog:          __construct, speak, getName
//     Cat:          __construct, speak, getName
//     Duck:         __construct, speak, getName (swim via SwimmingTrait)
//     Task:         __construct, getLabel, serialize, isOverdue, dispose, isDisposed, title
//     Repository:   add, get, getAll, findBy, count
//     EventEmitter: on, emit, off
//
// Expected references (lowercased):
//   src.models.entities.dog
//   src.models.entities.cat
//   src.models.entities.duck
//   src.models.entities.task
//   src.models.entities.repository
//   src.models.entities.eventemitter
//   src.models.entities.speak
//   src.models.entities.getname
//   src.models.entities.getlabel
//   src.models.entities.serialize
//   src.models.entities.isoverdue
//   src.models.entities.dispose
//   src.models.entities.isdisposed
//   src.models.entities.title
//   src.models.entities.add
//   src.models.entities.get
//   src.models.entities.getall
//   src.models.entities.findby
//   src.models.entities.count
//   src.models.entities.on
//   src.models.entities.emit
//   src.models.entities.off
//
// Expected call edges:
//   Dog::__construct   → Entity::setType
//   Cat::__construct   → Entity::setType
//   Duck::__construct  → Entity::setType
//   Task::__construct  → Entity::setType
//   Task::getLabel     → Utils\formatLabel (cross-package)
//   Repository::findBy → Repository::getAll
//
// Corner cases:
//   - Single inheritance (Dog/Cat/Duck/Task extend Entity)
//   - Multiple interface implementation (Duck implements Speakable, Swimmable)
//   - Trait usage (Duck uses SwimmingTrait)
//   - Constructor property promotion (PHP 8.0+)
//   - Readonly properties (PHP 8.1+) — public readonly string $taskName
//   - Parent constructor call (parent::__construct)
//   - Cross-namespace function call (formatLabel)
//   - Default parameter value (Cat $indoor = true)
//   - Map-based storage (Repository with array)
//   - Callback/listener pattern (EventEmitter)
// ---

namespace App\Models;

use App\Utils\Priority;
use App\Utils\Status;
use function App\Utils\formatLabel;

class Dog extends Entity implements Speakable {
    public function __construct(
        string $id,
        private readonly string $name,
        private string $breed
    ) {
        parent::__construct($id);
        $this->setType('Dog');
    }

    public function speak(): string {
        return 'Woof!';
    }

    public function getName(): string {
        return $this->name;
    }
}

class Cat extends Entity implements Speakable {
    public function __construct(
        string $id,
        private readonly string $name,
        private bool $indoor = true
    ) {
        parent::__construct($id);
        $this->setType('Cat');
    }

    public function speak(): string {
        return 'Meow!';
    }

    public function getName(): string {
        return $this->name;
    }
}

class Duck extends Entity implements Speakable, Swimmable {
    use SwimmingTrait;

    public function __construct(string $id) {
        parent::__construct($id);
        $this->setType('Duck');
    }

    public function speak(): string {
        return 'Quack!';
    }

    public function getName(): string {
        return $this->id;
    }
}

class Task extends Entity implements Disposable {
    private bool $disposed = false;

    public function __construct(
        string $id,
        public readonly string $taskName,
        public Priority $priority,
        public Status $status
    ) {
        parent::__construct($id);
        $this->setType('Task');
    }

    public function getLabel(): string {
        return formatLabel($this->taskName);
    }

    public function serialize(): array {
        return [
            'id' => $this->getId(),
            'name' => $this->taskName,
            'priority' => $this->priority->value,
            'status' => $this->status->value,
        ];
    }

    public function isOverdue(): bool {
        return $this->status === Status::Active && $this->priority === Priority::High;
    }

    public function dispose(): void {
        $this->disposed = true;
    }

    public function isDisposed(): bool {
        return $this->disposed;
    }

    public function title(): string {
        return $this->taskName . ' (' . $this->status->value . ')';
    }
}

class Repository {
    private array $items = [];

    public function add(string $id, mixed $item): void {
        $this->items[$id] = $item;
    }

    public function get(string $id): mixed {
        return $this->items[$id] ?? null;
    }

    public function getAll(): array {
        return array_values($this->items);
    }

    public function findBy(callable $predicate): array {
        return array_filter($this->getAll(), $predicate);
    }

    public function count(): int {
        return count($this->items);
    }
}

class EventEmitter {
    private array $listeners = [];

    public function on(string $event, callable $callback): void {
        $this->listeners[$event][] = $callback;
    }

    public function emit(string $event, mixed $data): void {
        foreach ($this->listeners[$event] ?? [] as $cb) {
            $cb($data);
        }
    }

    public function off(string $event): void {
        unset($this->listeners[$event]);
    }
}
