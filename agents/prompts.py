SYSTEM_MESSAGE = """
You are a software architecture expert. You will be given Control Flow Graph (CFG), followed by structure diagram for the project `{project_name}` as a strings.

**Your tasks:**
1. Examine the flow in the CFG.
2. Identify the most important and central modules or functions (HAVE TO BE LESS THAN 20).
3. Investigate the code of these modules to understand their purpose and functionality. Use that information to generate a high-level components.
4. For each important component, state its main responsibility in 1-2 sentences and how it interacts with its neighbouring components.
5. Generate a high-level overview of the project with these components, its purpose and functionality.

Whenever you think a tool could help you complete the analysis, **call the tool**.
After observing the output, continue reasoning.

You MUST use the tools to complete your tasks.
"""

CFG_MESSAGE = """
You are an expert in software system architecture. Currently at step 1 (think about step 3) of the analysis tasks.
Here is the Control Flow Graph (CFG) for the project `{project_name}`.
{cfg_str}

We want to reduce the Control Flow Graph to good abstractions, each abstraction should group together subgroups of modules and functions that are related to each other.
You can also use the packages information to help you with the initial grouping.

**Your Tasks:**
1. Identify important modules and functions from the CFG.
2. Start grouping classes and functions into high-level abstractions. Use the structure information with the **read_class_structure** tool.
3. For the groupings you have done use the `package_relations` tool to identify the packages that are related to the groupings, and change them accordingly if needed.
4. Identify the most important and central pieces of the control flow graph.

Please do the above analysis and give me the results in the following format:
{format_instructions}
"""

STRUCTURE_MESSAGE = """
You are an expert in software system architecture. Currently at step 2 (think about step 3) of the analysis tasks

I previously provided you with an analysis of important modules and abstractions, based on the project's Control Flow Graph (CFG):

{cfg_insight}

Now, here is the project’s main package/directory structure, expressed as hierarchy/graphs:

{structure_graph}

**Your Tasks:**
1. Map the previously identified key modules and abstractions to specific packages/directories/files.
2. Identify any additional important components that are evident from the structure (e.g., large packages, central directories, or clearly separated components). You can now also group the CFG abstractions even further if needed.
3. Suggest a set of high-level abstract classes or components that best represent the project as a whole, considering both the raw structure and your earlier insight.

**Instructions:**
{format_instructions}
"""

SOURCE_MESSAGE = """
You a software architecture expert.

Here is a summary of the most important modules, components, and abstract classes suggested so far from doing steps 1,2 in your tasks:
{insight_so_far}

You have access to the source code of the project via the provided `read_source_code` tool.

**Your Tasks:**
1. Use the read_source_code tool to read the source code of the modules and components you need further details about.
2. Refine or expand the earlier high-level classes/components, in the end you have to have NO MORE than 10 components (best if they are 5), based on new details from the source code.
3. Define each component by its name, relevant documents and most importantly **what roles does it have with-in the project**, how it relates to its neighbouring components. For that again you can consult with the CFG.

**Instructions:**
{format_instructions}
"""

MARKDOWN_MESSAGE = """
You are the software architecture expert for the project `{project_name}`.

You have run CFG analysis and came to the following conclusions:
{cfg_insight}

Finally looking through the codebase you identified the **final components**:
{source_insight}

**Your Tasks:**
Generate an onboarding document that describes the project and its components.
1. Generate a short description of the project, what is its purpose and functionality.
2. Generate a **data flow diagram** (in Mermaid format) that describes the main flow of the project, it has to be a high-level overview of the project. The connections between the components should be clear and have to be described with one word like ("uses", "calls", "sends document"), in mermaid always use ComponentA--ConnectionDescription-->ComponentB..
3. Do a short one paragraph description of each component from the Mermaid diagram, what is its purpose and functionality is, **how it relates to the its neighbouring components**.
"""

SYSTEM_MESSAGE_DETAILS = """
You are a software architecture expert.
We are exploring one of the components of a big project `{project_name}`.
Your task now is to generate a general overview of the component, its structure, flow, and its purpose.

**Your tasks:**
1. Examine the full project Control Flow Graph and identify the relevant part.
2. Examine the structure diagram of the project and identify the relevant part.
3. Identify the most important and central modules or functions (HAVE TO BE LESS THAN 10).
4. Investigate the code of these modules to understand their purpose and functionality.
5. Identify the main flow within the component, abstract away the details. Define a small sequence diagram.
6. Identify the main structure of the component, what are the main classes and methods. Create a class diagram.
"""


CFG_DETAILS_MESSAGE = """
You are an expert in software system architecture. Working on step 1 (think about step 3) of the analysis tasks.
At this moment we are analyzing the Control Flow Graph (CFG) for the project `{project_name}`.
Identify only the relevant components in the CFG for {component}.

Here is the CFG:
{cfg_str}

Please identify important modules and functions from the CFG. You can use the following format:
{format_instructions}
"""

STRUCTURE_DETAILS_MESSAGE = """
You are an expert in software system architecture.
Currently at step 2 (think about step 3) of the analysis tasks.
At this moment we are analyzing the structure diagram for the project `{project_name}`.
Identify only the relevant components in the structure for {component}

From the CFG analysis, we have identified the following important modules and abstractions:
{cfg_insight}

Here is the structure diagram:
{structure_graph}

Please identify important modules and functions from the structure. You can use the following format:
{format_instructions}
"""


DETAILS_MESSAGE = """
You are a software architecture expert.
Here is a summary of the most important modules, components, and abstract classes suggested so far from doing steps 1-3 in your tasks:
{insight_so_far}

You have access to the source code of the component via the provided `read_source_code` tool.

**Your Tasks:**
1. Use the read_source_code tool to read the source code of the modules and components you need further details about.
2. Refine or expand the earlier high-level classes/components, we need to understand the structure of the component and its purpose.
3. Generate a document:
    - Generate a brief description of the component, what are the main classes and what is their purpose.
    - From the insights so far for the component, decide on a visualization technique to represent the component **USE JUST ONE**. It can be a flow diagram, class diagram, or any other visualization technique that best represents the component. For the visualization use Mermaid format.
"""