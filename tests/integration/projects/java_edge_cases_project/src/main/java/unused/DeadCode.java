package unused;

// --- Static Analysis Expected ---
//
// Defined entities:
//   Class (1): DeadCode
//   Constants (1): UNUSED_CONSTANT
//   Methods (2): neverCalled(), orphanMethod()
//
// Edge cases covered:
//   - Entirely unreferenced class (no imports from other packages)
//   - Dead code detection: class, methods, and constants never used
//   - Package with no inbound dependencies
//
// ---

public class DeadCode {

    public static final String UNUSED_CONSTANT = "never used";

    public static void neverCalled() {
        System.out.println("This is dead code");
    }

    public String orphanMethod() {
        return "orphan";
    }
}
