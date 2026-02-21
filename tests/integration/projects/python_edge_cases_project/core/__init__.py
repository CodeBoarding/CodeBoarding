# --- Static Analysis Expected ---
#
# Defined entities: none (re-export only)
# Re-exports: Animal, Dog, Cat, SwimmingMixin from core.base;
#             UserProfile, Config from core.models
# Expected call edges: none
# Corner cases: re-exports — symbols should be attributed to their source files,
#   not to this __init__.py
# Package: core
# ---
from core.base import Animal, Dog, Cat, SwimmingMixin
from core.models import UserProfile, Config
