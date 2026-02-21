package core;

// --- Static Analysis Expected ---
//
// Defined entities:
//   Class (1): Dog
//   Constructors (2): Dog(String, int), Dog(String)
//   Methods (3): speak(), fetch(String), toString()
//
// Edge cases covered:
//   - Single inheritance (extends Animal)
//   - Constructor chaining with this()
//   - Constructor chaining with super()
//   - Method overriding (@Override speak, toString)
//   - super.toString() call
//   - Cross-method call: fetch → getName (inherited)
//
// Expected call edges:
//   Dog(String)     → Dog(String, int)   (this() constructor chaining)
//   Dog(String, int) → Animal(String)     (super() constructor chaining)
//   fetch(String)   → getName()           (inherited method call)
//   toString()      → super.toString()    (explicit super call)
//
// Class hierarchy:
//   Dog extends Animal
//
// ---

public class Dog extends Animal {

    private final int age;

    public Dog(String name, int age) {
        super(name);
        this.age = age;
    }

    public Dog(String name) {
        this(name, 0);
    }

    @Override
    public String speak() {
        return "Woof!";
    }

    public String fetch(String item) {
        return getName() + " fetches " + item;
    }

    @Override
    public String toString() {
        return super.toString() + " (age: " + age + ")";
    }
}
