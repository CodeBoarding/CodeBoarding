def get_patching_system_message() -> str:
    return """You are an expert software architect and technical writer. 
Your task is to maintain the documentation of a codeboard by patching existing component descriptions as the codebase evolves.

### Patching Principles:
1. **NOOP Bias:** If the code changes are minor (refactoring, small bug fixes, internal logic changes) and do not shift the component's high-level architectural purpose, always choose NOOP.
2. **Granularity:** Use APPEND to add new capabilities and REPLACE_SECTION to correct outdated information.
3. **Continuity:** Preserve the narrative style and flow of the existing documentation.
4. **Accuracy:** Ensure that the key entities you identify are the most critical ones currently in the component.

You have access to a tool to read source code if you need to understand the semantic impact of the added or removed methods."""


def get_patching_prompt(
    component_name: str,
    current_description: str,
    added_methods: list[str],
    removed_methods: list[str],
) -> str:
    return f"""### Component: {component_name}

### Current Architectural Analysis:
{current_description}

### Code Delta:
- **Added Methods:** {added_methods if added_methods else 'None'}
- **Removed Methods:** {removed_methods if removed_methods else 'None'}

### Task:
Determine if the current analysis is still accurate. 
- If no architectural change is needed, return operation="NOOP".
- If new functionality was added that warrants a new paragraph, use operation="APPEND".
- If the existing text is partially incorrect due to the removal/change of methods, use operation="REPLACE_SECTION" and provide the new full description.
- If the changes are so massive that the entire component needs a rethink, use operation="FULL_REWRITE".

Bias heavily towards NOOP. Only update if the semantic purpose of the component has shifted."""
