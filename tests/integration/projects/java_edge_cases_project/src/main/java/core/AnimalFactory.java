package core;

// --- Static Analysis Expected ---
//
// Defined entities:
//   Class (1): AnimalFactory
//   Methods (2): create(String, String), create(String, String, int)
//
// Edge cases covered:
//   - Static factory methods
//   - Method overloading (same name, different arity)
//   - Switch expression (Java 14+)
//   - Constructor calls from factory: new Dog(), new Cat(), new Duck()
//   - Overloaded method calling the other overload
//
// Expected call edges:
//   create(String, String)      → Dog(String)
//   create(String, String)      → Cat(String)
//   create(String, String)      → Duck(String)
//   create(String, String, int) → Dog(String, int)
//   create(String, String, int) → create(String, String)
//
// ---

public class AnimalFactory {

    public static Animal create(String type, String name) {
        return switch (type) {
            case "dog" -> new Dog(name);
            case "cat" -> new Cat(name);
            case "duck" -> new Duck(name);
            default -> throw new IllegalArgumentException("Unknown type: " + type);
        };
    }

    public static Animal create(String type, String name, int age) {
        if ("dog".equals(type)) {
            return new Dog(name, age);
        }
        return create(type, name);
    }
}
