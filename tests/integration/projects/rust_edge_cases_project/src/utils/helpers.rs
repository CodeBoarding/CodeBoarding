// Free helper functions — exercises function-level references and a
// dependency that other modules import (Task::get_label calls format_label).

pub fn add(a: i32, b: i32) -> i32 {
    a + b
}

pub fn format_label(id: u32, title: &str) -> String {
    format!("[{}] {}", id, title)
}
