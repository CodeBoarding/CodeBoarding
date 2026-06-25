```mermaid
graph LR
    Environment_Provisioner["Environment Provisioner"]
    Agent_Bridge["Agent Bridge"]
    Workspace_Bootstrapper["Workspace Bootstrapper"]
    CLI_Command_Dispatcher["CLI Command Dispatcher"]
    Workflow_Controller -- "triggers" --> Environment_Provisioner
    Environment_Provisioner -- "returns metadata to" --> Workflow_Controller
    Workflow_Controller -- "passes results to" --> Agent_Bridge
    Agent_Bridge -- "reports metadata to" --> Execution_Monitor
    CLI_Command_Dispatcher -- "Triggers environment setup sequence" --> Workspace_Bootstrapper
    Workspace_Bootstrapper -- "Provides status confirmation and path references" --> CLI_Command_Dispatcher
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Prepares the physical and system-level execution context, including logging directories, configuration templates, and external system dependencies.

### Environment Provisioner
Handles the physical preparation of the codebase, including repository cloning and path resolution.


**Related Classes/Methods**: _None_


**Source Files:**

- [`monitoring/context.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/context.py)
  - `monitoring.context.monitor_execution.DummyContext.step` ([L33-L34](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/context.py#L33-L34)) - Method
- [`repo_utils/git_ops.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingrepo_utils/git_ops.py)
  - `repo_utils.git_ops.get_current_commit` ([L47-L64](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingrepo_utils/git_ops.py#L47-L64)) - Function


### Agent Bridge
Acts as the translation layer between static analysis output and the LLM-driven reasoning engine.


**Related Classes/Methods**: _None_


**Source Files:**

- [`monitoring/context.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/context.py)
  - `monitoring.context.monitor_execution.MonitorContext.step` ([L77-L81](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/context.py#L77-L81)) - Method


### Workspace Bootstrapper
Responsible for the physical setup of the analysis environment, including directory creation, configuration initialization, and dependency verification.


**Related Classes/Methods**:

- `codeboarding_cli.bootstrap.bootstrap_environment`:38-53



**Source Files:**

- [`agents/llm_config.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/llm_config.py)
  - `agents.llm_config.configure_models` ([L54-L80](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/llm_config.py#L54-L80)) - Function


### CLI Command Dispatcher
Acts as the primary entry point and traffic controller, parsing user intent, managing run lifecycles, and instantiating analysis agents.


**Related Classes/Methods**: _None_


**Source Files:**

- [`codeboarding_cli/bootstrap.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingcodeboarding_cli/bootstrap.py)
  - `codeboarding_cli.bootstrap.bootstrap_environment` ([L38-L53](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingcodeboarding_cli/bootstrap.py#L38-L53)) - Function




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)