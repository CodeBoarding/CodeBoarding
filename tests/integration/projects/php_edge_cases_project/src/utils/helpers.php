<?php
// --- Static Analysis Ground Truth ---
//
// File: src/utils/helpers.php
//
// Defined entities:
//   Functions (7): add, clamp, formatLabel, createMultiplier,
//                  compose, isNonNull, identity
//
// Expected references (lowercased):
//   src.utils.helpers.add
//   src.utils.helpers.clamp
//   src.utils.helpers.formatlabel
//   src.utils.helpers.createmultiplier
//   src.utils.helpers.compose
//   src.utils.helpers.isnonnull
//   src.utils.helpers.identity
//
// Expected call edges:
//   formatLabel → Config::DEFAULT_LABEL (cross-file constant access)
//
// Corner cases:
//   - Standalone namespaced functions (not methods)
//   - Closure return (createMultiplier returns a Closure)
//   - Arrow function (fn() => syntax in createMultiplier)
//   - Variadic parameter (compose takes callable ...$fns)
//   - mixed type parameter and return (identity, isNonNull)
//   - Cross-file constant access (Config::DEFAULT_LABEL)
// ---

namespace App\Utils;

function add(int $a, int $b): int {
    return $a + $b;
}

function clamp(int $value, int $min, int $max): int {
    if ($value < $min) {
        return $min;
    }
    if ($value > $max) {
        return $max;
    }
    return $value;
}

function formatLabel(string $name): string {
    if ($name === '') {
        return '[' . Config::DEFAULT_LABEL . ']';
    }
    return '[' . $name . ']';
}

function createMultiplier(int $factor): \Closure {
    return fn(int $x): int => $x * $factor;
}

function compose(callable ...$fns): \Closure {
    return function (int $x) use ($fns): int {
        $result = $x;
        foreach ($fns as $fn) {
            $result = $fn($result);
        }
        return $result;
    };
}

function isNonNull(mixed $value): bool {
    return $value !== null;
}

function identity(mixed $value): mixed {
    return $value;
}
