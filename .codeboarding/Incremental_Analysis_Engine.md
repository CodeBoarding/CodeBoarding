```mermaid
graph LR
    IncrementalUpdater["IncrementalUpdater"]
    AnalysisCache["AnalysisCache"]
    StateValidator["StateValidator"]
    ClusterChangeAnalyzer["ClusterChangeAnalyzer"]
    DependencyImpactMapper["DependencyImpactMapper"]
    DeltaTaskGenerator["DeltaTaskGenerator"]
    IncrementalUpdater -- "invokes to determine which files have physically changed since the last run" --> StateValidator
    StateValidator -- "retrieves historical hashes and metadata to perform delta detection" --> AnalysisCache
    IncrementalUpdater -- "passes the detected file deltas to assess high-level architectural impact" --> ClusterChangeAnalyzer
    ClusterChangeAnalyzer -- "traces how low-level code changes propagate through the dependency graph" --> DependencyImpactMapper
    DependencyImpactMapper -- "queries the existing relationship graph to reconstruct the "blast zone" of a change" --> AnalysisCache
    IncrementalUpdater -- "provides the final list of affected entities to create a minimized analysis queue for the AI agents" --> DeltaTaskGenerator
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Optimizes analysis performance by managing the caching of static analysis results and orchestrating re-analysis only for changed parts of the codebase, ensuring efficiency and speed.

### IncrementalUpdater
The central orchestrator that manages the lifecycle of an incremental update. It coordinates between state validation, impact analysis, and task generation.


**Related Classes/Methods**:

- `repos.codeboarding.incremental.IncrementalUpdater`
- `repos.codeboarding.incremental.IncrementalUpdater.run_update`
- `repos.codeboarding.incremental.IncrementalUpdater._identify_changes`


### AnalysisCache
Manages the persistence and retrieval of previous analysis results, including file hashes, metadata, and the existing architectural graph stored in DuckDB.


**Related Classes/Methods**:

- `repos.codeboarding.incremental.AnalysisCache`
- `repos.codeboarding.incremental.AnalysisCache.get_metadata`
- `repos.codeboarding.incremental.AnalysisCache.save_state`
- `repos.codeboarding.incremental.AnalysisCache.query_hashes`


### StateValidator
Performs granular comparison of current file system states against the AnalysisCache to identify added, modified, or deleted files.


**Related Classes/Methods**:

- `repos.codeboarding.incremental.StateValidator`
- `repos.codeboarding.incremental.StateValidator.validate_hashes`
- `repos.codeboarding.incremental.StateValidator.detect_deltas`


### ClusterChangeAnalyzer
Evaluates logical groupings (clusters) of code to determine if a change in one module necessitates the re-analysis of its parent or sibling clusters.


**Related Classes/Methods**:

- `repos.codeboarding.incremental.ClusterChangeAnalyzer`
- `repos.codeboarding.incremental.ClusterChangeAnalyzer.analyze_impact`
- `repos.codeboarding.incremental.ClusterChangeAnalyzer.get_affected_clusters`


### DependencyImpactMapper
Maps code-level changes to the high-level architectural graph to identify "blast zones"—unchanged areas that require documentation updates due to dependency shifts.


**Related Classes/Methods**:

- `repos.codeboarding.incremental.DependencyImpactMapper`
- `repos.codeboarding.incremental.DependencyImpactMapper.map_dependencies`
- `repos.codeboarding.incremental.DependencyImpactMapper.calculate_blast_zone`


### DeltaTaskGenerator
Translates the identified impact areas into a prioritized execution plan for the Static Analysis Engine and AI Agents.


**Related Classes/Methods**:

- `repos.codeboarding.incremental.DeltaTaskGenerator`
- `repos.codeboarding.incremental.DeltaTaskGenerator.generate_tasks`
- `repos.codeboarding.incremental.DeltaTaskGenerator.prioritize_queue`




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)