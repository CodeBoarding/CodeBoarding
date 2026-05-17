```mermaid
graph LR
    Environment_Configuration_Manager["Environment & Configuration Manager"]
    LLM_Provider_Capability_Registry["LLM Provider & Capability Registry"]
    Persistence_Caching_Engine["Persistence & Caching Engine"]
    Environment_Configuration_Manager -- "Supplies resolved paths and user settings required to initialize" --> LLM_Provider_Capability_Registry
    LLM_Provider_Capability_Registry -- "Provides canonical model settings and provider metadata used to generate" --> Persistence_Caching_Engine
    Persistence_Caching_Engine -- "Uses resolved local run paths to locate and initialize" --> Environment_Configuration_Manager
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Provides the foundational services for the system, including LLM provider configuration (OpenAI, Anthropic, etc.), DuckDB/SQLite persistence for job tracking, and caching mechanisms to prevent redundant LLM calls.

### Environment & Configuration Manager
Orchestrates the initial setup of the system, resolving local filesystem paths for persistence and loading user-defined configurations. It ensures that the environment is prepared for both the LLM providers and the local database.


**Related Classes/Methods**:

- `codeboarding_cli.bootstrap.bootstrap_environment`:38-53
- `codeboarding_cli.bootstrap.resolve_local_run_paths`:26-35



**Source Files:**

- [`agents/llm_config.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/llm_config.py)
  - `agents.llm_config.configure_models` ([L34-L60](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/llm_config.py#L34-L60)) - Function
- [`codeboarding_cli/bootstrap.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingcodeboarding_cli/bootstrap.py)
  - `codeboarding_cli.bootstrap.LocalRunPaths` ([L18-L23](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingcodeboarding_cli/bootstrap.py#L18-L23)) - Class
  - `codeboarding_cli.bootstrap.resolve_local_run_paths` ([L26-L35](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingcodeboarding_cli/bootstrap.py#L26-L35)) - Function
  - `codeboarding_cli.bootstrap.bootstrap_environment` ([L38-L53](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingcodeboarding_cli/bootstrap.py#L38-L53)) - Function
- [`core/plugin_loader.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingcore/plugin_loader.py)
  - `core.plugin_loader.load_plugins` ([L17-L46](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingcore/plugin_loader.py#L17-L46)) - Function
- [`diagram_analysis/__init__.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/__init__.py)
  - `diagram_analysis.__init__.__getattr__` ([L6-L22](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/__init__.py#L6-L22)) - Function
- [`user_config.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardinguser_config.py)
  - `user_config.UserConfig.apply_to_env` ([L96-L101](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardinguser_config.py#L96-L101)) - Method
  - `user_config.ensure_config_template` ([L137-L143](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardinguser_config.py#L137-L143)) - Function
  - `user_config._append_commented_key` ([L146-L155](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardinguser_config.py#L146-L155)) - Function
- [`vscode_constants.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingvscode_constants.py)
  - `vscode_constants.get_bin_path` ([L5-L12](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingvscode_constants.py#L5-L12)) - Function
  - `vscode_constants.update_command_paths` ([L15-L62](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingvscode_constants.py#L15-L62)) - Function
  - `vscode_constants.find_runnable` ([L65-L69](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingvscode_constants.py#L65-L69)) - Function
  - `vscode_constants.update_config` ([L72-L74](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingvscode_constants.py#L72-L74)) - Function


### LLM Provider & Capability Registry
Acts as a factory and metadata registry for LLM services. It resolves specific providers (OpenAI, Anthropic, Ollama), validates API credentials, and maps model-specific capabilities such as context window limits to ensure requests stay within operational bounds.


**Related Classes/Methods**:

- `agents.llm_config.LLMConfig`:64-103
- `agents.llm_config.initialize_agent_llm`:324-327
- `agents.model_capabilities.get_context_window`:29-43



**Source Files:**

- [`agents/llm_config.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/llm_config.py)
  - `agents.llm_config.LLMConfig` ([L64-L103](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/llm_config.py#L64-L103)) - Class
  - `agents.llm_config.LLMConfig.get_api_key` ([L88-L89](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/llm_config.py#L88-L89)) - Method
  - `agents.llm_config.LLMConfig.is_active` ([L91-L95](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/llm_config.py#L91-L95)) - Method
  - `agents.llm_config.LLMConfig.get_resolved_extra_args` ([L97-L103](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/llm_config.py#L97-L103)) - Method
  - `agents.llm_config._initialize_llm` ([L254-L295](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/llm_config.py#L254-L295)) - Function
  - `agents.llm_config._resolve_active_provider` ([L298-L307](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/llm_config.py#L298-L307)) - Function
  - `agents.llm_config.LLMConfigError` ([L310-L311](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/llm_config.py#L310-L311)) - Class
  - `agents.llm_config.validate_api_key_provided` ([L314-L321](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/llm_config.py#L314-L321)) - Function
  - `agents.llm_config.initialize_agent_llm` ([L324-L327](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/llm_config.py#L324-L327)) - Function
  - `agents.llm_config.get_current_agent_context_window` ([L330-L341](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/llm_config.py#L330-L341)) - Function
  - `agents.llm_config.initialize_parsing_llm` ([L344-L346](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/llm_config.py#L344-L346)) - Function
  - `agents.llm_config.initialize_llms` ([L349-L352](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/llm_config.py#L349-L352)) - Function
  - `agents.llm_config.supports_prompt_caching` ([L355-L361](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/llm_config.py#L355-L361)) - Function
- [`agents/model_capabilities.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/model_capabilities.py)
  - `agents.model_capabilities.ContextWindow` ([L24-L26](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/model_capabilities.py#L24-L26)) - Class
  - `agents.model_capabilities.get_context_window` ([L29-L43](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/model_capabilities.py#L29-L43)) - Function
  - `agents.model_capabilities._resolve_env` ([L46-L60](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/model_capabilities.py#L46-L60)) - Function
  - `agents.model_capabilities._resolve_user_config` ([L63-L69](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/model_capabilities.py#L63-L69)) - Function
  - `agents.model_capabilities._user_context_window_override` ([L73-L78](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/model_capabilities.py#L73-L78)) - Function
  - `agents.model_capabilities._resolve_ollama` ([L81-L87](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/model_capabilities.py#L81-L87)) - Function
  - `agents.model_capabilities._ollama_show` ([L90-L120](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/model_capabilities.py#L90-L120)) - Function
  - `agents.model_capabilities._parse_num_ctx` ([L123-L126](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/model_capabilities.py#L123-L126)) - Function
  - `agents.model_capabilities._resolve_modelsdev` ([L129-L141](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/model_capabilities.py#L129-L141)) - Function
  - `agents.model_capabilities._resolve_litellm` ([L144-L155](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/model_capabilities.py#L144-L155)) - Function
  - `agents.model_capabilities._resolve_openrouter` ([L158-L167](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/model_capabilities.py#L158-L167)) - Function
  - `agents.model_capabilities._openrouter_id` ([L170-L176](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/model_capabilities.py#L170-L176)) - Function
  - `agents.model_capabilities._load` ([L180-L199](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/model_capabilities.py#L180-L199)) - Function
  - `agents.model_capabilities._read_cache` ([L202-L209](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/model_capabilities.py#L202-L209)) - Function
  - `agents.model_capabilities._normalize` ([L212-L217](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/model_capabilities.py#L212-L217)) - Function
- [`agents/prompts/prompt_factory.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/prompts/prompt_factory.py)
  - `agents.prompts.prompt_factory.LLMType.from_model_name` ([L30-L46](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/prompts/prompt_factory.py#L30-L46)) - Method
- [`user_config.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardinguser_config.py)
  - `user_config.ProviderUserConfig` ([L68-L81](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardinguser_config.py#L68-L81)) - Class
  - `user_config.LLMUserConfig` ([L85-L88](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardinguser_config.py#L85-L88)) - Class
  - `user_config.UserConfig` ([L92-L101](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardinguser_config.py#L92-L101)) - Class
  - `user_config.load_user_config` ([L104-L134](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardinguser_config.py#L104-L134)) - Function


### Persistence & Caching Engine
Manages the deduplication of LLM requests and the storage of results. It generates unique signatures based on model settings and prompts, interfacing with the underlying storage to provide a transparent caching layer that prevents redundant API calls.


**Related Classes/Methods**:

- `caching.cache.ModelSettings`:271-310
- `caching.cache.ModelSettings.signature`:288-289



**Source Files:**

- [`caching/cache.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingcaching/cache.py)
  - `caching.cache.BaseCache` ([L30-L268](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingcaching/cache.py#L30-L268)) - Class
  - `caching.cache.ModelSettings` ([L271-L310](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingcaching/cache.py#L271-L310)) - Class
  - `caching.cache.ModelSettings.canonical_json` ([L284-L286](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingcaching/cache.py#L284-L286)) - Method
  - `caching.cache.ModelSettings.signature` ([L288-L289](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingcaching/cache.py#L288-L289)) - Method
  - `caching.cache.ModelSettings.from_chat_model` ([L292-L310](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingcaching/cache.py#L292-L310)) - Method




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)