package core;

// --- Static Analysis Expected ---
//
// Defined entities:
//   Enum (1): Config
//   Enum constants (2): DEBUG, RELEASE
//   Methods (2): getPrefix(), format(String)
//
// Edge cases covered:
//   - Enum with abstract method
//   - Constant-specific method implementation (each constant overrides getPrefix)
//   - Enum constants as anonymous subclasses
//   - Enum method calling abstract method
//
// Expected call edges:
//   format(String) → getPrefix()  (concrete method calling abstract)
//
// ---

public enum Config {

    DEBUG {
        @Override
        public String getPrefix() {
            return "[DEBUG]";
        }
    },

    RELEASE {
        @Override
        public String getPrefix() {
            return "[RELEASE]";
        }
    };

    public abstract String getPrefix();

    public String format(String message) {
        return getPrefix() + " " + message;
    }
}
