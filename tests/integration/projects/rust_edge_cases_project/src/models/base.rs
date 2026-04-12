// Base types for the edge-case project. Exercises:
//   - struct + impl block
//   - trait declaration with default method
//   - trait implementation
//   - associated function (Entity::new)

pub struct Entity {
    pub id: u32,
}

impl Entity {
    pub fn new(id: u32) -> Self {
        Entity { id }
    }

    pub fn get_id(&self) -> u32 {
        self.id
    }
}

pub trait Speaker {
    fn speak(&self) -> String;

    fn shout(&self) -> String {
        // Default trait method — exercises trait dispatch on inherited impl
        let line = self.speak();
        line.to_uppercase()
    }
}
