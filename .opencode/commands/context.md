---
description: Create a focused CodeBoarding context for a specific area of the codebase
---

Create a focused CodeBoarding context for: $ARGUMENTS

1. **Find related files** — explore the codebase to discover all files relevant to the user's description.
   - Search by keywords, imports, naming conventions, directory structure
   - Use glob and grep to find matches across the repo
   - Be thorough — include helpers, types, config files that the core files depend on

2. **Propose the file list** — show the user the files you found.
   - Group by directory for readability
   - Suggest a context name (slugified from the description, e.g. "auth flow" → "auth-flow")
   - Let the user add/remove files before proceeding

3. **Check for existing context** — if a context with that name already exists:
   - Show the user the current `.codeboarding-include` contents
   - Ask if they want to overwrite it or append the new patterns
   - If overwrite, proceed with replacement; if append, merge patterns (deduplicate)

4. **Create/update the context** — once confirmed:
   - Derive the repo root (look for `.codeboarding/` or use current directory)
   - Run: `codeboarding context --local <repo-root> create <context-name>` (skip if exists and appending)
   - Write the confirmed file list (one per line, gitignore patterns) to `.codeboarding/contexts/<context-name>/.codeboarding-include`

5. **Switch to the context**:
   - Run: `codeboarding context --local <repo-root> set <context-name>`

6. **Ask before analyzing**:
   - Show the user the active context and include patterns
   - Ask: "Run full analysis now? (y/n)"
   - If yes: `codeboarding full --local <repo-root>`
   - If no: tell them how to run it later

7. **Report results** (if analysis ran):
   - Show how many files were included vs excluded
   - Summarize what components were generated
   - Point to the output in `.codeboarding/`

**Include file format** (gitignore-compatible patterns):
```
src/auth/**/*.ts
src/middleware.ts
lib/crypto.ts
```

**Rules:**
- One pattern per line
- Use `**` for recursive directory matching
- Always include transitive dependencies (types, utils, config) of the core files
- Never include `node_modules/`, `dist/`, `build/`, `.git/`
