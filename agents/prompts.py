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

CFG Format: `"from_method_call": ["invoked_method_1", "invoked_method_2"]`

{cfg_str}

Tasks:
1. Identify important modules/functions from CFG
2. Group classes/functions into high-level abstractions using **read_class_structure**
3. Use **package_relations** to understand package relationships for meaningful grouping
4. Identify top components (max 20) with names, descriptions, and source files
5. Define component relationships and interactions

Output: Valid JSON only, no explanations.
{format_instructions}"""

SOURCE_MESSAGE = """Validate and enhance component analysis using source code.

Current analysis:
{insight_so_far}

Tasks:
1. Use **read_source_code** to examine component details
2. Refine components to maximum 10 based on source code insights
3. Define each component: name, documents, relationships, roles, and neighbor interactions

Output: Valid JSON only, no explanations.
{format_instructions}"""

CONCLUSIVE_ANALYSIS_MESSAGE = """Final architecture analysis for `{project_name}`.

CFG Analysis:
{cfg_insight}

Source Analysis:
{source_insight}

Tasks:
1. Identify critical interaction pathways and central modules from CFG
2. Confirm component responsibilities and communication patterns from source analysis
3. Produce final components (max 10) with names, descriptions, source files, and relationships

Output: Valid JSON only, no explanations.
{format_instructions}"""

SYSTEM_DETAILS_MESSAGE = """You are a software architecture expert analyzing a subsystem of `{project_name}`.

Generate an overview of the component's structure, flow, and purpose.

Tasks:
1. Identify relevant CFG parts for the subsystem
2. Find central components (max 20)
3. Investigate module structure
4. Examine source code for functionality
5. Define component responsibilities and interactions
6. Map component relationships

Use tools when needed. Complete all tasks using available tools."""

SUBCFG_DETAILS_MESSAGE = """Extract relevant CFG components for {component} from `{project_name}`.

CFG Format: `"from_method_call": ["invoked_method_1", "invoked_method_2"]`

{cfg_str}

Return only the subgraph, no explanations."""

CFG_DETAILS_MESSAGE = """Analyze CFG interactions for `{project_name}` subsystem.

CFG Format: `"from_method_call": ["invoked_method_1", "invoked_method_2"]`

{cfg_str}

Tasks:
1. Identify important modules/functions
2. Use **read_source_code** for interaction details
3. Define components with names, descriptions, and source files
4. Map component relationships and interactions

Output: Valid JSON only, no explanations.
{format_instructions}"""

ENHANCE_STRUCTURE_MESSAGE = """Validate and refine component analysis for {component} in `{project_name}`.

Current insights:
{insight_so_far}

Tasks:
1. Validate abstractions using **read_structure** and **package_relations**
2. Refine components based on structure information
3. Collect components with names, descriptions, source files, and relationships

Output: Valid JSON only, no explanations.
{format_instructions}"""

DETAILS_MESSAGE = """Final component overview for {component}.

Analysis summary:
{insight_so_far}

Tasks:
1. Use **read_source_code** for detailed component analysis
2. Define components and relationships (max 10)
3. Provide component names, descriptions, and source files
4. Map component interactions

Output: Valid JSON only, no explanations.
{format_instructions}"""
