// Binary crate root. Exercises:
//   - main.rs stem collapse (functions here should be `src.main.<name>`)
//   - cross-module function calls (edges to models, services, utils)
//   - trait method invocation through dyn dispatch

mod models;
mod services;
mod utils;

use models::base::{Entity, Speaker};
use models::entities::{Cat, Dog, Task};
use services::processor;
use utils::helpers;

fn main() {
    let cat = Cat::new("Whiskers");
    let dog = Dog::new("Rex");

    println!("{}", cat.speak());
    println!("{}", dog.speak());

    let speakers: Vec<Box<dyn Speaker>> = vec![Box::new(cat), Box::new(dog)];
    for s in &speakers {
        println!("{}", s.speak());
    }

    let task = Task::new(1, "Write tests");
    let label = task.get_label();
    println!("{}", label);

    let summary = processor::summarize(&[task]);
    println!("count: {}", summary.count);

    let r = helpers::add(2, 3);
    println!("{}", r);
}
