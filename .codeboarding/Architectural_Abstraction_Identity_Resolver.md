```mermaid
graph LR
    Component_Synthesis_Relation_Mapper["Component Synthesis & Relation Mapper"]
    Identity_Repair_Integrity_Validator["Identity Repair & Integrity Validator"]
    Component_Synthesis_Relation_Mapper -- "stabilizes architectural model via identity repair" --> Identity_Repair_Integrity_Validator
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Web platform](https://img.shields.io/badge/Open%20in-Web%20platform-2563EB?style=flat-square)](https://app.codeboarding.org)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

The intelligence layer that interprets cluster data to define high-level architecture, utilizing specialized agents to synthesize descriptions and repair logic to maintain stable component identities across analysis runs.

### Component Synthesis & Relation Mapper
Responsible for transforming code clusters into architectural entities by analyzing API surfaces and mapping physical dependencies.


**Related Classes/Methods**:

- `agents.abstraction_agent.AbstractionAgent`:44-230
- `agents.details_agent.DetailsAgent`:45-300
- `static_analyzer.cluster_relations.build_component_relations`:297-335
- `agents.agent_responses.assign_component_ids`:599-630
- `agents.abstraction_agent.AbstractionAgent.step_api_surfaces`:147-154



**Source Files:**

- [`agents/abstraction_agent.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/abstraction_agent.py)
  - `agents.abstraction_agent.AbstractionAgent` ([L44-L230](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/abstraction_agent.py#L44-L230)) - Class
  - `agents.abstraction_agent.AbstractionAgent.step_clusters_grouping` ([L83-L95](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/abstraction_agent.py#L83-L95)) - Method
  - `agents.abstraction_agent.AbstractionAgent.step_api_surfaces` ([L147-L154](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/abstraction_agent.py#L147-L154)) - Method
  - `agents.abstraction_agent.AbstractionAgent.step_relation_analysis` ([L157-L191](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/abstraction_agent.py#L157-L191)) - Method
  - `agents.abstraction_agent.AbstractionAgent.run` ([L193-L230](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/abstraction_agent.py#L193-L230)) - Method
- [`agents/agent_responses.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py)
  - `agents.agent_responses.RelationCallSite` ([L178-L182](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L178-L182)) - Class
  - `agents.agent_responses.ComponentApiSurfaces.llm_str` ([L582-L585](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L582-L585)) - Method
  - `agents.agent_responses.assign_component_ids` ([L599-L630](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L599-L630)) - Function
  - `agents.agent_responses.assign_relation_ids` ([L633-L661](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L633-L661)) - Function
  - `agents.agent_responses.ScopeOperationAction` ([L741-L745](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L741-L745)) - Class
- [`agents/cluster_methods_mixin.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_methods_mixin.py)
  - `agents.cluster_methods_mixin.ClusterMethodsMixin._ensure_unique_key_entities` ([L187-L234](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_methods_mixin.py#L187-L234)) - Method
  - `agents.cluster_methods_mixin.ClusterMethodsMixin.build_static_relations` ([L690-L710](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_methods_mixin.py#L690-L710)) - Method
  - `agents.cluster_methods_mixin.ClusterMethodsMixin.build_scope_cfg_string` ([L719-L752](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_methods_mixin.py#L719-L752)) - Method
- [`agents/details_agent.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/details_agent.py)
  - `agents.details_agent.DetailsAgent` ([L45-L300](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/details_agent.py#L45-L300)) - Class
  - `agents.details_agent.DetailsAgent.step_clusters_grouping` ([L87-L105](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/details_agent.py#L87-L105)) - Method
  - `agents.details_agent.DetailsAgent.step_api_surfaces` ([L186-L193](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/details_agent.py#L186-L193)) - Method
  - `agents.details_agent.DetailsAgent.step_relation_analysis` ([L196-L231](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/details_agent.py#L196-L231)) - Method
  - `agents.details_agent.DetailsAgent.run` ([L233-L300](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/details_agent.py#L233-L300)) - Method
- [`diagram_analysis/diagram_generator.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/diagram_generator.py)
  - `diagram_analysis.diagram_generator.DiagramGenerator._initialize_agents` ([L916-L956](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/diagram_generator.py#L916-L956)) - Method
- [`static_analyzer/analysis_result.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/analysis_result.py)
  - `static_analyzer.analysis_result.StaticAnalysisResults.available_cfgs` ([L213-L219](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/analysis_result.py#L213-L219)) - Method
- [`static_analyzer/cluster_relations.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_relations.py)
  - `static_analyzer.cluster_relations.ClusterRelation` ([L22-L27](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_relations.py#L22-L27)) - Class
  - `static_analyzer.cluster_relations.build_node_to_component_map` ([L30-L41](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_relations.py#L30-L41)) - Function
  - `static_analyzer.cluster_relations.build_component_relations` ([L297-L335](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_relations.py#L297-L335)) - Function


### Identity Repair & Integrity Validator
Ensures architectural stability and correctness by repairing component names to prevent identity drift and validating structural integrity.


**Related Classes/Methods**:

- `agents.repair.repair_component_group_names`:27-46
- `agents.repair.ComponentRepairContext`:21-24
- `agents.validation.ValidationContext`:38-52
- `agents.repair._fuzzy_match_group_name`:63-75



**Source Files:**

- [`agents/abstraction_agent.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/abstraction_agent.py)
  - `agents.abstraction_agent.AbstractionAgent.step_final_analysis` ([L98-L144](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/abstraction_agent.py#L98-L144)) - Method
- [`agents/agent_responses.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py)
  - `agents.agent_responses.Component.llm_str` ([L487-L497](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L487-L497)) - Method
  - `agents.agent_responses.AnalysisInsights.llm_str` ([L515-L521](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L515-L521)) - Method
- [`agents/details_agent.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/details_agent.py)
  - `agents.details_agent.DetailsAgent.step_final_analysis` ([L108-L183](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/details_agent.py#L108-L183)) - Method
- [`agents/repair.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/repair.py)
  - `agents.repair.ComponentRepairTarget` ([L16-L17](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/repair.py#L16-L17)) - Class
  - `agents.repair.ComponentRepairContext` ([L21-L24](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/repair.py#L21-L24)) - Class
  - `agents.repair.repair_component_group_names` ([L27-L46](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/repair.py#L27-L46)) - Function
  - `agents.repair._canonical_group_name` ([L49-L54](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/repair.py#L49-L54)) - Function
  - `agents.repair._normalize_group_name` ([L57-L60](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/repair.py#L57-L60)) - Function
  - `agents.repair._fuzzy_match_group_name` ([L63-L75](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/repair.py#L63-L75)) - Function
  - `agents.repair.repair_key_entities` ([L78-L101](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/repair.py#L78-L101)) - Function
- [`agents/validation.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/validation.py)
  - `agents.validation.ValidationContext` ([L38-L52](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/validation.py#L38-L52)) - Class
  - `agents.validation.validate_group_name_coverage` ([L101-L187](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/validation.py#L101-L187)) - Function
  - `agents.validation.validate_key_entities` ([L190-L206](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/validation.py#L190-L206)) - Function




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)