```mermaid
graph LR
    Check_Discovery_Registry["Check Discovery & Registry"]
    Execution_Controller_Filter_Engine["Execution Controller & Filter Engine"]
    Static_Analysis_Diagnostic_Suite["Static Analysis Diagnostic Suite"]
    Check_Discovery_Registry -- "Discovers and registers diagnostic workers" --> Static_Analysis_Diagnostic_Suite
    Execution_Controller_Filter_Engine -- "Orchestrates check discovery and selection" --> Check_Discovery_Registry
    Execution_Controller_Filter_Engine -- "Dispatches execution via runtime function references" --> Static_Analysis_Diagnostic_Suite
    Static_Analysis_Diagnostic_Suite -- "calls" --> Check_Discovery_Registry
    Static_Analysis_Diagnostic_Suite -- "Reports architectural violations" --> Execution_Controller_Filter_Engine
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Acts as the central controller and entry point for the diagnostic subsystem, managing check discovery, exclusion filtering, and execution flow.

### Check Discovery & Registry
Responsible for the discovery phase of the health lifecycle, mapping programming languages to applicable diagnostic checks and registering available health probes.


**Related Classes/Methods**: _None_


**Source Files:**

- [`health/checks/circular_deps.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardinghealth/checks/circular_deps.py)
  - `health.checks.circular_deps.check_circular_dependencies` ([L10-L48](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardinghealth/checks/circular_deps.py#L10-L48)) - Function
- [`health/checks/unused_code_diagnostics.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardinghealth/checks/unused_code_diagnostics.py)
  - `health.checks.unused_code_diagnostics.FileDiagnostic` ([L131-L135](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardinghealth/checks/unused_code_diagnostics.py#L131-L135)) - Class
  - `health.checks.unused_code_diagnostics.LSPDiagnosticsCollector` ([L138-L262](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardinghealth/checks/unused_code_diagnostics.py#L138-L262)) - Class
  - `health.checks.unused_code_diagnostics.LSPDiagnosticsCollector.add_diagnostic` ([L145-L146](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardinghealth/checks/unused_code_diagnostics.py#L145-L146)) - Method
- [`health/models.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardinghealth/models.py)
  - `health.models.CircularDependencyCheck` ([L88-L103](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardinghealth/models.py#L88-L103)) - Class


### Execution Controller & Filter Engine
The central orchestrator managing the health run lifecycle, applying exclusion patterns, and coordinating the invocation of diagnostic checks.


**Related Classes/Methods**:

- `health.runner._apply_exclude_patterns`:44-63



**Source Files:**

- [`health/runner.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardinghealth/runner.py)
  - `health.runner._matches_exclude_pattern` ([L34-L41](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardinghealth/runner.py#L34-L41)) - Function
  - `health.runner._apply_exclude_patterns` ([L44-L63](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardinghealth/runner.py#L44-L63)) - Function


### Static Analysis Diagnostic Suite
Contains the worker logic for architectural validation, performing deep static analysis to identify structural issues like dependency cycles or illegal imports.


**Related Classes/Methods**: _None_


**Source Files:**

- [`health/runner.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardinghealth/runner.py)
  - `health.runner._collect_checks_for_language` ([L71-L131](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardinghealth/runner.py#L71-L131)) - Function




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)