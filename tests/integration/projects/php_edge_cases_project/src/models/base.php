<?php
// --- Static Analysis Ground Truth ---
//
// File: src/models/base.php
//
// Defined entities:
//   Interfaces (3):      Speakable, Swimmable, Disposable
//   Trait (1):           SwimmingTrait
//   Abstract class (1): Entity
//   Methods:
//     Speakable:     speak(), getName()
//     Swimmable:     swim()
//     Disposable:    dispose(), isDisposed()
//     SwimmingTrait: swim()
//     Entity:        __construct, getId, getType, __toString, setType
//
// Expected references (lowercased):
//   src.models.base.speakable
//   src.models.base.swimmable
//   src.models.base.disposable
//   src.models.base.swimmingtrait
//   src.models.base.entity
//   src.models.base.speak
//   src.models.base.getname
//   src.models.base.swim
//   src.models.base.dispose
//   src.models.base.isdisposed
//   src.models.base.__construct
//   src.models.base.getid
//   src.models.base.gettype
//   src.models.base.__tostring
//   src.models.base.settype
//
// Expected call edges:
//   __toString → getType   ($this->getType() inside __toString)
//
// Corner cases:
//   - Interface definitions with method signatures
//   - Trait with concrete method implementation
//   - Abstract class with concrete methods
//   - Magic methods (__construct, __toString)
//   - Constructor property promotion (protected readonly $id)
//   - Private static property ($entityCount)
//   - self:: static access in constructor
// ---

namespace App\Models;

interface Speakable {
    public function speak(): string;
    public function getName(): string;
}

interface Swimmable {
    public function swim(): string;
}

interface Disposable {
    public function dispose(): void;
    public function isDisposed(): bool;
}

trait SwimmingTrait {
    public function swim(): string {
        return 'Splash!';
    }
}

abstract class Entity {
    private static int $entityCount = 0;
    private string $typeName = '';

    public function __construct(
        protected readonly string $id
    ) {
        self::$entityCount++;
    }

    public function getId(): string {
        return $this->id;
    }

    public function getType(): string {
        return $this->typeName;
    }

    public function __toString(): string {
        return $this->getType() . ':' . $this->id;
    }

    public function setType(string $type): void {
        $this->typeName = $type;
    }
}
