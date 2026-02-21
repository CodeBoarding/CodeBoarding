package core;

// --- Static Analysis Expected ---
//
// Defined entities:
//   Record (1): UserProfile
//   Record components (2): name, email
//   Methods (1): displayName()
//
// Edge cases covered:
//   - Java record (auto-generated constructor, accessors, equals, hashCode, toString)
//   - Explicit method on a record
//   - Calling auto-generated accessor methods (name(), email())
//
// Expected call edges:
//   displayName() → name()   (auto-generated record accessor)
//   displayName() → email()  (auto-generated record accessor)
//
// ---

public record UserProfile(String name, String email) {

    public String displayName() {
        return name() + " <" + email() + ">";
    }
}
