# --- Static Analysis Expected ---
#
# Defined entities:
#   Classes (1): OrphanClass
#   Methods (1): OrphanClass.orphan_method
#   Functions (1): never_called
#   Constants (1): UNUSED_CONSTANT
#
# Expected call edges: none (no function here calls another project function)
#
# Class hierarchy:
#   OrphanClass — standalone, no superclass, no subclass
#
# Corner cases: entirely unreferenced module — nothing in the project imports or
#   calls any symbol defined here. Tests unused code / dead code detection.
# Package: unused | no imports, not imported by anyone
# ---
class OrphanClass:
    def orphan_method(self):
        return "never called"


def never_called():
    return 42


UNUSED_CONSTANT = "unused"
