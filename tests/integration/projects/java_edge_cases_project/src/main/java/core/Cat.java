package core;

// --- Static Analysis Expected ---
//
// Defined entities:
//   Class (1): Cat
//   Constructor (1): Cat(String)
//   Methods (2): speak(), purr()
//
// Edge cases covered:
//   - Single inheritance (extends Animal)
//   - Method overriding (@Override speak)
//   - Cross-method call: purr → getName (inherited)
//
// Expected call edges:
//   Cat(String) → Animal(String)   (super() constructor chaining)
//   purr()      → getName()        (inherited method call)
//
// Class hierarchy:
//   Cat extends Animal
//
// ---

public class Cat extends Animal {

    public Cat(String name) {
        super(name);
    }

    @Override
    public String speak() {
        return "Meow!";
    }

    public String purr() {
        return getName() + " purrs";
    }
}
