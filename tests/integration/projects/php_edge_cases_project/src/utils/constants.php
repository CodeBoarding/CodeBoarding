<?php
// --- Static Analysis Ground Truth ---
//
// File: src/utils/constants.php
//
// Defined entities:
//   Enums (2):     Priority (backed int), Status (backed string)
//   Class (1):     Config
//   Enum cases (6): Priority::Low, Priority::Medium, Priority::High,
//                   Status::Active, Status::Pending, Status::Done
//   Methods (1):   Priority::label
//   Constants (3): Config::MAX_RETRIES, Config::DEFAULT_TIMEOUT, Config::DEFAULT_LABEL
//
// Expected references (lowercased):
//   src.utils.constants.priority
//   src.utils.constants.status
//   src.utils.constants.config
//   src.utils.constants.low
//   src.utils.constants.medium
//   src.utils.constants.high
//   src.utils.constants.active
//   src.utils.constants.pending
//   src.utils.constants.done
//   src.utils.constants.label
//   src.utils.constants.max_retries
//   src.utils.constants.default_timeout
//   src.utils.constants.default_label
//
// Corner cases:
//   - Backed enums (PHP 8.1+) — int-backed and string-backed
//   - Enum method (label() with match expression)
//   - Class constants with visibility
//   - Enum cases as named constants
// ---

namespace App\Utils;

enum Priority: int {
    case Low = 0;
    case Medium = 1;
    case High = 2;

    public function label(): string {
        return match ($this) {
            self::Low => 'low',
            self::Medium => 'medium',
            self::High => 'high',
        };
    }
}

enum Status: string {
    case Active = 'active';
    case Pending = 'pending';
    case Done = 'done';
}

class Config {
    public const MAX_RETRIES = 3;
    public const DEFAULT_TIMEOUT = 5000;
    public const DEFAULT_LABEL = 'untitled';
}
