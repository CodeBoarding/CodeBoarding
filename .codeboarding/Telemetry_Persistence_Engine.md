```mermaid
graph LR
    Telemetry_Extraction_Interface["Telemetry Extraction Interface"]
    Persistence_Orchestrator["Persistence Orchestrator"]
    Usage_Serialization_Sink["Usage Serialization Sink"]
    Telemetry_Extraction_Interface -- "provides raw metric payloads to" --> Persistence_Orchestrator
    Persistence_Orchestrator -- "delegates formatting and writing to" --> Usage_Serialization_Sink
    Usage_Serialization_Sink -- "returns serialized data fragments to" --> Persistence_Orchestrator
    Persistence_Orchestrator -- "calls" --> Telemetry_Extraction_Interface
    Usage_Serialization_Sink -- "calls" --> Telemetry_Extraction_Interface
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Handles the asynchronous egress of monitoring data to persistent storage or external sinks, ensuring data is periodically flushed.

### Telemetry Extraction Interface
Acts as the data acquisition layer that bridges the runtime agents with the persistence engine, providing mechanisms for components to expose internal state and metrics.


**Related Classes/Methods**:

- `monitoring.mixin.MonitoringMixin.get_monitoring_results`:14-16
- `monitoring.mixin.MonitoringMixin`:5-16



**Source Files:**

- [`monitoring/mixin.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/mixin.py)
  - `monitoring.mixin.MonitoringMixin` ([L5-L16](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/mixin.py#L5-L16)) - Class
  - `monitoring.mixin.MonitoringMixin.get_monitoring_results` ([L14-L16](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/mixin.py#L14-L16)) - Method
- [`monitoring/writers.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/writers.py)
  - `monitoring.writers.StreamingStatsWriter._loop` ([L84-L88](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/writers.py#L84-L88)) - Method
  - `monitoring.writers.StreamingStatsWriter._stream_token_usage` ([L90-L114](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/writers.py#L90-L114)) - Method


### Persistence Orchestrator
The central controller managing the lifecycle of telemetry data, including internal buffers, asynchronous flush timing, and coordination of system statistics writing.


**Related Classes/Methods**:

- `monitoring.writers.StreamingStatsWriter`:18-172



**Source Files:**

- [`monitoring/writers.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/writers.py)
  - `monitoring.writers.StreamingStatsWriter.llm_usage_file` ([L47-L48](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/writers.py#L47-L48)) - Method
  - `monitoring.writers.StreamingStatsWriter.__enter__` ([L50-L52](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/writers.py#L50-L52)) - Method
  - `monitoring.writers.StreamingStatsWriter.__exit__` ([L54-L56](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/writers.py#L54-L56)) - Method
  - `monitoring.writers.StreamingStatsWriter.stop` ([L70-L82](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/writers.py#L70-L82)) - Method
  - `monitoring.writers.StreamingStatsWriter._save_llm_usage` ([L116-L137](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/writers.py#L116-L137)) - Method


### Usage Serialization Sink
A specialized data handler focused on the structured serialization of LLM-specific metrics, including token usage, model identifiers, and cost metrics.


**Related Classes/Methods**:

- `monitoring.writers.StreamingStatsWriter._save_llm_usage`:116-137



**Source Files:**

- [`monitoring/writers.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/writers.py)
  - `monitoring.writers.StreamingStatsWriter.__init__` ([L24-L44](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/writers.py#L24-L44)) - Method
  - `monitoring.writers.StreamingStatsWriter.start` ([L58-L68](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/writers.py#L58-L68)) - Method
  - `monitoring.writers.StreamingStatsWriter._save_run_metadata` ([L139-L172](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/writers.py#L139-L172)) - Method




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)