// Concrete domain types. Exercises:
//   - multiple struct definitions
//   - impl Trait for Struct
//   - method that calls another method on the same struct
//   - method that calls a function in a sibling module (utils::helpers::format_label)

use crate::models::base::{Entity, Speaker};
use crate::utils::helpers;

pub struct Cat {
    pub entity: Entity,
    pub name: String,
}

impl Cat {
    pub fn new(name: &str) -> Self {
        Cat {
            entity: Entity::new(1),
            name: name.to_string(),
        }
    }
}

impl Speaker for Cat {
    fn speak(&self) -> String {
        format!("{} says meow", self.name)
    }
}

pub struct Dog {
    pub entity: Entity,
    pub name: String,
}

impl Dog {
    pub fn new(name: &str) -> Self {
        Dog {
            entity: Entity::new(2),
            name: name.to_string(),
        }
    }
}

impl Speaker for Dog {
    fn speak(&self) -> String {
        format!("{} says woof", self.name)
    }
}

pub struct Task {
    pub id: u32,
    pub title: String,
}

impl Task {
    pub fn new(id: u32, title: &str) -> Self {
        Task {
            id,
            title: title.to_string(),
        }
    }

    pub fn get_label(&self) -> String {
        helpers::format_label(self.id, &self.title)
    }
}
