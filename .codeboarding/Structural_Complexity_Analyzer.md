```mermaid
graph LR
    God_Class_Detection_Engine["God Class Detection Engine"]
    Coupling_Fan_Out_Analyzer["Coupling & Fan-Out Analyzer"]
    Function_Method_Complexity_Scorer["Function & Method Complexity Scorer"]
    Health_Check_Orchestrator["Health Check Orchestrator"]
    God_Class_Detection_Engine -- "delegates method grouping for class analysis" --> Function_Method_Complexity_Scorer
    God_Class_Detection_Engine -- "queries entity metadata for classification" --> Health_Check_Orchestrator
    Coupling_Fan_Out_Analyzer -- "calls" --> Function_Method_Complexity_Scorer
    Coupling_Fan_Out_Analyzer -- "queries entity metadata for dependency mapping" --> Health_Check_Orchestrator
    Function_Method_Complexity_Scorer -- "calls" --> Coupling_Fan_Out_Analyzer
    Function_Method_Complexity_Scorer -- "queries entity metadata for filtering" --> Health_Check_Orchestrator
    Health_Check_Orchestrator -- "dispatches class-level structural analysis" --> God_Class_Detection_Engine
    Health_Check_Orchestrator -- "dispatches interdependence analysis" --> Coupling_Fan_Out_Analyzer
    Health_Check_Orchestrator -- "dispatches maintainability scoring" --> Function_Method_Complexity_Scorer
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Performs deep static analysis using AST node representations to identify clean code violations such as God Classes, high fan-out, and excessive function size.

### God Class Detection Engine
Analyzes class-level structures to identify monolithic 'God Classes' that violate the Single Responsibility Principle by calculating LCOM and method counts.


**Related Classes/Methods**: _None_


**Source Files:**

- [`health/checks/coupling.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardinghealth/checks/coupling.py)
  - `health.checks.coupling.collect_coupling_values` ([L15-L32](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardinghealth/checks/coupling.py#L15-L32)) - Function
  - `health.checks.coupling.check_fan_out` ([L35-L85](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardinghealth/checks/coupling.py#L35-L85)) - Function
  - `health.checks.coupling.check_fan_in` ([L88-L140](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardinghealth/checks/coupling.py#L88-L140)) - Function


### Coupling & Fan-Out Analyzer
Measures the degree of interdependence between codebase parts by calculating Fan-Out to identify fragile areas prone to cascading failures.


**Related Classes/Methods**: _None_


**Source Files:**

- [`health/checks/function_size.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardinghealth/checks/function_size.py)
  - `health.checks.function_size.collect_function_sizes` ([L16-L25](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardinghealth/checks/function_size.py#L16-L25)) - Function
  - `health.checks.function_size.check_function_size` ([L28-L85](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardinghealth/checks/function_size.py#L28-L85)) - Function
- [`health/checks/god_class.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardinghealth/checks/god_class.py)
  - `health.checks.god_class._group_methods_by_class` ([L16-L27](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardinghealth/checks/god_class.py#L16-L27)) - Function


### Function & Method Complexity Scorer
Evaluates individual functions and methods for maintainability by measuring cyclomatic complexity and physical size (LOC).


**Related Classes/Methods**: _None_


**Source Files:**

- [`health/checks/god_class.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardinghealth/checks/god_class.py)
  - `health.checks.god_class.collect_god_class_values` ([L30-L64](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardinghealth/checks/god_class.py#L30-L64)) - Function
  - `health.checks.god_class.check_god_classes` ([L67-L167](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardinghealth/checks/god_class.py#L67-L167)) - Function
- [`repo_utils/ignore.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingrepo_utils/ignore.py)
  - `repo_utils.ignore.RepoIgnoreManager.should_skip_file` ([L292-L301](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingrepo_utils/ignore.py#L292-L301)) - Method


### Health Check Orchestrator
Acts as the central execution and aggregation layer, managing the lifecycle of health checks and formatting findings into unified reports.


**Related Classes/Methods**: _None_


**Source Files:**

- [`static_analyzer/node.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/node.py)
  - `static_analyzer.node.Node.is_class` ([L37-L39](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/node.py#L37-L39)) - Method
  - `static_analyzer.node.Node.is_data` ([L41-L43](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/node.py#L41-L43)) - Method




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)