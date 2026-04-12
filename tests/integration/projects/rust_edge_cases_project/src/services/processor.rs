// Free function + struct in a sibling module — exercises cross-module
// references and a small "summary" data type.

use crate::models::entities::Task;

pub struct Summary {
    pub count: usize,
    pub last_label: String,
}

pub fn summarize(tasks: &[Task]) -> Summary {
    let last_label = tasks
        .last()
        .map(|t| t.get_label())
        .unwrap_or_default();
    Summary {
        count: tasks.len(),
        last_label,
    }
}
