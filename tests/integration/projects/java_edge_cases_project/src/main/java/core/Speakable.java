package core;

// --- Static Analysis Expected ---
//
// Defined entities:
//   Interface (1): Speakable
//   Methods (3): speak(), describe(), defaultGreeting()
//
// Edge cases covered:
//   - Interface with abstract method (speak)
//   - Interface with default method (describe)
//   - Interface with static method (defaultGreeting)
//
// Expected call edges:
//   describe() → speak()  (default method calling abstract method)
//
// ---

public interface Speakable {

    String speak();

    default String describe() {
        return "I am " + speak();
    }

    static String defaultGreeting() {
        return "Hello!";
    }
}
