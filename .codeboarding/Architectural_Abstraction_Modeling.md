```mermaid
graph LR
    Architectural_Synthesis_Cluster_Orchestrator["Architectural Synthesis & Cluster Orchestrator"]
    Component_Modeling_API_Definition["Component Modeling & API Definition"]
    Incremental_State_Content_Fingerprinting["Incremental State & Content Fingerprinting"]
    Architectural_Synthesis_Cluster_Orchestrator -- "Orchestrates architectural schema instantiation" --> Component_Modeling_API_Definition
    Architectural_Synthesis_Cluster_Orchestrator -- "Synchronizes architectural state with physical code changes" --> Incremental_State_Content_Fingerprinting
    Component_Modeling_API_Definition -- "Provides structural insights for global synthesis" --> Architectural_Synthesis_Cluster_Orchestrator
    Component_Modeling_API_Definition -- "Maps logical components to physical file entries" --> Incremental_State_Content_Fingerprinting
    Incremental_State_Content_Fingerprinting -- "Reconstitutes architectural models from persisted state" --> Component_Modeling_API_Definition
    click Architectural_Synthesis_Cluster_Orchestrator href "https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboarding/Architectural_Synthesis_Cluster_Orchestrator.md" "Details"
    click Component_Modeling_API_Definition href "https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboarding/Component_Modeling_API_Definition.md" "Details"
    click Incremental_State_Content_Fingerprinting href "https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboarding/Incremental_State_Content_Fingerprinting.md" "Details"
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Transforms raw code data into high-level architectural models, defining components, API surfaces, and structural clusters through specialized abstraction agents.

### Architectural Synthesis & Cluster Orchestrator [[Expand]](./Architectural_Synthesis_Cluster_Orchestrator.md)
Acts as the primary intelligence layer that interprets cluster data to define high-level architecture, managing the lifecycle of abstraction agents and assigning stable identities to components.


**Related Classes/Methods**:

- `agents.abstraction_agent.AbstractionAgent`:44-230
- `agents.agent_responses.ClusterAnalysis`:440-452
- `agents.cluster_ids.CodeBoardingClusterIds`:14-44
- `agents.cluster_methods_mixin.ClusterMethodsMixin`:100-752
- `agents.agent_responses.assign_component_ids`:612-643



**Source Files:**

- [`agents/abstraction_agent.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/abstraction_agent.py)
  - `agents.abstraction_agent.AbstractionAgent` ([L44-L230](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/abstraction_agent.py#L44-L230)) - Class
  - `agents.abstraction_agent.AbstractionAgent.step_clusters_grouping` ([L83-L95](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/abstraction_agent.py#L83-L95)) - Method
  - `agents.abstraction_agent.AbstractionAgent.step_final_analysis` ([L98-L144](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/abstraction_agent.py#L98-L144)) - Method
  - `agents.abstraction_agent.AbstractionAgent.step_api_surfaces` ([L147-L154](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/abstraction_agent.py#L147-L154)) - Method
  - `agents.abstraction_agent.AbstractionAgent.step_relation_analysis` ([L157-L191](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/abstraction_agent.py#L157-L191)) - Method
  - `agents.abstraction_agent.AbstractionAgent.run` ([L193-L230](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/abstraction_agent.py#L193-L230)) - Method
- [`agents/agent_responses.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py)
  - `agents.agent_responses.RelationCallSite` ([L179-L183](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L179-L183)) - Class
  - `agents.agent_responses.ClustersComponent` ([L389-L437](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L389-L437)) - Class
  - `agents.agent_responses.ClustersComponent.llm_str` ([L435-L437](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L435-L437)) - Method
  - `agents.agent_responses.ClusterAnalysis` ([L440-L452](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L440-L452)) - Class
  - `agents.agent_responses.ClusterAnalysis.llm_str` ([L447-L452](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L447-L452)) - Method
  - `agents.agent_responses.Component.llm_str` ([L496-L506](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L496-L506)) - Method
  - `agents.agent_responses.AnalysisInsights` ([L509-L534](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L509-L534)) - Class
  - `agents.agent_responses.AnalysisInsights.llm_str` ([L524-L530](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L524-L530)) - Method
  - `agents.agent_responses.AnalysisInsights.file_to_component` ([L532-L534](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L532-L534)) - Method
  - `agents.agent_responses.ComponentArchitecture` ([L537-L550](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L537-L550)) - Class
  - `agents.agent_responses.ComponentArchitecture.llm_str` ([L545-L550](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L545-L550)) - Method
  - `agents.agent_responses.ComponentApiSurfaces.llm_str` ([L595-L598](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L595-L598)) - Method
  - `agents.agent_responses.assign_component_ids` ([L612-L643](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L612-L643)) - Function
  - `agents.agent_responses.assign_relation_ids` ([L646-L674](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L646-L674)) - Function
  - `agents.agent_responses.ScopeOperationAction` ([L823-L827](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L823-L827)) - Class
- [`agents/cluster_ids.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_ids.py)
  - `agents.cluster_ids.GraphClusterIds` ([L8-L11](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_ids.py#L8-L11)) - Class
  - `agents.cluster_ids.GraphClusterIds.sort` ([L10-L11](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_ids.py#L10-L11)) - Method
  - `agents.cluster_ids.CodeBoardingClusterIds` ([L14-L44](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_ids.py#L14-L44)) - Class
  - `agents.cluster_ids.CodeBoardingClusterIds.prefix_for_scope` ([L16-L17](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_ids.py#L16-L17)) - Method
  - `agents.cluster_ids.CodeBoardingClusterIds.sort` ([L20-L22](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_ids.py#L20-L22)) - Method
  - `agents.cluster_ids.CodeBoardingClusterIds.from_graph_id` ([L25-L26](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_ids.py#L25-L26)) - Method
  - `agents.cluster_ids.CodeBoardingClusterIds.from_graph_ids` ([L29-L30](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_ids.py#L29-L30)) - Method
  - `agents.cluster_ids.CodeBoardingClusterIds.qualify_local_id` ([L33-L38](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_ids.py#L33-L38)) - Method
  - `agents.cluster_ids.CodeBoardingClusterIds.qualify_local_ids` ([L41-L44](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_ids.py#L41-L44)) - Method
  - `agents.cluster_ids._cluster_id_sort_key` ([L47-L49](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_ids.py#L47-L49)) - Function
- [`agents/cluster_methods_mixin.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_methods_mixin.py)
  - `agents.cluster_methods_mixin._summarize_group` ([L47-L66](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_methods_mixin.py#L47-L66)) - Function
  - `agents.cluster_methods_mixin._fallback_component` ([L69-L73](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_methods_mixin.py#L69-L73)) - Function
  - `agents.cluster_methods_mixin.ClusterMethodsMixin` ([L100-L752](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_methods_mixin.py#L100-L752)) - Class
  - `agents.cluster_methods_mixin.ClusterMethodsMixin.deterministic_cluster_grouping` ([L113-L146](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_methods_mixin.py#L113-L146)) - Method
  - `agents.cluster_methods_mixin.ClusterMethodsMixin.assemble_one_component_per_group` ([L149-L185](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_methods_mixin.py#L149-L185)) - Method
  - `agents.cluster_methods_mixin.ClusterMethodsMixin._ensure_unique_key_entities` ([L187-L234](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_methods_mixin.py#L187-L234)) - Method
  - `agents.cluster_methods_mixin.ClusterMethodsMixin._resolve_cluster_ids_from_groups` ([L236-L251](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_methods_mixin.py#L236-L251)) - Method
  - `agents.cluster_methods_mixin.ClusterMethodsMixin._collect_all_cfg_nodes` ([L359-L378](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_methods_mixin.py#L359-L378)) - Method
  - `agents.cluster_methods_mixin.ClusterMethodsMixin._find_nearest_cluster` ([L402-L439](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_methods_mixin.py#L402-L439)) - Method
  - `agents.cluster_methods_mixin.ClusterMethodsMixin._build_cluster_to_component_map` ([L497-L503](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_methods_mixin.py#L497-L503)) - Method
  - `agents.cluster_methods_mixin.ClusterMethodsMixin._build_node_to_cluster_map` ([L505-L519](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_methods_mixin.py#L505-L519)) - Method
  - `agents.cluster_methods_mixin.ClusterMethodsMixin._validate_cluster_coverage` ([L521-L531](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_methods_mixin.py#L521-L531)) - Method
  - `agents.cluster_methods_mixin.ClusterMethodsMixin._find_component_by_file` ([L533-L553](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_methods_mixin.py#L533-L553)) - Method
  - `agents.cluster_methods_mixin.ClusterMethodsMixin._assign_nodes_to_components` ([L555-L628](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_methods_mixin.py#L555-L628)) - Method
  - `agents.cluster_methods_mixin.ClusterMethodsMixin._log_node_coverage` ([L630-L634](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_methods_mixin.py#L630-L634)) - Method
  - `agents.cluster_methods_mixin.ClusterMethodsMixin.populate_file_methods` ([L636-L688](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_methods_mixin.py#L636-L688)) - Method
  - `agents.cluster_methods_mixin.ClusterMethodsMixin.build_static_relations` ([L690-L710](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_methods_mixin.py#L690-L710)) - Method
  - `agents.cluster_methods_mixin.ClusterMethodsMixin._prefix_local_cluster_ids` ([L712-L717](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_methods_mixin.py#L712-L717)) - Method
  - `agents.cluster_methods_mixin.ClusterMethodsMixin.build_scope_cfg_string` ([L719-L752](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_methods_mixin.py#L719-L752)) - Method
- [`agents/details_agent.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/details_agent.py)
  - `agents.details_agent.DetailsAgent` ([L45-L300](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/details_agent.py#L45-L300)) - Class
  - `agents.details_agent.DetailsAgent.step_clusters_grouping` ([L87-L105](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/details_agent.py#L87-L105)) - Method
  - `agents.details_agent.DetailsAgent.step_final_analysis` ([L108-L183](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/details_agent.py#L108-L183)) - Method
  - `agents.details_agent.DetailsAgent.step_api_surfaces` ([L186-L193](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/details_agent.py#L186-L193)) - Method
  - `agents.details_agent.DetailsAgent.step_relation_analysis` ([L196-L231](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/details_agent.py#L196-L231)) - Method
  - `agents.details_agent.DetailsAgent.run` ([L233-L300](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/details_agent.py#L233-L300)) - Method
- [`agents/incremental_agent.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/incremental_agent.py)
  - `agents.incremental_agent.IncrementalAgent` ([L55-L508](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/incremental_agent.py#L55-L508)) - Class
  - `agents.incremental_agent.IncrementalAgent.update_scope` ([L97-L178](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/incremental_agent.py#L97-L178)) - Method
  - `agents.incremental_agent.IncrementalAgent._create_component_from_operation` ([L180-L210](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/incremental_agent.py#L180-L210)) - Method
  - `agents.incremental_agent.IncrementalAgent.detail_new_components` ([L213-L248](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/incremental_agent.py#L213-L248)) - Method
  - `agents.incremental_agent.IncrementalAgent._update_component_from_operation` ([L250-L269](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/incremental_agent.py#L250-L269)) - Method
  - `agents.incremental_agent.IncrementalAgent._patch_scope_file_methods` ([L271-L300](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/incremental_agent.py#L271-L300)) - Method
  - `agents.incremental_agent.IncrementalAgent.step_api_surfaces` ([L303-L312](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/incremental_agent.py#L303-L312)) - Method
  - `agents.incremental_agent.IncrementalAgent.step_relation_analysis` ([L315-L352](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/incremental_agent.py#L315-L352)) - Method
  - `agents.incremental_agent.IncrementalAgent._attach_static_relations` ([L354-L363](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/incremental_agent.py#L354-L363)) - Method
  - `agents.incremental_agent.IncrementalAgent.generate_scope_relations` ([L366-L422](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/incremental_agent.py#L366-L422)) - Method
  - `agents.incremental_agent.IncrementalAgent.generate_all_scope_relations` ([L425-L458](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/incremental_agent.py#L425-L458)) - Method
  - `agents.incremental_agent.IncrementalAgent._generate_scope_relations_parallel` ([L460-L496](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/incremental_agent.py#L460-L496)) - Method
  - `agents.incremental_agent.IncrementalAgent._generate_scope_relations_parallel.run_one` ([L476-L486](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/incremental_agent.py#L476-L486)) - Function
  - `agents.incremental_agent.IncrementalAgent._clone_for_worker` ([L498-L508](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/incremental_agent.py#L498-L508)) - Method
  - `agents.incremental_agent._cluster_analysis_for_scope` ([L511-L533](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/incremental_agent.py#L511-L533)) - Function
  - `agents.incremental_agent._local_graph_cluster_ids` ([L536-L553](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/incremental_agent.py#L536-L553)) - Function
  - `agents.incremental_agent._new_component_membership_summary` ([L556-L567](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/incremental_agent.py#L556-L567)) - Function
  - `agents.incremental_agent._log_scope_relations_summary` ([L570-L575](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/incremental_agent.py#L570-L575)) - Function
  - `agents.incremental_agent._operation_source_cluster_ids` ([L578-L584](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/incremental_agent.py#L578-L584)) - Function
  - `agents.incremental_agent._remove_reassigned_clusters` ([L587-L612](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/incremental_agent.py#L587-L612)) - Function
  - `agents.incremental_agent._log_duplicate_cluster_ownership` ([L615-L628](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/incremental_agent.py#L615-L628)) - Function
  - `agents.incremental_agent._component_id_parent` ([L631-L632](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/incremental_agent.py#L631-L632)) - Function
  - `agents.incremental_agent._live_cfg_qnames` ([L732-L739](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/incremental_agent.py#L732-L739)) - Function
  - `agents.incremental_agent._component_has_live_cfg_methods` ([L742-L745](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/incremental_agent.py#L742-L745)) - Function
- [`agents/incremental_results.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/incremental_results.py)
  - `agents.incremental_results.ScopeRelationContext` ([L7-L14](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/incremental_results.py#L7-L14)) - Class
  - `agents.incremental_results.ScopeUpdateResult` ([L18-L24](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/incremental_results.py#L18-L24)) - Class
- [`agents/repair.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/repair.py)
  - `agents.repair.ComponentRepairTarget` ([L16-L17](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/repair.py#L16-L17)) - Class
  - `agents.repair.ComponentRepairContext` ([L21-L24](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/repair.py#L21-L24)) - Class
  - `agents.repair.repair_component_group_names` ([L27-L46](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/repair.py#L27-L46)) - Function
  - `agents.repair._canonical_group_name` ([L49-L54](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/repair.py#L49-L54)) - Function
  - `agents.repair._normalize_group_name` ([L57-L60](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/repair.py#L57-L60)) - Function
  - `agents.repair._fuzzy_match_group_name` ([L63-L75](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/repair.py#L63-L75)) - Function
  - `agents.repair.repair_key_entities` ([L78-L101](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/repair.py#L78-L101)) - Function
- [`agents/validation.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/validation.py)
  - `agents.validation.ValidationContext` ([L38-L53](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/validation.py#L38-L53)) - Class
  - `agents.validation.ValidationResult` ([L57-L62](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/validation.py#L57-L62)) - Class
  - `agents.validation.RelationValidationTarget` ([L65-L66](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/validation.py#L65-L66)) - Class
  - `agents.validation.ComponentValidationTarget` ([L69-L70](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/validation.py#L69-L70)) - Class
  - `agents.validation.validate_group_name_coverage` ([L102-L188](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/validation.py#L102-L188)) - Function
  - `agents.validation.validate_key_entities` ([L191-L207](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/validation.py#L191-L207)) - Function
  - `agents.validation.validate_relation_component_names` ([L273-L323](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/validation.py#L273-L323)) - Function
  - `agents.validation.validate_relation_evidence` ([L326-L402](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/validation.py#L326-L402)) - Function
  - `agents.validation.validate_relations` ([L405-L427](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/validation.py#L405-L427)) - Function
  - `agents.validation._has_relation_evidence` ([L451-L455](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/validation.py#L451-L455)) - Function
  - `agents.validation._component_cluster_ids` ([L488-L496](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/validation.py#L488-L496)) - Function
- [`diagram_analysis/diagram_generator.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/diagram_generator.py)
  - `diagram_analysis.diagram_generator.DiagramGenerator._initialize_agents` ([L836-L876](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/diagram_generator.py#L836-L876)) - Method
- [`diagram_analysis/exceptions.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/exceptions.py)
  - `diagram_analysis.exceptions.IncrementalClusteringError` ([L46-L63](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/exceptions.py#L46-L63)) - Class
  - `diagram_analysis.exceptions.IncrementalClusteringError.__init__` ([L57-L63](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/exceptions.py#L57-L63)) - Method
- [`diagram_analysis/scope_plan.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/scope_plan.py)
  - `diagram_analysis.scope_plan.previous_ownership` ([L44-L107](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/scope_plan.py#L44-L107)) - Function
  - `diagram_analysis.scope_plan.plan_scope_update` ([L110-L244](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/scope_plan.py#L110-L244)) - Function
  - `diagram_analysis.scope_plan._provisional_name` ([L247-L250](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/scope_plan.py#L247-L250)) - Function
  - `diagram_analysis.scope_plan._provisional_description` ([L253-L267](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/scope_plan.py#L253-L267)) - Function
- [`monitoring/context.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/context.py)
  - `monitoring.context.trace` ([L131-L173](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/context.py#L131-L173)) - Function
  - `monitoring.context.trace._create_wrapper` ([L139-L161](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/context.py#L139-L161)) - Function
  - `monitoring.context.trace._create_wrapper.wrapper` ([L141-L159](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/context.py#L141-L159)) - Function
  - `monitoring.context.trace.decorator` ([L169-L171](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingmonitoring/context.py#L169-L171)) - Function
- [`static_analyzer/analysis_result.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/analysis_result.py)
  - `static_analyzer.analysis_result.StaticAnalysisResults.available_cfgs` ([L213-L219](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/analysis_result.py#L213-L219)) - Method
- [`static_analyzer/cluster_helpers.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_helpers.py)
  - `static_analyzer.cluster_helpers._build_meta_graph` ([L133-L160](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_helpers.py#L133-L160)) - Function
  - `static_analyzer.cluster_helpers.group_symbols` ([L163-L166](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_helpers.py#L163-L166)) - Function
  - `static_analyzer.cluster_helpers.combine_cluster_results` ([L169-L188](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_helpers.py#L169-L188)) - Function
  - `static_analyzer.cluster_helpers._pick_peak_partition` ([L196-L243](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_helpers.py#L196-L243)) - Function
  - `static_analyzer.cluster_helpers._pick_peak_partition.range_distance` ([L238-L239](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_helpers.py#L238-L239)) - Function
  - `static_analyzer.cluster_helpers._seeds_from_partition` ([L246-L280](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_helpers.py#L246-L280)) - Function
  - `static_analyzer.cluster_helpers._cluster_packages` ([L283-L285](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_helpers.py#L283-L285)) - Function
  - `static_analyzer.cluster_helpers._package_affinity` ([L288-L300](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_helpers.py#L288-L300)) - Function
  - `static_analyzer.cluster_helpers._seed_distances` ([L303-L322](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_helpers.py#L303-L322)) - Function
  - `static_analyzer.cluster_helpers._absorb_leftovers` ([L325-L357](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_helpers.py#L325-L357)) - Function
  - `static_analyzer.cluster_helpers._method_counts` ([L365-L366](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_helpers.py#L365-L366)) - Function
  - `static_analyzer.cluster_helpers._modularity` ([L369-L371](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_helpers.py#L369-L371)) - Function
  - `static_analyzer.cluster_helpers._optimize_grouping` ([L374-L399](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_helpers.py#L374-L399)) - Function
  - `static_analyzer.cluster_helpers.supercluster_by_modularity_peak` ([L402-L427](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_helpers.py#L402-L427)) - Function
  - `static_analyzer.cluster_helpers.supercluster_leaf_ids` ([L430-L446](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_helpers.py#L430-L446)) - Function
  - `static_analyzer.cluster_helpers._inherit_ids` ([L460-L490](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_helpers.py#L460-L490)) - Function
  - `static_analyzer.cluster_helpers.AnchoredGrouping` ([L494-L501](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_helpers.py#L494-L501)) - Class
  - `static_analyzer.cluster_helpers.anchored_grouping` ([L504-L577](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_helpers.py#L504-L577)) - Function
- [`static_analyzer/cluster_relations.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_relations.py)
  - `static_analyzer.cluster_relations.ClusterRelation` ([L22-L27](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_relations.py#L22-L27)) - Class
  - `static_analyzer.cluster_relations.build_node_to_component_map` ([L30-L41](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_relations.py#L30-L41)) - Function
  - `static_analyzer.cluster_relations.build_component_relations` ([L110-L148](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_relations.py#L110-L148)) - Function
- [`static_analyzer/graph.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/graph.py)
  - `static_analyzer.graph.detect_communities` ([L21-L34](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/graph.py#L21-L34)) - Function


### Component Modeling & API Definition [[Expand]](./Component_Modeling_API_Definition.md)
Defines the structural schema for architectural components, including their public API surfaces, file memberships, and inter-component relationships.


**Related Classes/Methods**:

- `agents.agent_responses.ComponentApiSurface`:553-587
- `agents.agent_responses.ComponentRelations`:601-609
- `agents.agent_responses.CFGComponent`:700-716
- `agents.agent_responses.FileClassification`:798-805



**Source Files:**

- [`agents/agent_responses.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py)
  - `agents.agent_responses.LLMBaseModel` ([L24-L129](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L24-L129)) - Class
  - `agents.agent_responses.LLMBaseModel.llm_str` ([L28-L29](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L28-L29)) - Method
  - `agents.agent_responses.LLMBaseModel._is_field_hidden` ([L32-L38](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L32-L38)) - Method
  - `agents.agent_responses.LLMBaseModel._excluded_fields` ([L41-L50](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L41-L50)) - Method
  - `agents.agent_responses.LLMBaseModel._resolve_excluded_by_title` ([L53-L72](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L53-L72)) - Method
  - `agents.agent_responses.LLMBaseModel._resolve_excluded_by_title.walk` ([L57-L69](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L57-L69)) - Function
  - `agents.agent_responses.LLMBaseModel._extractor_fields` ([L75-L94](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L75-L94)) - Method
  - `agents.agent_responses.LLMBaseModel.extractor_str` ([L97-L104](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L97-L104)) - Method
  - `agents.agent_responses.LLMBaseModel.model_json_schema` ([L107-L129](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L107-L129)) - Method
  - `agents.agent_responses.SourceCodeReference` ([L132-L171](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L132-L171)) - Class
  - `agents.agent_responses.SourceCodeReference.llm_str` ([L153-L161](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L153-L161)) - Method
  - `agents.agent_responses.SourceCodeReference.__str__` ([L163-L171](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L163-L171)) - Method
  - `agents.agent_responses.RelationEdge` ([L186-L245](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L186-L245)) - Class
  - `agents.agent_responses.RelationEdge.from_dict` ([L200-L211](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L200-L211)) - Method
  - `agents.agent_responses.RelationEdge.from_edge` ([L214-L229](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L214-L229)) - Method
  - `agents.agent_responses.RelationEdge.identity` ([L234-L245](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L234-L245)) - Method
  - `agents.agent_responses._relation_endpoint_from_key` ([L248-L267](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L248-L267)) - Function
  - `agents.agent_responses.Relation` ([L270-L386](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L270-L386)) - Class
  - `agents.agent_responses.Relation.from_edges` ([L301-L322](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L301-L322)) - Method
  - `agents.agent_responses.Relation.llm_str` ([L324-L325](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L324-L325)) - Method
  - `agents.agent_responses.Relation.pair_key` ([L327-L332](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L327-L332)) - Method
  - `agents.agent_responses.Relation.with_merged_edges` ([L334-L346](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L334-L346)) - Method
  - `agents.agent_responses.Relation.merge_edges_from` ([L348-L354](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L348-L354)) - Method
  - `agents.agent_responses.Relation._merge_edges` ([L357-L362](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L357-L362)) - Method
  - `agents.agent_responses.Relation._unique_edges` ([L365-L374](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L365-L374)) - Method
  - `agents.agent_responses.Relation.edge_count` ([L377-L378](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L377-L378)) - Method
  - `agents.agent_responses.Relation.analysis_dump` ([L380-L386](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L380-L386)) - Method
  - `agents.agent_responses.Component` ([L455-L506](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L455-L506)) - Class
  - `agents.agent_responses.Component.file_paths` ([L492-L494](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L492-L494)) - Method
  - `agents.agent_responses.ComponentApiSurface` ([L553-L587](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L553-L587)) - Class
  - `agents.agent_responses.ComponentApiSurface.llm_str` ([L575-L587](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L575-L587)) - Method
  - `agents.agent_responses.ComponentApiSurfaces` ([L590-L598](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L590-L598)) - Class
  - `agents.agent_responses.ComponentRelations` ([L601-L609](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L601-L609)) - Class
  - `agents.agent_responses.ComponentRelations.llm_str` ([L606-L609](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L606-L609)) - Method
  - `agents.agent_responses.CFGComponent` ([L700-L716](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L700-L716)) - Class
  - `agents.agent_responses.CFGComponent.llm_str` ([L709-L716](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L709-L716)) - Method
  - `agents.agent_responses.CFGAnalysisInsights` ([L719-L731](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L719-L731)) - Class
  - `agents.agent_responses.CFGAnalysisInsights.llm_str` ([L725-L731](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L725-L731)) - Method
  - `agents.agent_responses.ExpandComponent` ([L734-L741](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L734-L741)) - Class
  - `agents.agent_responses.ExpandComponent.llm_str` ([L740-L741](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L740-L741)) - Method
  - `agents.agent_responses.ValidationInsights` ([L744-L754](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L744-L754)) - Class
  - `agents.agent_responses.ValidationInsights.llm_str` ([L753-L754](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L753-L754)) - Method
  - `agents.agent_responses.UpdateAnalysis` ([L757-L766](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L757-L766)) - Class
  - `agents.agent_responses.UpdateAnalysis.llm_str` ([L765-L766](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L765-L766)) - Method
  - `agents.agent_responses.FileClassification` ([L798-L805](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L798-L805)) - Class
  - `agents.agent_responses.FileClassification.llm_str` ([L804-L805](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L804-L805)) - Method
  - `agents.agent_responses.ComponentFiles` ([L808-L820](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L808-L820)) - Class
  - `agents.agent_responses.ComponentFiles.llm_str` ([L815-L820](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L815-L820)) - Method
  - `agents.agent_responses.ScopedClusterRef` ([L830-L839](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L830-L839)) - Class
  - `agents.agent_responses.ScopedClusterRef.llm_str` ([L837-L839](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L837-L839)) - Method
  - `agents.agent_responses.ScopeOperation` ([L842-L872](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L842-L872)) - Class
  - `agents.agent_responses.ScopeOperation.llm_str` ([L865-L872](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L865-L872)) - Method
  - `agents.agent_responses.ScopeUpdateDecision` ([L875-L883](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L875-L883)) - Class
  - `agents.agent_responses.ScopeUpdateDecision.llm_str` ([L880-L883](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L880-L883)) - Method
  - `agents.agent_responses.FilePath` ([L886-L900](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L886-L900)) - Class
  - `agents.agent_responses.FilePath.llm_str` ([L899-L900](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L899-L900)) - Method
- [`agents/relation_edges.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/relation_edges.py)
  - `agents.relation_edges.append_or_merge_relation` ([L9-L23](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/relation_edges.py#L9-L23)) - Function
  - `agents.relation_edges._is_internal_self_relation` ([L55-L68](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/relation_edges.py#L55-L68)) - Function
  - `agents.relation_edges.drop_internal_self_relations` ([L71-L73](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/relation_edges.py#L71-L73)) - Function
  - `agents.relation_edges._relation_backing_survives` ([L76-L89](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/relation_edges.py#L76-L89)) - Function
  - `agents.relation_edges._backing_edge_pairs` ([L92-L94](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/relation_edges.py#L92-L94)) - Function
  - `agents.relation_edges._relation_edges_unmoved` ([L97-L106](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/relation_edges.py#L97-L106)) - Function
  - `agents.relation_edges._edge_touches_changed_method` ([L109-L110](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/relation_edges.py#L109-L110)) - Function
  - `agents.relation_edges._reconcile_unchanged_edges` ([L113-L137](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/relation_edges.py#L113-L137)) - Function
  - `agents.relation_edges._reconcile_unchanged_edges.split` ([L127-L130](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/relation_edges.py#L127-L130)) - Function
  - `agents.relation_edges.preserve_unchanged_relations` ([L140-L206](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/relation_edges.py#L140-L206)) - Function
  - `agents.relation_edges.preserve_unchanged_relations.touches_change` ([L173-L174](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/relation_edges.py#L173-L174)) - Function
- [`codeboarding_workflows/rendering.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingcodeboarding_workflows/rendering.py)
  - `codeboarding_workflows.rendering._ancestor_in_level` ([L27-L32](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingcodeboarding_workflows/rendering.py#L27-L32)) - Function
  - `codeboarding_workflows.rendering.project_relations_to_level` ([L35-L62](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingcodeboarding_workflows/rendering.py#L35-L62)) - Function
- [`diagram_analysis/analysis_json.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/analysis_json.py)
  - `diagram_analysis.analysis_json.ComponentJson` ([L46-L70](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/analysis_json.py#L46-L70)) - Class
  - `diagram_analysis.analysis_json._extract_analysis_recursive` ([L582-L673](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/analysis_json.py#L582-L673)) - Function
- [`output_generators/html.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html.py)
  - `output_generators.html.generate_cytoscape_data` ([L10-L56](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html.py#L10-L56)) - Function
  - `output_generators.html.generate_html` ([L59-L125](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html.py#L59-L125)) - Function
  - `output_generators.html.generate_html_file` ([L128-L152](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html.py#L128-L152)) - Function
  - `output_generators.html.component_header_html` ([L155-L163](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html.py#L155-L163)) - Function
- [`output_generators/html_template.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html_template.py)
  - `output_generators.html_template._generate_css_styles` ([L4-L86](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html_template.py#L4-L86)) - Function
  - `output_generators.html_template._generate_html_body` ([L89-L122](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html_template.py#L89-L122)) - Function
  - `output_generators.html_template._get_library_checks` ([L125-L145](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html_template.py#L125-L145)) - Function
  - `output_generators.html_template._get_dagre_registration` ([L148-L159](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html_template.py#L148-L159)) - Function
  - `output_generators.html_template._get_cytoscape_style` ([L162-L221](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html_template.py#L162-L221)) - Function
  - `output_generators.html_template._get_layout_config` ([L224-L235](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html_template.py#L224-L235)) - Function
  - `output_generators.html_template._get_event_handlers` ([L238-L285](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html_template.py#L238-L285)) - Function
  - `output_generators.html_template._get_control_functions` ([L288-L314](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html_template.py#L288-L314)) - Function
  - `output_generators.html_template._generate_cytoscape_script` ([L317-L360](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html_template.py#L317-L360)) - Function
  - `output_generators.html_template.populate_html_template` ([L363-L385](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html_template.py#L363-L385)) - Function
- [`output_generators/markdown.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/markdown.py)
  - `output_generators.markdown.generated_mermaid_str` ([L9-L40](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/markdown.py#L9-L40)) - Function
  - `output_generators.markdown.generate_markdown` ([L43-L122](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/markdown.py#L43-L122)) - Function
  - `output_generators.markdown.generate_markdown_file` ([L125-L146](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/markdown.py#L125-L146)) - Function
  - `output_generators.markdown.component_header` ([L149-L157](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/markdown.py#L149-L157)) - Function
- [`output_generators/mdx.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/mdx.py)
  - `output_generators.mdx.generated_mermaid_str` ([L8-L35](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/mdx.py#L8-L35)) - Function
  - `output_generators.mdx.generate_frontmatter` ([L38-L49](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/mdx.py#L38-L49)) - Function
  - `output_generators.mdx.generate_mdx` ([L52-L158](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/mdx.py#L52-L158)) - Function
  - `output_generators.mdx.generate_mdx_file` ([L161-L183](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/mdx.py#L161-L183)) - Function
  - `output_generators.mdx.component_header` ([L186-L194](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/mdx.py#L186-L194)) - Function
- [`output_generators/sphinx.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/sphinx.py)
  - `output_generators.sphinx.generated_mermaid_str` ([L8-L43](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/sphinx.py#L8-L43)) - Function
  - `output_generators.sphinx.generate_rst` ([L46-L159](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/sphinx.py#L46-L159)) - Function
  - `output_generators.sphinx.generate_rst_file` ([L162-L187](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/sphinx.py#L162-L187)) - Function
  - `output_generators.sphinx.component_header` ([L190-L201](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/sphinx.py#L190-L201)) - Function
- [`static_analyzer/cluster_relations.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_relations.py)
  - `static_analyzer.cluster_relations.build_global_node_to_component_map` ([L44-L54](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_relations.py#L44-L54)) - Function
  - `static_analyzer.cluster_relations._qnames_match` ([L57-L67](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_relations.py#L57-L67)) - Function
  - `static_analyzer.cluster_relations.ground_relation_edges` ([L70-L107](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_relations.py#L70-L107)) - Function
  - `static_analyzer.cluster_relations.iter_ancestor_ids` ([L151-L155](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_relations.py#L151-L155)) - Function
  - `static_analyzer.cluster_relations._collect_component_names` ([L163-L169](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_relations.py#L163-L169)) - Function
  - `static_analyzer.cluster_relations._collect_authoritative_relations` ([L172-L185](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_relations.py#L172-L185)) - Function
  - `static_analyzer.cluster_relations._ancestor_relation` ([L188-L200](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_relations.py#L188-L200)) - Function
  - `static_analyzer.cluster_relations._relation_key_edges_for_pair` ([L203-L216](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_relations.py#L203-L216)) - Function
  - `static_analyzer.cluster_relations.build_global_relations` ([L219-L281](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_relations.py#L219-L281)) - Function
  - `static_analyzer.cluster_relations.merge_relations` ([L284-L378](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_relations.py#L284-L378)) - Function
- [`static_analyzer/constants.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/constants.py)
  - `static_analyzer.constants.ClusteringConfig` ([L58-L92](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/constants.py#L58-L92)) - Class
  - `static_analyzer.constants.NodeType` ([L95-L146](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/constants.py#L95-L146)) - Class
  - `static_analyzer.constants.NodeType.label` ([L132-L134](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/constants.py#L132-L134)) - Method
  - `static_analyzer.constants.NodeType.from_name` ([L137-L146](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/constants.py#L137-L146)) - Method
- [`static_analyzer/engine/result_converter.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/result_converter.py)
  - `static_analyzer.engine.result_converter._map_symbol_kind` ([L204-L213](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/engine/result_converter.py#L204-L213)) - Function
- [`static_analyzer/node.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/node.py)
  - `static_analyzer.node.Node.__init__` ([L12-L27](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/node.py#L12-L27)) - Method
- [`utils.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingutils.py)
  - `utils.sanitize` ([L92-L94](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingutils.py#L92-L94)) - Function


### Incremental State & Content Fingerprinting [[Expand]](./Incremental_State_Content_Fingerprinting.md)
Manages the physical mapping of code symbols to architectural models through granular hashing and indexing to detect architectural deltas.


**Related Classes/Methods**:

- `agents.content_hash.hash_method_body`:52-61
- `agents.file_index_models.FileEntry`:55-114
- `agents.incremental_agent._patch_file_methods`:635-682
- `agents.content_hash.MethodRef`:25-30



**Source Files:**

- [`agents/cluster_methods_mixin.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_methods_mixin.py)
  - `agents.cluster_methods_mixin.ClusterMethodsMixin._build_file_methods_from_nodes` ([L441-L495](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_methods_mixin.py#L441-L495)) - Method
  - `agents.cluster_methods_mixin.ClusterMethodsMixin._build_file_methods_from_nodes._is_more_specific` ([L455-L465](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/cluster_methods_mixin.py#L455-L465)) - Function
- [`agents/content_hash.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/content_hash.py)
  - `agents.content_hash.MethodRef` ([L25-L30](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/content_hash.py#L25-L30)) - Class
  - `agents.content_hash.MethodSpan` ([L33-L37](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/content_hash.py#L33-L37)) - Class
  - `agents.content_hash.read_source_lines` ([L40-L49](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/content_hash.py#L40-L49)) - Function
  - `agents.content_hash.hash_method_body` ([L52-L61](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/content_hash.py#L52-L61)) - Function
  - `agents.content_hash.hash_whole_file` ([L64-L68](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/content_hash.py#L64-L68)) - Function
  - `agents.content_hash.hash_file_residual` ([L71-L90](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/content_hash.py#L71-L90)) - Function
- [`agents/file_index_models.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/file_index_models.py)
  - `agents.file_index_models.MethodEntry` ([L14-L42](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/file_index_models.py#L14-L42)) - Class
  - `agents.file_index_models.MethodEntry.__hash__` ([L26-L27](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/file_index_models.py#L26-L27)) - Method
  - `agents.file_index_models.MethodEntry.__eq__` ([L29-L32](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/file_index_models.py#L29-L32)) - Method
  - `agents.file_index_models.MethodEntry.from_node` ([L35-L42](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/file_index_models.py#L35-L42)) - Method
  - `agents.file_index_models.FileMethodGroup` ([L45-L52](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/file_index_models.py#L45-L52)) - Class
  - `agents.file_index_models.FileEntry` ([L55-L114](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/file_index_models.py#L55-L114)) - Class
  - `agents.file_index_models.FileEntry.merge_from` ([L75-L103](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/file_index_models.py#L75-L103)) - Method
  - `agents.file_index_models.FileEntry.merge_method_spans` ([L105-L114](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/file_index_models.py#L105-L114)) - Method
- [`agents/incremental_agent.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/incremental_agent.py)
  - `agents.incremental_agent._patch_file_methods` ([L635-L682](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/incremental_agent.py#L635-L682)) - Function
  - `agents.incremental_agent._without_methods` ([L685-L700](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/incremental_agent.py#L685-L700)) - Function
  - `agents.incremental_agent._merge_file_method_groups` ([L703-L724](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/incremental_agent.py#L703-L724)) - Function
  - `agents.incremental_agent._method_physical_key` ([L727-L729](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/incremental_agent.py#L727-L729)) - Function
- [`agents/relation_edges.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/relation_edges.py)
  - `agents.relation_edges.merge_relations_by_pair` ([L26-L30](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/relation_edges.py#L26-L30)) - Function
  - `agents.relation_edges.index_relation_endpoints` ([L33-L52](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/relation_edges.py#L33-L52)) - Function
- [`codeboarding_workflows/rendering.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingcodeboarding_workflows/rendering.py)
  - `codeboarding_workflows.rendering._load_entries` ([L75-L101](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingcodeboarding_workflows/rendering.py#L75-L101)) - Function
- [`diagram_analysis/analysis_json.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/analysis_json.py)
  - `diagram_analysis.analysis_json.RelationEdgeJson` ([L23-L27](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/analysis_json.py#L23-L27)) - Class
  - `diagram_analysis.analysis_json.RelationJson` ([L30-L43](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/analysis_json.py#L30-L43)) - Class
  - `diagram_analysis.analysis_json.FileCoverageSummary` ([L78-L84](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/analysis_json.py#L78-L84)) - Class
  - `diagram_analysis.analysis_json.AnalysisMetadata` ([L95-L113](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/analysis_json.py#L95-L113)) - Class
  - `diagram_analysis.analysis_json.MethodIndexEntry` ([L116-L125](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/analysis_json.py#L116-L125)) - Class
  - `diagram_analysis.analysis_json.ComponentFileMethodGroupJson` ([L128-L133](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/analysis_json.py#L128-L133)) - Class
  - `diagram_analysis.analysis_json.FileEntryJson` ([L136-L153](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/analysis_json.py#L136-L153)) - Class
  - `diagram_analysis.analysis_json.UnifiedAnalysisJson` ([L156-L170](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/analysis_json.py#L156-L170)) - Class
  - `diagram_analysis.analysis_json._build_files_index_from_analysis` ([L173-L186](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/analysis_json.py#L173-L186)) - Function
  - `diagram_analysis.analysis_json._method_key` ([L189-L191](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/analysis_json.py#L189-L191)) - Function
  - `diagram_analysis.analysis_json._source_reference_method_key` ([L194-L196](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/analysis_json.py#L194-L196)) - Function
  - `diagram_analysis.analysis_json._relativize_key_entities` ([L199-L213](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/analysis_json.py#L199-L213)) - Function
  - `diagram_analysis.analysis_json._relation_edge_to_json` ([L216-L222](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/analysis_json.py#L216-L222)) - Function
  - `diagram_analysis.analysis_json._to_component_file_method_refs` ([L225-L237](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/analysis_json.py#L225-L237)) - Function
  - `diagram_analysis.analysis_json._method_refs_to_placeholders` ([L240-L249](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/analysis_json.py#L240-L249)) - Function
  - `diagram_analysis.analysis_json._build_methods_index_from_files` ([L252-L264](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/analysis_json.py#L252-L264)) - Function
  - `diagram_analysis.analysis_json._build_file_entry_json_from_files` ([L267-L276](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/analysis_json.py#L267-L276)) - Function
  - `diagram_analysis.analysis_json._hydrate_component_methods_from_refs` ([L279-L311](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/analysis_json.py#L279-L311)) - Function
  - `diagram_analysis.analysis_json._relation_to_json` ([L314-L326](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/analysis_json.py#L314-L326)) - Function
  - `diagram_analysis.analysis_json.from_component_to_json_component` ([L329-L383](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/analysis_json.py#L329-L383)) - Function
  - `diagram_analysis.analysis_json.from_analysis_to_json` ([L386-L412](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/analysis_json.py#L386-L412)) - Function
  - `diagram_analysis.analysis_json._compute_depth_level` ([L415-L456](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/analysis_json.py#L415-L456)) - Function
  - `diagram_analysis.analysis_json._compute_depth_level.get_depth` ([L426-L436](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/analysis_json.py#L426-L436)) - Function
  - `diagram_analysis.analysis_json.build_unified_analysis_json` ([L459-L510](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/analysis_json.py#L459-L510)) - Function
  - `diagram_analysis.analysis_json.parse_unified_analysis` ([L513-L539](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/analysis_json.py#L513-L539)) - Function
  - `diagram_analysis.analysis_json._reconstruct_files_index` ([L542-L570](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/analysis_json.py#L542-L570)) - Function
  - `diagram_analysis.analysis_json.build_id_to_name_map` ([L573-L579](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/analysis_json.py#L573-L579)) - Function
- [`diagram_analysis/cluster_delta.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/cluster_delta.py)
  - `diagram_analysis.cluster_delta.ChangedMembers` ([L122-L139](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/cluster_delta.py#L122-L139)) - Class
  - `diagram_analysis.cluster_delta.compute_changed_members` ([L142-L219](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/cluster_delta.py#L142-L219)) - Function
  - `diagram_analysis.cluster_delta._live_member_hashes` ([L222-L252](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/cluster_delta.py#L222-L252)) - Function
  - `diagram_analysis.cluster_delta._delta_for_language._fresh_file` ([L605-L610](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/cluster_delta.py#L605-L610)) - Function
  - `diagram_analysis.cluster_delta._delta_for_language._old_file` ([L612-L617](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/cluster_delta.py#L612-L617)) - Function
- [`diagram_analysis/diagram_generator.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/diagram_generator.py)
  - `diagram_analysis.diagram_generator._reconcile_child_scope` ([L113-L147](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/diagram_generator.py#L113-L147)) - Function
  - `diagram_analysis.diagram_generator._graft_entered_methods` ([L150-L171](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/diagram_generator.py#L150-L171)) - Function
  - `diagram_analysis.diagram_generator._append_method` ([L174-L180](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/diagram_generator.py#L174-L180)) - Function
  - `diagram_analysis.diagram_generator.DiagramGenerator._refresh_files_index` ([L1506-L1524](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/diagram_generator.py#L1506-L1524)) - Method
  - `diagram_analysis.diagram_generator._child_scope_needs_recursive_update` ([L1596-L1622](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/diagram_generator.py#L1596-L1622)) - Function
- [`diagram_analysis/file_index.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/file_index.py)
  - `diagram_analysis.file_index.build_files_index` ([L21-L65](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/file_index.py#L21-L65)) - Function
  - `diagram_analysis.file_index.refresh_method_spans_from_cfg` ([L68-L82](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/file_index.py#L68-L82)) - Function
  - `diagram_analysis.file_index._cfg_method_spans` ([L85-L98](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/file_index.py#L85-L98)) - Function
- [`repo_utils/path_utils.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingrepo_utils/path_utils.py)
  - `repo_utils.path_utils.normalize_repo_path` ([L5-L20](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingrepo_utils/path_utils.py#L5-L20)) - Function
  - `repo_utils.path_utils.to_relative_path` ([L23-L32](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingrepo_utils/path_utils.py#L23-L32)) - Function
  - `repo_utils.path_utils.to_absolute_path` ([L35-L41](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingrepo_utils/path_utils.py#L35-L41)) - Function




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)