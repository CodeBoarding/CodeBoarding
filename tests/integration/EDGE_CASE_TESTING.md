# Edge Case Integration Testing

End-to-end integration tests that run the real StaticAnalyzer (scanner + Pyright LSP) against
purpose-built dummy Python projects, validate all outputs against human-derived expectations,
and repeat multiple times to verify deterministic behavior.

---

## Overview

```
tests/integration/
├── test_edge_cases.py                  # Test runner
├── generate_scaled_project.py          # Scale-up generator
├── fixtures/
│   ├── python_edge_cases.json          # Fixture for base project (hand-written)
│   └── python_scaled.json              # Fixture for scaled project (auto-generated)
└── projects/
    ├── python_edge_cases_project/      # Base project (~10 files, hand-written)
    └── python_scaled_project/          # Scaled project (~1000 files, auto-generated)
```

## What the test validates

Each run of the analyzer produces results that are checked against the fixture:

1. **Language detection** — Python is detected
2. **References** — all expected symbol references exist (lowercase dotted keys like `core.base.animal`)
3. **Class hierarchy** — all expected classes appear
4. **Inheritance** — superclass/subclass relationships match (`Dog → Animal`, `Duck → Animal + SwimmingMixin`)
5. **Call graph edges** — expected caller→callee edges are present
6. **Package dependencies** — import/imported_by relationships between packages
7. **Source files** — all expected `.py` files are discovered
8. **Stability** — metrics are identical across repeated runs

---

## Step 1: The Base Project

### Structure

The hand-written base project lives at `tests/integration/projects/python_edge_cases_project/`:

```
python_edge_cases_project/
├── .git/                    # Must be a git repo for the analyzer
├── core/
│   ├── __init__.py          # Re-exports (Animal, Dog, Cat, UserProfile, Config)
│   ├── base.py              # ABC, single/multiple inheritance, mixin, static/classmethod
│   ├── models.py            # @dataclass, nested class, @property + setter, __eq__/__hash__
│   ├── services.py          # Decorator, closure, lambda, cross-module calls
│   └── pipeline.py          # Builder pattern, dict dispatch, map/filter, nested calls
├── utils/
│   ├── __init__.py          # Re-exports (add, clamp)
│   └── helpers.py           # Leaf functions, module-level constants
├── unused/
│   ├── __init__.py          # Empty init
│   └── dead_code.py         # Unreferenced class, function, constant
└── main.py                  # Entry point calling everything
```

### Corner cases covered

| Category | Patterns |
|---|---|
| **Inheritance** | ABC, single inheritance, multiple inheritance, mixin |
| **Class features** | `@dataclass`, nested class, `@property` + setter, `@staticmethod`, `@classmethod` |
| **Magic methods** | `__init__`, `__str__`, `__repr__`, `__eq__`, `__hash__` |
| **Functions** | Decorator (`log_call`), closure (`make_multiplier`), lambda (`sorter`) |
| **Call patterns** | Dict-based dispatch, method chaining (builder), chained attribute calls |
| **Argument passing** | `map`/`filter` with callables, `*args`/`**kwargs` forwarding |
| **Control flow** | Generator pipeline, nested function calls, conditional call chains |
| **Cross-module** | Calls spanning `core.base`, `core.services`, `core.pipeline`, `utils.helpers` |
| **Dead code** | Entirely unreferenced module (`unused/dead_code.py`) |
| **Re-exports** | `__init__.py` re-exporting symbols from submodules |

### Writing expectations

Each source file has a header comment documenting what a correct analyzer should find.
These are **human-derived** — not copied from LSP output. Example from `base.py`:

```python
# --- Static Analysis Expected ---
#
# Defined entities:
#   Classes (5): SwimmingMixin, Animal, Dog, Cat, Duck
#   Methods (8): SwimmingMixin.swim, Animal.__init__, Animal.speak, ...
#
# Expected call edges (from method bodies):
#   __repr__  → __str__   (self.__str__())
#   create    → Dog       (cls(name) — classmethod constructor call)
#   actions   → speak     (self.speak())
#   actions   → swim      (self.swim())
#
# Class hierarchy:
#   Animal(ABC)              — abstract base, superclass of Dog, Cat, Duck
#   Duck(Animal, SwimmingMixin) — multiple inheritance
# ---
```

### The fixture file

`tests/integration/fixtures/python_edge_cases.json` — hand-written, maps expectations to
the format the test runner checks:

```json
{
  "language": "Python",
  "sample_references": [
    "core.base.animal",       // lowercase dotted name
    "core.base.dog",
    "utils.helpers.add",
    "main.main"
  ],
  "sample_classes": [
    "core.base.Animal",       // case-preserved for hierarchy
    "core.base.Dog"
  ],
  "sample_hierarchy": {
    "core.base.Dog": {
      "superclasses_contain": ["core.base.Animal"]
    }
  },
  "sample_edges": [
    ["main.main", "core.services.create_dog"],    // [caller, callee]
    ["core.base.__repr__", "core.base.__str__"]
  ],
  "sample_package_deps": {
    "core": { "imports_contain": ["utils"] },
    "utils": { "imported_by_contain": ["core"] }
  },
  "expected_source_files_contain": [
    "core/base.py",
    "main.py"
  ]
}
```

Key: all fixture checks are **containment** checks (subset, not equality). The analyzer
may find more than what's listed — we only assert the expected items are present.

### Making the project a git repo

The StaticAnalyzer requires a git repository. The base project has a `.git/` directory
committed within the test tree. If you need to recreate it:

```bash
cd tests/integration/projects/python_edge_cases_project
git init && git add . && git commit -m "init"
```

### Running the base test

```bash
STATIC_ANALYSIS_CONFIG=static_analysis_config.yml \
  uv run pytest tests/integration/test_edge_cases.py -k python_edge_cases -m integration -v -s
```

The test runs 10 stability runs by default.

---

## Step 2: Scaling Up (100×)

### How it works

The generator (`generate_scaled_project.py`) takes the base project and duplicates it
N times with simple string replacement:

```
Base project copy i:
  core/       → core_{i:02d}/        (core_00, core_01, ..., core_99)
  utils/      → utils_{i:02d}/       (utils_00, utils_01, ..., utils_99)
  unused/     → unused_{i:02d}/      (unused_00, unused_01, ..., unused_99)
  main.py     → entry_{i:02d}.py     (entry_00.py, entry_01.py, ..., entry_99.py)
```

All imports are rewritten: `from core.base import Dog` → `from core_00.base import Dog`.

A **mega `main.py`** ties everything together:

```python
from entry_00 import main as main_00
from entry_01 import main as main_01
...

def main():
    main_00()
    main_01()
    ...
```

The fixture JSON is auto-generated by applying the same renaming to the base fixture,
plus adding edges from `main.main` → each `entry_XX.main`.

### Scale comparison

|  | Base (hand-written) | Scaled (100 copies) |
|---|---|---|
| Python files | 10 | 1,001 |
| Packages | 4 | 301 |
| Expected references | 63 | 5,901 |
| Expected classes | 10 | 1,000 |
| Expected edges | 34 | 3,500 |
| Stability runs | 10 | 2 |

### Generating the scaled project

```bash
# Default: 100 copies (~1000 files)
uv run python tests/integration/generate_scaled_project.py

# Custom: 50 copies (~500 files)
uv run python tests/integration/generate_scaled_project.py --copies 50
```

This creates:
- `tests/integration/projects/python_scaled_project/` — the project (git-initialized)
- `tests/integration/fixtures/python_scaled.json` — the fixture

### Running the scaled test

```bash
STATIC_ANALYSIS_CONFIG=static_analysis_config.yml \
  uv run pytest tests/integration/test_edge_cases.py -k python_scaled -m integration -v -s
```

### Regenerating after base project changes

If you modify the base project or its fixture, regenerate the scaled version:

```bash
# 1. Update the base project files in python_edge_cases_project/
# 2. Update the base fixture in fixtures/python_edge_cases.json
# 3. Regenerate:
uv run python tests/integration/generate_scaled_project.py
```

The scaled project and fixture are derived entirely from the base — you never edit them
directly.

---

## Adding a New Language

To add edge case testing for another language (e.g., TypeScript):

1. Create `tests/integration/projects/typescript_edge_cases_project/` with source files
   and a `.git/` init
2. Create `tests/integration/fixtures/typescript_edge_cases.json` with expected results
3. Add an entry to `EDGE_CASE_PROJECTS` in `test_edge_cases.py`:

```python
EdgeCaseProject(
    name="typescript_edge_cases",
    project_dir="typescript_edge_cases_project",
    language="TypeScript",
    fixture_file="typescript_edge_cases.json",
),
```

The same test runner validates all languages — only the fixture format matters.
