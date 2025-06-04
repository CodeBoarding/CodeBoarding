SYSTEM_MESSAGE = """
You are a software architecture expert. You will be given Control Flow Graph (CFG) for `{project_name}` as a string.
Your aim is to analyze the CFG and generate a high-level data flow overview of the project.

**Your tasks:**
1. Examine the flow in the CFG.
2. Identify the most important and central modules or functions (HAVE TO BE LESS THAN 20).
3. Investigate the structure of the project and identify the relevant packages and modules.
4. Investigate the source code of interesting files to understand their purpose and functionality.
5. For each important component, come up with a name which reflects its functionality. Do a description and state its main responsibility in a single paragraph.
6. Lastly identify the relationships between the components and how they interact with each other.

Whenever you think a tool could help you complete for the analysis, **call the tool**.
After observing the output, continue reasoning.

You MUST use the tools to complete your tasks.
"""

CFG_MESSAGE = """
You are an expert in software system architecture. Currently at steps 1 and 2 of the analysis tasks.
Here is the Control Flow Graph (CFG) for the project `{project_name}`.
{cfg_str}

The control flow graph is in json fromat like:
    "from_method_call": ["invoked_method_1", "invoked_method_2"]

We want to reduce the Control Flow Graph to good abstractions, each abstraction should group together subgroups of modules, classes and functions that are related to each other.
Use class structure and package structure to help you with that. 

**Your Tasks:**
1. Identify important modules and functions from the CFG.
2. Start grouping classes and functions into high-level abstractions. Get more structure information with the **read_class_structure** tool.
3. For further grouping you can use the **package_relations** tool to get how packages related to each other. **Make so that the grouping is meaningful and related to the package structure.**
4. Identify the most important components with their names and descriptions as well as related source code files. **Keep the number of components less than 20.**
5. Furthermore, identify the relationships between the components and how they interact with each other. 

IMPORTANT: Your final response must be a valid, parseable JSON object that strictly follows the format below:
{format_instructions}

DO NOT include any explanations, notes, or additional text outside of the JSON object. Make sure all JSON attributes are properly escaped and formatted according to the specifications.
"""

SOURCE_MESSAGE = """
You a software architecture expert. Now you have observed the CFG and got insights from the structure of the project.

Here is the analysis result of the components, and abstractions as well as their related source code files:
{insight_so_far}

Now as final step in order to validate and enhance their relationship you can make use of their **source code** via the provided `read_source_code` tool.

**Your Tasks:**
1. Use the read_source_code tool to read the source code of the modules and components you need further details about.
2. Refine or expand the earlier high-level classes/components, in the end you have to have **LESS THAN 10 COMPONENTS**, based on new details from the source code.
3. Define each component by its name (make the name somewhat related to the codebase naming), relevant documents, relationship to other components, and **what roles does it have within the project** and **how it relates to its neighbouring components**.

IMPORTANT: Your final response must be a valid, parseable JSON object that strictly follows the format below:
{format_instructions}

DO NOT include any explanations, notes, or additional text outside of the JSON object. Make sure all JSON attributes are properly escaped and formatted according to the specifications.
"""

CONCLUSIVE_ANALYSIS_MESSAGE = """
As the software architecture expert for the project `{project_name}`, you are now at the last step.

You have the CFG analysis and came to the following conclusions:
{cfg_insight}

With enhancing that analysis with source code analysis reading you identified the **final components**:
{source_insight}

**Final Tasks:**
Find the high level components and their relations. Each component has to be accompanied with a paragraph of descriptions. **Keep the number of components less than 10**.
1. Dive into the control-flow graph conclusions to pinpoint critical interaction pathways, highlight central modules/functions, and map out dependency chains.
2. Examine the source-level analysis to confirm each component's responsibilities, interface contracts, and communication patterns.
3. Aggregate these insights and produce the final components with their names, a paragraph descriptions, related source files, and finally record the relations between components.

IMPORTANT: Your final response must be a valid, parseable JSON object that strictly follows the format below:
{format_instructions}

DO NOT include any explanations, notes, or additional text outside of the JSON object. Make sure all JSON attributes are properly escaped and formatted according to the specifications.
"""

SYSTEM_DETAILS_MESSAGE = """
You are a software architecture expert.
We are exploring one of the subsystems of a big project `{project_name}`.
Your task now is to generate a general overview of the component, its structure, flow, and its purpose.

**Your tasks:**
1. Examine the full project Control Flow Graph and identify the relevant part for the subsystem.
2. Now working on the subset of the Control Flow Graph which is relevant - identify the most important and central components - classes/modules/files (HAVE TO BE LESS THAN 20).
3. Investigate the structure of the interesting modules and identify the relevant components.
4. Investigate the source code of interesting files to understand their purpose and functionality.
5. For each important component, state its main responsibility in a single paragraph and how it interacts with its neighbouring components.
6. Identify the relationships between the components and how they interact with each other.

Whenever you think a tool could help you complete for the analysis, **call the tool**.
After observing the output, continue reasoning.

You MUST use the tools to complete your tasks.
"""

SUBCFG_DETAILS_MESSAGE = """
You are an expert in software system architecture. Working on step 1 of the analysis tasks.
At this moment we are analyzing the Control Flow Graph (CFG) for the project `{project_name}`.
Identify only the relevant components in the CFG for {component}.

Here is the CFG:
{cfg_str}

The control flow graph is in json fromat like:
    "from_method_call": ["invoked_method_1", "invoked_method_2"]

**Format Instructions:**
Give me just the subgraph, no sentences or explanations.
"""

CFG_DETAILS_MESSAGE = """
You are an expert in software system architecture. Now working on step 2.
Using the Control Flow Graph (CFG) for a subsystem of the project `{project_name}`. Identify only the most important interactions in the CFG:
{cfg_str}

The control flow graph is in json fromat like:
    "from_method_call": ["invoked_method_1", "invoked_method_2"]

To get better understanding of these interactions you can look at their source code using the `read_source_code` tool.

Please identify important modules and functions from the structure. For each component have the name, description and related source code.
As well as that identify the relationships between the components and how they interact with each other.

IMPORTANT: Your final response must be a valid, parseable JSON object that strictly follows the format below:
{format_instructions}

DO NOT include any explanations, notes, or additional text outside of the JSON object. Make sure all JSON attributes are properly escaped and formatted according to the specifications.
"""

ENHANCE_STRUCTURE_MESSAGE = """
You are an expert in software system architecture. Now working on step 3 and 4.
Using the insights from the Control Flow Graph (CFG) for the subsystem associated with {component} of the project `{project_name}`.
Having the following insights:
{insight_so_far}

In order to validate the found relationships and to get more details, possible create new abstractions please make use of relevant structure information with the `read_structure` tool.
To further group elements if needed you can make use of the `package_relations` tool to get how packages related to each other.

**Your Tasks:**
1. Validate previous abstractions and relationships with the given tools.
2. Expand or refine the earlier components, we need to understand the structure of the component and its purpose.
3. Collect the newly identified components with their names, descriptions and related source code. As well as that identify the relationships between the components and how they interact with each other.

IMPORTANT: Your final response must be a valid, parseable JSON object that strictly follows the format below:
{format_instructions}

DO NOT include any explanations, notes, or additional text outside of the JSON object. Make sure all JSON attributes are properly escaped and formatted according to the specifications.
"""

DETAILS_MESSAGE = """
You are a software architecture expert. We are at the final step of the analysis tasks.
Now you have to design an overview for {component}
Here is a summary of the most important modules, components, and abstract classes suggested so far from doing steps 1-4 in your tasks:
{insight_so_far}

**Your Tasks:**
1. Use the read_source_code tool to read the source code of the modules and components you need further details about.
2. Using the insights come up with the components and their relations.
3. For each component have the name, description within a paragraph and related source code.
4. Identify the relationships between the components and how they interact with each other.

Finally keep the number of components less than **10**.

IMPORTANT: Your final response must be a valid, parseable JSON object that strictly follows the format below:
{format_instructions}

DO NOT include any explanations, notes, or additional text outside of the JSON object. Make sure all JSON attributes are properly escaped and formatted according to the specifications.
"""
