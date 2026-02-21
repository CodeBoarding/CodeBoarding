package utils;

// --- Static Analysis Expected ---
//
// Defined entities:
//   Class (1): Helpers
//   Constants (3): MAX_VALUE, MIN_VALUE, DEFAULT_NAME
//   Methods (7): add(int, int), add(double, double), subtract(int, int),
//                clamp(int, int, int), formatName(String, String),
//                sum(int...), firstOrDefault(T[], T)
//
// Edge cases covered:
//   - Static utility class (all static methods)
//   - Method overloading: add(int, int) vs add(double, double)
//   - Varargs: sum(int...)
//   - Generic method: <T> T firstOrDefault(T[], T)
//   - Module-level constants (public static final)
//   - Math.min / Math.max calls (external standard library)
//
// Expected call edges:
//   clamp(int, int, int) → Math.max()  (standard library)
//   clamp(int, int, int) → Math.min()  (standard library)
//
// ---

public class Helpers {

    public static final int MAX_VALUE = 1000;
    public static final int MIN_VALUE = 0;
    public static final String DEFAULT_NAME = "Unknown";

    public static int add(int a, int b) {
        return a + b;
    }

    public static double add(double a, double b) {
        return a + b;
    }

    public static int subtract(int a, int b) {
        return a - b;
    }

    public static int clamp(int value, int min, int max) {
        return Math.min(Math.max(value, min), max);
    }

    public static String formatName(String first, String last) {
        return first + " " + last;
    }

    public static int sum(int... numbers) {
        int total = 0;
        for (int n : numbers) {
            total += n;
        }
        return total;
    }

    public static <T> T firstOrDefault(T[] items, T defaultValue) {
        return items.length > 0 ? items[0] : defaultValue;
    }
}
