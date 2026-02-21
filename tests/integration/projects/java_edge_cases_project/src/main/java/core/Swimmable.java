package core;

// --- Static Analysis Expected ---
//
// Defined entities:
//   Interface (1): Swimmable
//   Methods (1): swim()
//
// Edge cases covered:
//   - Second interface for testing multiple interface implementation
//   - Interface default method providing concrete implementation
//
// ---

public interface Swimmable {

    default String swim() {
        return "Swimming!";
    }
}
