package core;

// --- Static Analysis Expected ---
//
// Defined entities:
//   Class (1): Animal
//   Constructor (1): Animal(String)
//   Methods (3): getName(), toString(), speak()
//
// Edge cases covered:
//   - Abstract class implementing interface
//   - Abstract method override declaration
//   - toString() calling getName() and speak()
//   - Protected field access
//
// Expected call edges:
//   toString() → getName()
//   toString() → speak()
//
// Class hierarchy:
//   Animal implements Speakable
//   Animal is superclass of Dog, Cat, Duck
//
// ---

public abstract class Animal implements Speakable {

    protected final String name;

    public Animal(String name) {
        this.name = name;
    }

    public String getName() {
        return name;
    }

    @Override
    public abstract String speak();

    @Override
    public String toString() {
        return getName() + ": " + speak();
    }
}
