# AI Agent Code Review Guidelines

**Purpose:** This document serves as a comprehensive checklist for AI agents to evaluate code changes before considering work complete. Follow these guidelines rigorously to ensure all changes meet production-quality standards.

**When to Use:** Use this checklist when reviewing code changes on your current branch against the `main` branch. Run through this checklist before marking tasks as complete or creating pull requests.

---

## 1. Logic & Architecture

### Code Reuse & DRY Principle
- [ ] **No duplicated logic:** Check for copy-pasted code blocks that could be extracted into shared functions/classes
- [ ] **Existing utilities used:** Verify you're using existing helper functions from `utils.py`, `repo_utils.py`, or other shared modules
- [ ] **Consistent patterns:** Follow existing architectural patterns (e.g., LSP client implementations, analysis pipeline stages)
- [ ] **No leaky abstractions:** Implementation details shouldn't bleed through abstraction layers

### Architecture Validation
- [ ] **Single Responsibility:** Each function/class has one clear purpose
- [ ] **Dependency direction:** Dependencies flow inward (high-level → low-level), no circular dependencies
- [ ] **Interface consistency:** Public APIs match existing patterns in the codebase
- [ ] **No god objects:** Classes don't accumulate unrelated responsibilities

### Design Patterns
- [ ] **Factory pattern:** Used appropriately for object creation (e.g., LSP clients)
- [ ] **Strategy pattern:** Different algorithms implement same interface
- [ ] **Pipeline pattern:** Analysis stages follow ProjectScanner → StaticAnalyzer → OutputGenerator flow
- [ ] **Observer pattern:** Events/notifications use proper callback mechanisms

---

## 2. Clean Code Standards

### Comments & Documentation
- [ ] **No redundant comments:** Comments explain "why" not "what" (code should be self-documenting)
- [ ] **TODO/FIXME tracked:** Any temporary comments have corresponding tickets/tasks
- [ ] **Type hints:** All function parameters and return types are annotated
- [ ] **Comments are concise:** No multi-paragraph explanations of simple decisions (see §10)
- [ ] **Docstrings are short:** One-line summary + minimal `Why:` when non-obvious. Avoid duplicating the code in prose.
- [ ] **No historical narrative:** Don't explain what the code *used to do*, what bug this fixes, or what "V4" / "Bug C" is — that belongs in the commit message, not the source
- [ ] **No line-number references to external files:** e.g. "see lines ~1116-1133 of nodeenv.py" — those rot the moment upstream changes

### Code Clarity
- [ ] **Descriptive naming:** Variable/function names clearly indicate purpose (avoid `data`, `temp`, `x`)
- [ ] **Early returns:** Use guard clauses instead of deep nesting
- [ ] **Function length:** Functions under 50 lines (preferably under 30)
- [ ] **Cyclomatic complexity:** No excessive branching (keep complexity < 10)

### Dead Code Removal
- [ ] **No unused imports:** All imports are actively used
- [ ] **No unused variables:** No linter warnings about unused variables
- [ ] **No commented-out code:** Delete don't comment out
- [ ] **No debug prints:** Remove `print()` statements used for debugging
- [ ] **No backwards compatibility shims:** Remove migration code after migration complete

---

## 3. Import Standards

### Import Style (Strict)
- [ ] **Top-level imports only:** All imports at file top, never inside functions/classes
  ```python
  # ❌ BAD - function-level import
  def process():
      import json
      return json.loads(data)
  
  # ✅ GOOD - module-level import
  import json
  
  def process():
      return json.loads(data)
  ```

- [ ] **Absolute imports for cross-module:** Use absolute imports when importing from other packages
  ```python
  # ✅ GOOD - absolute import for packages
  from static_analyzer.lsp_client import BaseLSPClient
  
  # ❌ BAD - relative imports
  from .conftest import load_fixture
  ```

- [ ] **No star imports:** Explicit imports only (`from module import specific_thing`)
- [ ] **Import ordering:** Standard library → third-party → local (alphabetical within groups)

For circular dependencies, try to redesign the approach to not have a circular dependency in the first place. 

### Exceptions for Function-Level Imports
Only allow function-level imports if:
- [ ] **Heavy module loading:** Module takes >100ms to import and is rarely used
- [ ] **Optional dependencies:** Import may fail (feature not installed)
- [ ] **Tree-shaking:** Large library where only specific exports are needed

**Document the exception:** Add comment explaining why function-level import is necessary
```python
def analyze():
    # Import here to avoid circular dependency with diagrams module
    from diagrams import Diagram
    ...
```

---

## 4. Testing Integrity

### Test Coverage Requirements
- [ ] **New code covered:** All new functions have corresponding tests
- [ ] **Minimum 80% coverage:** Project enforces 80% coverage threshold
- [ ] **Edge cases tested:** Null inputs, empty collections, boundary values
- [ ] **Error paths tested:** Exceptions and error conditions are validated

### Test Quality
- [ ] **Atomic tests:** One test, one assertion (or one logical concept)
  ```python
  # ❌ BAD - multiple assertions, hard to identify what failed
  def test_user():
      user = create_user()
      assert user.name == "John"
      assert user.email == "john@example.com"
      assert user.age == 30
  
  # ✅ GOOD - parametrize for multiple similar assertions
  @pytest.mark.parametrize("field,value", [
      ("name", "John"),
      ("email", "john@example.com"),
      ("age", 30),
  ])
  def test_user_field(field, value):
      user = create_user()
      assert getattr(user, field) == value
  ```

- [ ] **No duplicate tests:** Same logic not tested in multiple test files
- [ ] **Descriptive test names:** Test names explain the scenario being tested
- [ ] **Given-When-Then structure:** Tests follow arrange-act-assert pattern

### Test Independence
- [ ] **No shared state:** Tests don't depend on execution order
- [ ] **Proper fixtures:** Use pytest fixtures for setup/teardown
- [ ] **Temp directories:** Use `temp_workspace` fixture for file operations
- [ ] **Mock external calls:** Database, HTTP, LSP servers are mocked in unit tests

### Integration Tests
- [ ] **Real scenarios:** Integration tests use actual (not mocked) components where appropriate
- [ ] **Performance baselines:** Execution time assertions have reasonable tolerances (10-20%)
- [ ] **Isolation:** Tests don't interfere with each other (unique temp dirs, ports)

---

## 5. Robustness & Error Handling

### Input Validation
- [ ] **Type checking:** Inputs validated (use mypy for static, assertions for runtime)
- [ ] **Null/None checks:** Handle missing or null inputs gracefully
- [ ] **Empty collections:** Handle empty lists/dicts/strings appropriately
- [ ] **Range validation:** Numeric inputs checked for valid ranges

### Error Handling
- [ ] **Specific exceptions:** Catch specific exceptions, not bare `except:`
  ```python
  # ❌ BAD
  try:
      process()
  except:
      pass
  
  # ✅ GOOD
  try:
      process()
  except ValueError as e:
      logger.error(f"Invalid value: {e}")
      raise ProcessingError("Failed to process") from e
  ```

- [ ] **Exception chaining:** Use `raise ... from e` to preserve stack traces
- [ ] **Resource cleanup:** Use context managers (`with` statements) for file/network resources
- [ ] **Timeout handling:** All network/file operations have timeouts
- [ ] **Graceful degradation:** System continues working if non-critical components fail

### Defensive Programming
- [ ] **Fail fast:** Validate inputs at function entry
- [ ] **Assertions for invariants:** Use `assert` for conditions that should never happen
- [ ] **Immutable defaults:** Never use mutable default arguments
  ```python
  # ❌ BAD
  def process(items=[]):
      items.append("item")
      return items
  
  # ✅ GOOD
  def process(items=None):
      if items is None:
          items = []
      items.append("item")
      return items
  ```

---

## 6. Performance & Security

### Performance
- [ ] **No O(n²) loops:** Check for nested loops over same collection
- [ ] **Efficient data structures:** Use sets for lookups, not lists
- [ ] **Lazy loading:** Large datasets loaded on demand, not upfront
- [ ] **Caching:** Expensive operations cached appropriately (use `@lru_cache` or project cache)
- [ ] **Resource limits:** Bounded queues, limited thread pools

### Memory Management
- [ ] **Streaming for large files:** Use iterators, not loading entire files into memory
- [ ] **Context managers:** Files and network connections properly closed
- [ ] **No memory leaks:** Caches have size limits and TTL

### Security Checklist
- [ ] **No hardcoded secrets:** No API keys, passwords, tokens in code
- [ ] **No SQL injection:** All SQL queries use parameterized statements
- [ ] **No command injection:** Shell commands properly escaped (use lists, not strings)
- [ ] **Input sanitization:** User inputs validated/sanitized before processing
- [ ] **Path traversal prevention:** File paths validated (use `Path.resolve()`)
- [ ] **Secure defaults:** Security features enabled by default (not opt-in)

### Sensitive Data Handling
- [ ] **No PII in logs:** User data, emails, names not logged
- [ ] **No secrets in error messages:** API keys not included in exceptions
- [ ] **Environment variables:** Configuration loaded from env, not hardcoded
- [ ] **.env file ignored:** Secrets not committed to git (check `.gitignore`)

---

## 7. Python-Specific Standards

### Type Hints (Strict)
- [ ] **Function signatures:** All parameters and return types annotated
- [ ] **Generic types:** Use `list[str]` not `List[str]` (Python 3.12+)
- [ ] **Optional types:** Use `str | None` not `Optional[str]`
- [ ] **Complex types:** Define type aliases for complex signatures
  ```python
  # ✅ GOOD
  from typing import TypeAlias
  
  JsonDict: TypeAlias = dict[str, Any]
  HandlerFunc: TypeAlias = Callable[[Request], Response]
  ```

### Python Patterns
- [ ] **Pathlib usage:** Use `Path` objects, not string paths
- [ ] **F-strings:** Use f-strings for string formatting (not `%` or `.format()`)
- [ ] **Comprehensions:** Use list/dict comprehensions where readable
- [ ] **Dataclasses:** Use `@dataclass` for data containers
- [ ] **Enums:** Use `Enum` for fixed sets of constants

### Async Code (if applicable)
- [ ] **Consistent async:** Don't mix sync and async in same function
- [ ] **Proper awaiting:** All coroutines awaited
- [ ] **Context managers:** Use `async with` for async resources
- [ ] **Task management:** Tasks properly awaited/cancelled

---

## 8. Project-Specific Standards

### CodeBoarding Conventions
- [ ] **LSP client pattern:** New language servers follow `BaseLSPClient` pattern
- [ ] **Analysis pipeline:** Changes fit into Scanner → Analyzer → Generator flow
- [ ] **Configuration:** New settings added to `static_analysis_config.yml`
- [ ] **Logging:** Use `logging_config.py` setup, not ad-hoc print statements
- [ ] **Error taxonomy:** New errors inherit from appropriate base exception

### File Organization
- [ ] **Correct directory:** Code placed in appropriate functional directory
  - `agents/` - LLM agent implementations
  - `static_analyzer/` - Static analysis code
  - `output_generators/` - Output format generators
  - `monitoring/` - Metrics and monitoring
- [ ] **Module naming:** File names match contained functionality
- [ ] **Test location:** Tests mirror source structure under `tests/`

### Dependencies
- [ ] **pyproject.toml:** New dependencies added to appropriate group
- [ ] **Version pinning:** Specific versions specified for reproducibility
- [ ] **Minimal dependencies:** Only add what's necessary

---

## 9. Documentation

### Code Documentation
- [ ] **Module docstrings:** Each file has module-level docstring
- [ ] **Function docstrings:** Public functions documented (Google style)
- [ ] **Complex logic explained:** Non-obvious code has inline comments
- [ ] **Type hints as docs:** Complex types have explanatory aliases

### User Documentation
- [ ] **README updated:** If adding features, update relevant README sections
- [ ] **Usage examples:** New features have usage examples
- [ ] **Breaking changes:** Document any API changes or migrations
- [ ] **AGENTS.md updated:** If adding new conventions, document for future agents

### Changelog
- [ ] **CHANGELOG.md:** Significant changes documented
- [ ] **Version bump:** If adding features, version incremented appropriately

---

## 10. Review Red Flags

Watch for these patterns that indicate deeper issues:

### 🚩 Architectural Red Flags
- **Shotgun surgery:** Same change needed in multiple places
- **Divergent change:** One module changed for many different reasons
- **Feature envy:** Function uses more data from other classes than its own
- **Data clumps:** Same groups of variables passed together repeatedly

### 🚩 Code Smells
- **Primitive obsession:** Using primitives instead of domain types
- **Switch statements:** Long if/elif chains (use polymorphism)
- **Temporary field:** Instance variables only used in specific methods
- **Refused bequest:** Subclass ignores inherited methods

### 🚩 Testing Red Flags
- **Fragile tests:** Tests break on unrelated changes
- **Slow tests:** Tests taking > 1 second (should be unit tests)
- **Mystery guest:** Test relies on external data not visible in test
- **Happy path only:** No error condition tests

### 🚩 Comment & Docstring Bloat (critical — recent recurring issue)

Verbose commentary costs context budget every time an agent reads the file and
buries the actual logic. The rule: if the prose is longer than the code it
documents, you're probably writing it wrong. Watch for:

- **Multi-paragraph function docstrings** for a 10-line function. A single
  summary sentence is almost always enough; add a short `Why:` only when the
  behavior is genuinely non-obvious.
- **Narrating the diff:** "Previously this only checked tokei...", "The pre-fix
  behavior would have...", "This is the Phase-2 fix for Bug C." The code is
  what it *is now* — the reader has `git blame` for history.
- **Bug-ticket names in source:** "the V4 scenario", "Bug C reproducer",
  "the original issue". These are meaningless six months later and leak
  private tracker vocabulary into the repo.
- **Tutorial-style docstrings on test methods:** Test names should already say
  what's being tested. A docstring that re-states the assertion in English is
  pure overhead.
- **Line-number references to other files** (especially third-party ones):
  "mirrors nodeenv.main() at lines ~1116-1133". Guaranteed to be wrong on the
  next upstream bump.
- **Docstrings restating the type hints:** "``repo: GitHub owner/repo string``"
  when the parameter is already `repo: str`.
- **Duplicated module-level preamble explaining the package layout** in every
  submodule. Put it once in the package `__init__.py` (or a design doc) — not
  re-pasted into each file.
- **"Defensive" explanations of single-line obvious code.** `if not path: return None`
  does not need three sentences explaining what None means.

The goal isn't zero comments — it's comments that *earn their line count*.
When in doubt: delete the prose, keep the code, and if a reviewer is confused
they'll ask. Then you know which sentence was actually load-bearing.

---

## 11. Quick Reference: Common Issues

### Issue: Mutable Default Arguments
**Solution:** Use `None` as default, initialize inside function

### Issue: Bare Except Clauses
**Solution:** Catch specific exceptions, log and re-raise

### Issue: Missing Type Hints
**Solution:** Add comprehensive type annotations, run mypy

### Issue: Import Side Effects
**Solution:** Move initialization to functions, use `if __name__ == "__main__"`

### Issue: Resource Leaks
**Solution:** Use context managers (`with` statements)

### Issue: Inconsistent Error Handling
**Solution:** Define custom exception hierarchy, use consistently

---

## Review Completion Checklist

After making changes, verify:

- [ ] All items in Sections 1-8 reviewed and addressed
- [ ] No red flags from Section 10
- [ ] Changes are better than what was there before
- [ ] You would be comfortable explaining these changes in a code review meeting
- [ ] Documentation is updated for any new patterns or conventions

---

**Remember:** The goal is not just working code, but maintainable, well-architected code that follows project conventions. When in doubt, favor clarity over cleverness, and consistency over novelty.

**Quality Standard:** Would a Senior Staff Engineer approve this code?
