```mermaid
graph LR
    Component_Schema_Structural_Definitions["Component Schema & Structural Definitions"]
    Architectural_Surface_Visualization_Generator["Architectural Surface & Visualization Generator"]
    Relational_Logic_Edge_Reconciliation["Relational Logic & Edge Reconciliation"]
    Component_Schema_Structural_Definitions -- "provides the data contract for visualization" --> Architectural_Surface_Visualization_Generator
    Component_Schema_Structural_Definitions -- "defines structural domain for graph reconciliation" --> Relational_Logic_Edge_Reconciliation
    Relational_Logic_Edge_Reconciliation -- "Supplies reconciled dependency data for rendering" --> Architectural_Surface_Visualization_Generator
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Web platform](https://img.shields.io/badge/Open%20in-Web%20platform-2563EB?style=flat-square)](https://app.codeboarding.org)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Defines the structural schema for architectural components, including their public API surfaces, file memberships, and inter-component relationships.

### Component Schema & Structural Definitions
Defines the core Pydantic models and data structures that represent the codebase's architectural building blocks, establishing the schema for file classifications and component boundaries.


**Related Classes/Methods**:

- `agents.agent_responses.Component`:446-497
- `agents.agent_responses.FileClassification`:716-723
- `agents.agent_responses.ScopeOperation`:760-790



**Source Files:**

- [`agents/agent_responses.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py)
  - `agents.agent_responses.LLMBaseModel` ([L23-L128](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L23-L128)) - Class
  - `agents.agent_responses.LLMBaseModel.llm_str` ([L27-L28](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L27-L28)) - Method
  - `agents.agent_responses.LLMBaseModel._is_field_hidden` ([L31-L37](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L31-L37)) - Method
  - `agents.agent_responses.LLMBaseModel._excluded_fields` ([L40-L49](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L40-L49)) - Method
  - `agents.agent_responses.LLMBaseModel._resolve_excluded_by_title` ([L52-L71](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L52-L71)) - Method
  - `agents.agent_responses.LLMBaseModel._resolve_excluded_by_title.walk` ([L56-L68](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L56-L68)) - Function
  - `agents.agent_responses.LLMBaseModel._extractor_fields` ([L74-L93](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L74-L93)) - Method
  - `agents.agent_responses.LLMBaseModel.extractor_str` ([L96-L103](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L96-L103)) - Method
  - `agents.agent_responses.LLMBaseModel.model_json_schema` ([L106-L128](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L106-L128)) - Method
  - `agents.agent_responses.SourceCodeReference` ([L131-L170](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L131-L170)) - Class
  - `agents.agent_responses.SourceCodeReference.__str__` ([L162-L170](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L162-L170)) - Method
  - `agents.agent_responses.RelationEdge` ([L185-L244](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L185-L244)) - Class
  - `agents.agent_responses.RelationEdge.from_dict` ([L199-L210](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L199-L210)) - Method
  - `agents.agent_responses.RelationEdge.from_edge` ([L213-L228](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L213-L228)) - Method
  - `agents.agent_responses._relation_endpoint_from_key` ([L247-L266](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L247-L266)) - Function
  - `agents.agent_responses.Relation.llm_str` ([L323-L324](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L323-L324)) - Method
  - `agents.agent_responses.Component` ([L446-L497](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L446-L497)) - Class
  - `agents.agent_responses.Component.file_paths` ([L483-L485](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L483-L485)) - Method
  - `agents.agent_responses.AnalysisInsights` ([L500-L521](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L500-L521)) - Class
  - `agents.agent_responses.ComponentApiSurfaces` ([L577-L585](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L577-L585)) - Class
  - `agents.agent_responses.ComponentRelations` ([L588-L596](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L588-L596)) - Class
  - `agents.agent_responses.ComponentRelations.llm_str` ([L593-L596](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L593-L596)) - Method
  - `agents.agent_responses.FileClassification` ([L716-L723](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L716-L723)) - Class
  - `agents.agent_responses.FileClassification.llm_str` ([L722-L723](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L722-L723)) - Method
  - `agents.agent_responses.ComponentFiles` ([L726-L738](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L726-L738)) - Class
  - `agents.agent_responses.ComponentFiles.llm_str` ([L733-L738](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L733-L738)) - Method
  - `agents.agent_responses.ScopedClusterRef` ([L748-L757](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L748-L757)) - Class
  - `agents.agent_responses.ScopedClusterRef.llm_str` ([L755-L757](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L755-L757)) - Method
  - `agents.agent_responses.ScopeOperation` ([L760-L790](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L760-L790)) - Class
  - `agents.agent_responses.ScopeOperation.llm_str` ([L783-L790](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L783-L790)) - Method
  - `agents.agent_responses.ScopeUpdateDecision` ([L793-L801](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L793-L801)) - Class
  - `agents.agent_responses.ScopeUpdateDecision.llm_str` ([L798-L801](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L798-L801)) - Method
- [`diagram_analysis/analysis_json.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/analysis_json.py)
  - `diagram_analysis.analysis_json.ComponentJson` ([L46-L70](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/analysis_json.py#L46-L70)) - Class
  - `diagram_analysis.analysis_json._extract_analysis_recursive` ([L582-L673](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/analysis_json.py#L582-L673)) - Function


### Architectural Surface & Visualization Generator
Responsible for defining the public API surfaces of components and transforming internal models into human-readable and interactive formats like HTML, Markdown, and Cytoscape.


**Related Classes/Methods**:

- `agents.agent_responses.ComponentApiSurface`:540-574
- `output_generators.html.generate_html`:59-125
- `output_generators.html_template._generate_cytoscape_script`:317-360
- `output_generators.markdown.generate_markdown`:43-122



**Source Files:**

- [`agents/agent_responses.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py)
  - `agents.agent_responses.SourceCodeReference.llm_str` ([L152-L160](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L152-L160)) - Method
  - `agents.agent_responses.ComponentApiSurface` ([L540-L574](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L540-L574)) - Class
  - `agents.agent_responses.ComponentApiSurface.llm_str` ([L562-L574](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L562-L574)) - Method
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


### Relational Logic & Edge Reconciliation
Manages the logic of inter-component relationships, ensuring that low-level code calls are correctly mapped to high-level component relations during codebase evolution.


**Related Classes/Methods**:

- `agents.agent_responses.Relation`:269-377
- `agents.relation_edges.preserve_unchanged_relations`:200-291
- `agents.relation_edges.append_or_merge_relation`:9-23
- `codeboarding_workflows.rendering.project_relations_to_level`:35-62



**Source Files:**

- [`agents/agent_responses.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py)
  - `agents.agent_responses.RelationEdge.identity` ([L233-L244](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L233-L244)) - Method
  - `agents.agent_responses.Relation` ([L269-L377](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L269-L377)) - Class
  - `agents.agent_responses.Relation.from_edges` ([L300-L321](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L300-L321)) - Method
  - `agents.agent_responses.Relation.pair_key` ([L326-L331](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L326-L331)) - Method
  - `agents.agent_responses.Relation.with_merged_edges` ([L333-L345](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L333-L345)) - Method
  - `agents.agent_responses.Relation.merge_edges_from` ([L347-L353](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L347-L353)) - Method
  - `agents.agent_responses.Relation._merge_edges` ([L356-L361](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L356-L361)) - Method
  - `agents.agent_responses.Relation._unique_edges` ([L364-L373](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L364-L373)) - Method
  - `agents.agent_responses.Relation.edge_count` ([L376-L377](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/agent_responses.py#L376-L377)) - Method
- [`agents/relation_edges.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/relation_edges.py)
  - `agents.relation_edges.append_or_merge_relation` ([L9-L23](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/relation_edges.py#L9-L23)) - Function
  - `agents.relation_edges._is_internal_self_relation` ([L55-L69](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/relation_edges.py#L55-L69)) - Function
  - `agents.relation_edges.drop_internal_self_relations` ([L72-L74](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/relation_edges.py#L72-L74)) - Function
  - `agents.relation_edges._relation_backing_survives` ([L77-L90](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/relation_edges.py#L77-L90)) - Function
  - `agents.relation_edges._backing_edge_pairs` ([L93-L96](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/relation_edges.py#L93-L96)) - Function
  - `agents.relation_edges._relation_edges_unmoved` ([L99-L108](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/relation_edges.py#L99-L108)) - Function
  - `agents.relation_edges._edge_touches_changed_method` ([L111-L112](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/relation_edges.py#L111-L112)) - Function
  - `agents.relation_edges._restore_baseline_orientation` ([L115-L143](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/relation_edges.py#L115-L143)) - Function
  - `agents.relation_edges._filter_edges_touched_by_change` ([L146-L159](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/relation_edges.py#L146-L159)) - Function
  - `agents.relation_edges._commit_deleted_the_backing` ([L162-L170](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/relation_edges.py#L162-L170)) - Function
  - `agents.relation_edges._reconcile_unchanged_edges` ([L173-L197](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/relation_edges.py#L173-L197)) - Function
  - `agents.relation_edges._reconcile_unchanged_edges.split` ([L187-L190](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/relation_edges.py#L187-L190)) - Function
  - `agents.relation_edges.preserve_unchanged_relations` ([L200-L291](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/relation_edges.py#L200-L291)) - Function
  - `agents.relation_edges.preserve_unchanged_relations.touches_change` ([L236-L237](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingagents/relation_edges.py#L236-L237)) - Function
- [`codeboarding_workflows/rendering.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingcodeboarding_workflows/rendering.py)
  - `codeboarding_workflows.rendering._ancestor_in_level` ([L27-L32](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingcodeboarding_workflows/rendering.py#L27-L32)) - Function
  - `codeboarding_workflows.rendering.project_relations_to_level` ([L35-L62](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingcodeboarding_workflows/rendering.py#L35-L62)) - Function
- [`diagram_analysis/diagram_generator.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/diagram_generator.py)
  - `diagram_analysis.diagram_generator.DiagramGenerator.rebuild_global_relations` ([L1156-L1210](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingdiagram_analysis/diagram_generator.py#L1156-L1210)) - Method
- [`static_analyzer/cluster_relations.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_relations.py)
  - `static_analyzer.cluster_relations.build_global_node_to_component_map` ([L44-L54](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_relations.py#L44-L54)) - Function
  - `static_analyzer.cluster_relations._qnames_match` ([L57-L67](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_relations.py#L57-L67)) - Function
  - `static_analyzer.cluster_relations.build_owner_index` ([L70-L80](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_relations.py#L70-L80)) - Function
  - `static_analyzer.cluster_relations._endpoint_owner` ([L83-L91](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_relations.py#L83-L91)) - Function
  - `static_analyzer.cluster_relations.edge_crosses_components` ([L94-L113](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_relations.py#L94-L113)) - Function
  - `static_analyzer.cluster_relations.prune_ungrounded_edges` ([L116-L195](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_relations.py#L116-L195)) - Function
  - `static_analyzer.cluster_relations.prune_ungrounded_edges.find_relation_pair_for_edge` ([L142-L159](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_relations.py#L142-L159)) - Function
  - `static_analyzer.cluster_relations.drop_reverse_duplicates` ([L198-L254](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_relations.py#L198-L254)) - Function
  - `static_analyzer.cluster_relations.ground_relation_edges` ([L257-L294](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_relations.py#L257-L294)) - Function
  - `static_analyzer.cluster_relations.iter_ancestor_ids` ([L338-L342](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_relations.py#L338-L342)) - Function
  - `static_analyzer.cluster_relations._collect_component_names` ([L350-L356](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_relations.py#L350-L356)) - Function
  - `static_analyzer.cluster_relations._collect_authoritative_relations` ([L359-L372](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_relations.py#L359-L372)) - Function
  - `static_analyzer.cluster_relations._ancestor_relation` ([L375-L387](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_relations.py#L375-L387)) - Function
  - `static_analyzer.cluster_relations._relation_key_edges_for_pair` ([L390-L403](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_relations.py#L390-L403)) - Function
  - `static_analyzer.cluster_relations.build_global_relations` ([L406-L480](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_relations.py#L406-L480)) - Function
  - `static_analyzer.cluster_relations.merge_relations` ([L483-L582](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingstatic_analyzer/cluster_relations.py#L483-L582)) - Function




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)