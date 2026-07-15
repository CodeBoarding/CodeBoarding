```mermaid
graph LR
    Lifecycle_Event_Interceptor["Lifecycle Event Interceptor"]
    Usage_Normalizer_Parser["Usage Normalizer & Parser"]
    Metrics_Aggregator_Budget_Guardrail["Metrics Aggregator & Budget Guardrail"]
    Lifecycle_Event_Interceptor -- "delegates raw response parsing for schema normalization" --> Usage_Normalizer_Parser
    Lifecycle_Event_Interceptor -- "updates execution state and triggers budget validation" --> Metrics_Aggregator_Budget_Guardrail
    Usage_Normalizer_Parser -- "feeds standardized usage data for stateful accumulation" --> Metrics_Aggregator_Budget_Guardrail
    Metrics_Aggregator_Budget_Guardrail -- "provides budget status for execution control" --> Lifecycle_Event_Interceptor
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Provides real-time feedback on actual token consumption and tool invocation to validate budget planner effectiveness and provide performance metrics.

### Lifecycle Event Interceptor
Acts as the primary entry point for the monitoring system by hooking into the agent's execution pipeline to capture temporal boundaries and initialize telemetry context.


**Related Classes/Methods**:

- `monitoring.callbacks.MonitoringCallback.on_tool_start`:64-79
- `monitoring.callbacks.MonitoringCallback.on_llm_end`:43-62



**Source Files:**

- [`monitoring/callbacks.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/callbacks.py)
  - `monitoring.callbacks.MonitoringCallback.on_llm_end` ([L43-L62](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/callbacks.py#L43-L62)) - Method
  - `monitoring.callbacks.MonitoringCallback.on_tool_start` ([L64-L79](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/callbacks.py#L64-L79)) - Method
  - `monitoring.callbacks.MonitoringCallback._extract_usage._coerce_int` ([L107-L111](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/callbacks.py#L107-L111)) - Function


### Usage Normalizer & Parser
Extracts raw metadata from diverse LLM provider responses and maps them into a standardized internal schema for consistent token counting.


**Related Classes/Methods**:

- `monitoring.callbacks.MonitoringCallback._extract_usage`:106-163



**Source Files:**

- [`monitoring/callbacks.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/callbacks.py)
  - `monitoring.callbacks.MonitoringCallback.__init__` ([L21-L26](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/callbacks.py#L21-L26)) - Method
  - `monitoring.callbacks.MonitoringCallback.model_name` ([L33-L35](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/callbacks.py#L33-L35)) - Method
  - `monitoring.callbacks.MonitoringCallback._extract_usage` ([L106-L163](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/callbacks.py#L106-L163)) - Method


### Metrics Aggregator & Budget Guardrail
Maintains stateful accumulation of usage data, calculates performance metrics, and performs proactive budget validation against predefined thresholds.


**Related Classes/Methods**: _None_


**Source Files:**

- [`monitoring/callbacks.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/callbacks.py)
  - `monitoring.callbacks.MonitoringCallback.stats` ([L38-L41](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/callbacks.py#L38-L41)) - Method
  - `monitoring.callbacks.MonitoringCallback.on_tool_end` ([L81-L89](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/callbacks.py#L81-L89)) - Method
  - `monitoring.callbacks.MonitoringCallback.on_tool_error` ([L91-L104](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/callbacks.py#L91-L104)) - Method
  - `monitoring.callbacks.MonitoringCallback._extract_usage._extract_usage_from_mapping` ([L113-L131](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/callbacks.py#L113-L131)) - Function




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)