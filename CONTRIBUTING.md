# CONTRIBUTING.md

CodeBoarding is an early-stage project, and we love contributors! :heart:

---

## 1) Before you start
- Check existing issues or open a new one to discuss your idea.
- Small, focused changes are preferred.
- Be kind and constructive in discussions (Discord welcome).

---

## 2) What to work on
- Bug fixes
- Features and enhancements
- Language support and static analysis improvements
- Docs and examples
- Performance and stability (LLM prompting mostly)

---

## 3) Code style (quick)
- **Naming:** variables use `snake_case`; classes use `PascalCase`.
- **Formatting:** We use Black formatter (line length: 120).
- Add type hints if possible.

---

## 4) Development setup (for contributors)

For active contributors who want to modify the code:

```bash
# Install with dev dependencies (includes Black formatter and pre-commit)
uv sync --dev

# Activate virtual environment
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Run setup (this will automatically install pre-commit hooks if available)
python setup.py
```

The pre-commit hooks will automatically format your code with Black before each commit.

For regular users who just want to run the analysis, use `uv sync --frozen` instead (see README).

---

## 5) How to PR
- Fork the repo.
- Create a branch: `feat/...`, `fix/...`, or `docs/...`.
- No tests or linters yet (yaaayy)
- In the PR, explain **WHAT/WHY/HOW**: what changed, why it's useful, and how you tested it.
- Optional: add a picture of a diagram from your favorite project.
