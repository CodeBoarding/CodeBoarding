package core;

// --- Static Analysis Expected ---
//
// Defined entities:
//   Class (1): Duck
//   Constructor (1): Duck(String)
//   Methods (2): speak(), actions()
//
// Edge cases covered:
//   - Multiple interface implementation (extends Animal implements Swimmable)
//   - Diamond-like pattern: Animal implements Speakable, Duck implements Swimmable
//   - Method calling both inherited interface default methods
//
// Expected call edges:
//   Duck(String) → Animal(String)  (super() constructor chaining)
//   actions()    → speak()         (own method call)
//   actions()    → swim()          (inherited default method call)
//
// Class hierarchy:
//   Duck extends Animal
//   Duck implements Swimmable
//
// ---

public class Duck extends Animal implements Swimmable {

    public Duck(String name) {
        super(name);
    }

    @Override
    public String speak() {
        return "Quack!";
    }

    public String actions() {
        return speak() + " " + swim();
    }
}
