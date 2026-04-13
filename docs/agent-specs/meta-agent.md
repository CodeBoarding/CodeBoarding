# MetaAgent -- Technical Spec Sheet

| Field | Value |
|-------|-------|
| Class | `MetaAgent` |
| File | `agents/meta_agent.py` |
| Base class | `CodeBoardingAgent` (`agents/agent.py:34`) |
| LLM steps | 1 (`analyze_project_metadata`) |
| Cache layer | `MetaCache` (`caching/meta_cache.py:40`) |

---

## System Prompt

Defined as the constant `SYSTEM_META_ANALYSIS_MESSAGE` at `agents/prompts/claude_prompts.py:186`.

```text
You extract architectural metadata from projects.

<instructions>
1. Start by examining available project context and structure
2. You MUST use readDocs to analyze project documentation when available
3. You MUST use getFileStructure to understand project organization
4. Identify project type, domain, technology stack, and component patterns to guide analysis
5. Focus on patterns that will help new developers understand the system architecture
</instructions>

<thinking>
The goal is to provide architectural context that guides the analysis process and helps create documentation that new team members can quickly understand.
</thinking>
```

Passed to the base class `__init__` which wraps it in a `SystemMessage` at `agents/agent.py:58`.

---

## User Prompt (templates)

Defined as the constant `META_INFORMATION_PROMPT` at `agents/prompts/claude_prompts.py:200`.

**Template** (single variable `{project_name}`):

```text
Analyze project '{project_name}' to extract architectural metadata for comprehensive analysis optimization.

<context>
The goal is to understand the project deeply enough to provide architectural guidance that helps new team members understand the system's purpose, structure, and patterns within their first week.
</context>

<instructions>
1. You MUST use readDocs to examine project documentation (README, setup files) to understand purpose and domain
2. You MUST use getFileStructure to examine file structure and identify the technology stack
3. You MUST use readExternalDeps to identify dependency files and frameworks used
4. Apply architectural expertise to determine patterns and expected component structure
5. Focus on insights that guide component identification, flow visualization, and documentation generation
</instructions>

<thinking>
Required analysis outputs:
1. **Project Type**: Classify the project category (web framework, data processing library, ML toolkit, CLI tool, etc.)
2. **Domain**: Identify the primary domain/field (web development, data science, DevOps, AI/ML, etc.)
3. **Technology Stack**: List main technologies, frameworks, and libraries used
4. **Architectural Patterns**: Identify common patterns for this project type (MVC, microservices, pipeline, etc.)
5. **Expected Components**: Predict high-level component categories typical for this project type
6. **Architectural Bias**: Provide guidance on how to organize and interpret components for this specific project type
</thinking>
```

The template is instantiated at `agents/meta_agent.py:34-36` via `PromptTemplate(template=..., input_variables=["project_name"])` and rendered at runtime with `self.meta_analysis_prompt.format(project_name=self.project_name)` (`agents/meta_agent.py:55`).

**Synthetic example** (illustrative, not from a real run):

```text
Analyze project 'fastapi-todo-app' to extract architectural metadata for comprehensive analysis optimization.
...
```

Where `{project_name}` is replaced by the value passed to `MetaAgent.__init__`.

---

## Tools

MetaAgent restricts the base toolkit to exactly 4 tools, registered at `agents/meta_agent.py:40-45`:

| # | Tool name | Class | File | Signature |
|---|-----------|-------|------|-----------|
| 1 | `readDocs` | `ReadDocsTool` | `agents/tools/read_docs.py:22` | `_run(file_path: str \| None = None, line_number: int = 0) -> str` |
| 2 | `readFile` | `ReadFileTool` | `agents/tools/read_file.py:19` | `_run(file_path: str, line_number: int) -> str` |
| 3 | `readExternalDeps` | `ExternalDepsTool` | `agents/tools/get_external_deps.py:15` | `_run() -> str` |
| 4 | `getFileStructure` | `FileStructureTool` | `agents/tools/read_file_structure.py:22` | `_run(dir: str) -> str` |

All tools inherit from `BaseRepoTool` (`agents/tools/base.py`) which injects `RepoContext` (repo directory, ignore manager, static analysis results).

---

## Output Schema

Defined as `MetaAnalysisInsights` at `agents/agent_responses.py:368`.

```json
{
  "type": "object",
  "properties": {
    "project_type": {
      "type": "string",
      "description": "Type/category of the project (e.g., web framework, data processing, ML library, etc.)"
    },
    "domain": {
      "type": "string",
      "description": "Domain or field the project belongs to (e.g., web development, data science, DevOps, etc.)"
    },
    "architectural_patterns": {
      "type": "array",
      "items": { "type": "string" },
      "description": "Main architectural patterns typically used in such projects"
    },
    "expected_components": {
      "type": "array",
      "items": { "type": "string" },
      "description": "Expected high-level components/modules based on project type"
    },
    "technology_stack": {
      "type": "array",
      "items": { "type": "string" },
      "description": "Main technologies, frameworks, and libraries used"
    },
    "architectural_bias": {
      "type": "string",
      "description": "Guidance on how to interpret and organize components for this project type"
    }
  },
  "required": [
    "project_type",
    "domain",
    "architectural_patterns",
    "expected_components",
    "technology_stack",
    "architectural_bias"
  ]
}
```

`MetaAnalysisInsights` extends `LLMBaseModel` (`agents/agent_responses.py:14`), which provides `llm_str()` and `extractor_str()` used during parsing.

---

## Validators

MetaAgent does **not** apply any domain validators. There is no call to `_validation_invoke` anywhere in `MetaAgent`; the single LLM step calls `_parse_invoke` directly (`agents/meta_agent.py:61`).

Why: MetaAgent produces free-form architectural metadata (project type, domain, technology stack, etc.). There is no ground-truth reference data (such as cluster IDs or component names) to validate against. The validators defined in `agents/validation.py` (`validate_cluster_coverage`, `validate_group_name_coverage`, `validate_key_entities`, `validate_relation_component_names`, `validate_file_classifications`) all require a `ValidationContext` populated with CFG cluster results or static analysis artifacts that are not available at the meta-analysis stage, which runs before any of those artifacts exist.

The only structural validation applied is the Pydantic model parse inside `_parse_response` (`agents/agent.py:338`), which enforces the 6-field schema via `MetaAnalysisInsights.model_validate()`.

---

## Deterministic Pre-processing

Before the LLM call in `analyze_project_metadata` (`agents/meta_agent.py:51-60`):

1. **Prompt rendering** (`agents/meta_agent.py:55`): `self.meta_analysis_prompt.format(project_name=self.project_name)` fills the single `{project_name}` variable into the `META_INFORMATION_PROMPT` template.

2. **Cache key construction** (`agents/meta_agent.py:56`): `self._meta_cache.build_key(prompt, self.agent_llm, self.parsing_llm)` builds a `MetaCacheKey` (`caching/meta_cache.py:29-37`) that hashes together:
   - The rendered prompt text
   - Agent model name and settings (temperature, top_p, etc.)
   - Parsing model name and settings
   - A sorted list of "metadata files" (dependency manifests + README) discovered by `discover_metadata_files()` (`caching/meta_cache.py:57-69`)
   - A SHA-256 content hash of those files (`_compute_metadata_content_hash`, `caching/meta_cache.py:96-111`)

3. **Cache lookup** (`agents/meta_agent.py:59`): If `cache_key` is not `None` and `skip_cache` is `False`, `self._meta_cache.load(cache_key)` checks the SQLite-backed cache. On hit, the cached `MetaAnalysisInsights` is returned immediately, skipping the LLM entirely.

4. **Toolkit initialization** (constructor, `agents/meta_agent.py:38-46`): The langgraph agent is created with exactly 4 tools (`read_docs`, `read_file`, `external_deps`, `read_file_structure`). An empty `StaticAnalysisResults()` is passed to the base class since no static analysis exists yet at this stage (`agents/meta_agent.py:28`).

---

## Deterministic Post-processing

After the LLM returns in `analyze_project_metadata` (`agents/meta_agent.py:61-66`):

1. **Agent response extraction** (`agents/agent.py:96-178`): `_invoke` runs the langgraph agent with system + human messages, extracts the last `AIMessage`, and returns its content as a string. Includes retry logic with exponential backoff (up to 5 retries) for timeouts, rate limits, and transient errors.

2. **Structured parsing** (`agents/agent.py:220-223`): `_parse_invoke` passes the raw string to `_parse_response`, which uses `trustcall.create_extractor` with the `parsing_llm` to extract a `MetaAnalysisInsights` object. If the extractor fails, it falls back to a `PydanticOutputParser` chain with up to 5 retry attempts (`agents/agent.py:338-417`).

3. **Pydantic validation** (`agents/agent.py:352`): `return_type.model_validate(result["responses"][0])` enforces the 6-field schema. Any `ValidationError` triggers the fallback `_try_parse` path.

4. **Cache store** (`agents/meta_agent.py:62-63`): If `cache_key` is not `None`, `self._meta_cache.store(cache_key, analysis, run_id=self.run_id)` persists the parsed result to SQLite. The `_CLEAR_BEFORE_STORE = True` flag on `MetaCache` (`caching/meta_cache.py:44`) ensures old entries for this namespace are cleared before writing.

5. **Logging** (`agents/meta_agent.py:65`): The analysis summary is logged via `analysis.llm_str()`, which formats the 6 fields into a human-readable markdown block (`agents/agent_responses.py:384-394`).

The returned `MetaAnalysisInsights` is consumed downstream by other agents (e.g., the cluster grouping and final analysis prompts) as the `{meta_context}` template variable.
