```mermaid
graph LR
    Entity_Identity_Reference_Resolver["Entity Identity & Reference Resolver"]
    Dependency_Graph_Indexer["Dependency Graph Indexer"]
    Visual_Hierarchy_Container_Engine["Visual Hierarchy & Container Engine"]
    Entity_Identity_Reference_Resolver -- "validates and enriches edges with static truth" --> Dependency_Graph_Indexer
    Entity_Identity_Reference_Resolver -- "resolves entity locations within the hierarchy" --> Visual_Hierarchy_Container_Engine
    Dependency_Graph_Indexer -- "provides relational data for structural clustering" --> Visual_Hierarchy_Container_Engine
    Visual_Hierarchy_Container_Engine -- "constrains edge discovery to structural boundaries" --> Dependency_Graph_Indexer
```

[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/CodeBoarding)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/diagrams)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)

## Details

Prepares and indexes relational data for the UI, resolving qualified names, building cluster hierarchies, and mapping dependencies to visual containers.

### Entity Identity & Reference Resolver
Serves as the Source of Truth for entity identification, normalizing naming conventions and resolving ambiguous code references into stable, fully qualified names.


**Related Classes/Methods**: _None_


**Source Files:**

- [`output_generators/html_template.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html_template.py)
  - `output_generators.html_template._get_layout_config` ([L221-L232](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html_template.py#L221-L232)) - Function
  - `output_generators.html_template._get_control_functions` ([L285-L311](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html_template.py#L285-L311)) - Function


### Dependency Graph Indexer
Processes interaction data such as function calls, class inheritance, and module imports, indexing them against normalized entities to prepare the edge-list for visualization.


**Related Classes/Methods**: _None_


**Source Files:**

- [`output_generators/html_template.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html_template.py)
  - `output_generators.html_template._get_library_checks` ([L122-L142](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html_template.py#L122-L142)) - Function
  - `output_generators.html_template._get_cytoscape_style` ([L159-L218](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html_template.py#L159-L218)) - Function


### Visual Hierarchy & Container Engine
Organizes entities and relationships into a nested structure, defining containment logic to enable hierarchical architectural abstractions.


**Related Classes/Methods**: _None_


**Source Files:**

- [`output_generators/html_template.py`](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html_template.py)
  - `output_generators.html_template._get_dagre_registration` ([L145-L156](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html_template.py#L145-L156)) - Function
  - `output_generators.html_template._get_event_handlers` ([L235-L282](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html_template.py#L235-L282)) - Function
  - `output_generators.html_template.populate_html_template` ([L360-L382](https://github.com/CodeBoarding/CodeBoarding/blob/main/.codeboardingoutput_generators/html_template.py#L360-L382)) - Function




### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)