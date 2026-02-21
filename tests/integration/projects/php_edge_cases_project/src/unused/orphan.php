<?php
// --- Static Analysis Ground Truth ---
//
// File: src/unused/orphan.php
//
// Defined entities:
//   Class (1):     OrphanClass
//   Methods (1):   orphanMethod
//   Functions (1): neverCalled
//   Constants (1): UNUSED_CONSTANT
//
// Expected references (lowercased):
//   src.unused.orphan.orphanclass
//   src.unused.orphan.orphanmethod
//   src.unused.orphan.nevercalled
//   src.unused.orphan.unused_constant
//
// This namespace is never imported — exercises dead-code detection.
// No expected call edges.
// ---

namespace App\Unused;

class OrphanClass {
    public function orphanMethod(): string {
        return 'orphan';
    }
}

function neverCalled(): int {
    return 42;
}

const UNUSED_CONSTANT = 'dead';
