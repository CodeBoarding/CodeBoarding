// Intentional dead code so rust-analyzer publishes diagnostics that map to
// the unused_code_diagnostics health check categories.
use std::collections::HashMap; // unused_imports — never used

pub fn used_function() -> i32 {
    let unused_local = 5; // unused_variables — assigned but never read
    42
}

fn never_called() -> i32 {
    // dead_code — private fn with no callers
    99
}

#[allow(dead_code)]
struct UnusedField {
    used: i32,
    unused: String, // dead_code — field never read
}
