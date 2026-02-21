package core;

import java.util.Objects;

// --- Static Analysis Expected ---
//
// Defined entities:
//   Class (1): Container
//   Static nested class (1): Metadata
//   Inner class (1): Item
//   Constructor: Container(String)
//   Methods: getLabel(), equals(Object), hashCode()
//   Metadata constructor: Metadata(String, String)
//   Metadata methods: format()
//   Item constructor: Item(String)
//   Item methods: describe()
//
// Edge cases covered:
//   - Static nested class (Container.Metadata)
//   - Non-static inner class (Container.Item) accessing outer instance method
//   - equals(Object) with pattern matching instanceof (Java 16+)
//   - hashCode() calling Objects.hash()
//   - Inner class calling outer class method (describe → getLabel)
//
// Expected call edges:
//   describe()      → getLabel()         (inner class → outer class)
//   equals(Object)  → Objects.equals()   (external utility call)
//   hashCode()      → Objects.hash()     (external utility call)
//
// ---

public class Container {

    private final String label;

    public Container(String label) {
        this.label = label;
    }

    public String getLabel() {
        return label;
    }

    public static class Metadata {

        private final String key;
        private final String value;

        public Metadata(String key, String value) {
            this.key = key;
            this.value = value;
        }

        public String format() {
            return key + "=" + value;
        }
    }

    public class Item {

        private final String content;

        public Item(String content) {
            this.content = content;
        }

        public String describe() {
            return getLabel() + ": " + content;
        }
    }

    @Override
    public boolean equals(Object o) {
        if (this == o) return true;
        if (!(o instanceof Container c)) return false;
        return Objects.equals(label, c.label);
    }

    @Override
    public int hashCode() {
        return Objects.hash(label);
    }
}
