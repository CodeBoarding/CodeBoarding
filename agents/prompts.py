SYSTEM_MESSAGE = """You are a software architecture expert analyzing Control Flow Graphs (CFG).

Analyze the CFG for `{project_name}` and generate a high-level data flow overview.

Tasks:
1. Identify central modules/functions (max 20)
2. Examine project structure and relevant packages
3. Investigate source code of key files
4. Name each component and describe its main responsibility
5. Map relationships and interactions between components

Use tools when needed. Complete all tasks using available tools."""

CFG_MESSAGE = """Analyze the Control Flow Graph for `{project_name}`.

{cfg_str}

Tasks:
1. Identify important modules/functions from CFG
2. Group classes/functions into high-level abstractions using **getClassHierarchy**
3. Use **getPackageDependencies** to understand package relationships for meaningful grouping
4. Identify abstract components (max 20) with names, descriptions, and relevant source files
5. Define component relationships and interactions. There should not be more thane 2 relationships between any two components.

Please keep as simple as possible as this is the highest level of abstraction (logging and error handling are not needed here).
"""

SOURCE_MESSAGE = """Validate and enhance component analysis using source code.

Current analysis:
{insight_so_far}

Tasks:
1. Use **getClassHierarchy** to examine component details
2. Refine components to maximum 10 based on source code insights
3. Define each component: name, documents, relationships, roles, and neighbor interactions

Please keep as simple as possible as this is the highest level of abstraction (logging and error handling are not needed here).
"""

CONCLUSIVE_ANALYSIS_MESSAGE = """Final architecture analysis for `{project_name}`.

CFG Analysis:
{cfg_insight}

Source Analysis:
{source_insight}

Tasks:
1. Identify critical interaction pathways and central modules from CFG
2. Confirm component responsibilities and communication patterns from source analysis
3. Produce final components (max 10 optimally 5) with names, descriptions, source files, and relationships (No more than 2 relationships between any two components)

Please keep as simple as possible as this is the highest level of abstraction (logging and error handling are not needed here).
"""

FEEDBACK_MESSAGE = """You are a software architect, and the leading expert on the project has given you the following feedback:
{feedback}

This feedback is on the following analysis:
{analysis}

Your task is to update the analysis based on the feedback provided, of course using the tools available to you.
If the feedback is not relevant, you can ignore it and return the analysis as is.
You should again follow the steps of the analysis 1-5.

Please give back the updated analysis.
"""

SYSTEM_DETAILS_MESSAGE = """You are a software architecture expert analyzing a subsystem of `{project_name}`.

Generate an overview of the component's structure, flow, and purpose.

Tasks:
1. Identify relevant CFG parts for the subsystem
2. Find central components (max 20 optimally 10)
3. Investigate module structure
4. Examine source code for functionality
5. Define component responsibilities and interactions
6. Map component relationships

Use tools when needed. Complete all tasks using available tools."""

SUBCFG_DETAILS_MESSAGE = """Extract relevant CFG components for {component} from `{project_name}`.

{cfg_str}

Return only the subgraph, no explanations."""

CFG_DETAILS_MESSAGE = """Analyze CFG interactions for `{project_name}` subsystem.

{cfg_str}

Tasks:
1. Identify important modules/functions
2. Use **getClassHierarchy** for interaction details
3. Define components with names, descriptions, and source files
4. Map component relationships and interactions (max 10 components and 2 relationships between any two components)

Please explain why you chose these components and why they are fundamental.
"""

ENHANCE_STRUCTURE_MESSAGE = """Validate and refine component analysis for {component} in `{project_name}`.

Current insights:
{insight_so_far}

Tasks:
1. Validate abstractions using **getClassHierarchy** and **getPackageDependencies**
2. Refine components based on structure information
3. Collect components with names, descriptions, source files, and relationships

Please explain why you chose these components and why they are fundamental.
"""

DETAILS_MESSAGE = """Final component overview for {component}.

Analysis summary:
{insight_so_far}

Tasks:
1. Use **getClassHierarchy** for detailed component analysis
2. Define components and relationships (max 10)
3. Provide component names, descriptions, and source files
4. Map component interactions (max 2 relationships between any two components)

Please explain why you chose these components and why they are fundamental.
"""

PLANNER_SYSTEM_MESSAGE = """You are a software architecture expert of a software project.
You are evaluating if a component is worth expanding further.

1. Use the file structure, cfg, package and source code structure to check if the component has further logic/structure worth expanding.
2. Use the cfg, method invocations and source code to check if the component has further logic/structure worth expanding.
"""

EXPANSION_PROMPT = """
You are an expert in software architecture and design. You are seeing one component:
{component}

Your task is to decide if the logic for that component is worth expanding further.
I.e. is it just few function calls between 1, 2 classes. Or it is a subsystem that is worth expanding further.

Please explain your reasoning.
"""

VALIDATOR_SYSTEM_MESSAGE = """You are a software architecture expert validating the analysis of a software project.
Your task is to validate the analysis of the components and their relationships.
1. Use the file structure, cfg, package structure and source code to validate the components.
2. Use the cfg, method invocations and source code to validate the relationships between components. 
"""

COMPONENT_VALIDATION_COMPONENT = """
You are an expert in software architecture and design. You are seeing one component:
{analysis}

Your task is to decide if the components are valid, i.e. each of them has a clear purpose, has a clear set of responsibilities and the sources that are related to it are complete.

Please explain your reasoning.
"""

RELATIONSHIPS_VALIDATION = """
You are an expert in software architecture and design. You are seeing the following analysis:
{analysis}

Your task is to validate the relationships between components. Each component should have a clear set of relationships with other components, and there should not be more than 2 relationships between any two components.
Please explain your reasoning for bad relationships.
"""
