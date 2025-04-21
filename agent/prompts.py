SYSTEM_MESSAGE = """
You are a software architecture expert. You will be given Control Flow Graph (CFG), followed by structure diagram for the project `{project_name}` as a strings.

**Your tasks:**
1. Examine the flow in the CFG.
2. Examine the structure diagram of the program.
3. Identify the most important and central modules or functions (HAVE TO BE LESS THAN 10).
4. Investigate the code of these modules to understand their purpose and functionality.
5. For each important module, state its main responsibility in 1-2 sentences.
6. Summarize the core abstractions the project seems to implement.
"""

CFG_MESSAGE = """
You are an expert in software system architecture. Currently at step 1 (think about step 3) of the analysis tasks.
Here is the Control Flow Graph (CFG) for the project `{project_name}`.
{cfg_str}

Please identify important modules and functions from the CFG. You can use the following format:
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
2. Identify any additional important components that are evident from the structure (e.g., large packages, central directories, or clearly separated components).
3. Suggest a set of high-level abstract classes or components that best represent the project as a whole, considering both the raw structure and your earlier insight.

**Instructions:**
{format_instructions}
"""

SOURCE_MESSAGE = """
You a software architecture expert.

Here is a summary of the most important modules, components, and abstract classes suggested so far from doing steps 1-3 in your tasks:
{insight_so_far}

You have access to the source code of the project via the provided `read_source_code` tool.

**Your Tasks:**
1. Use the read_source_code tool to read the source code of the modules and components you need further details about.
2. Refine or expand the earlier high-level classes/components, in the end you have to have NO MORE than 10 components (best if they are 5), based on new details from the source code.
3. Generate an on-boarding document:
    - Generate a high-level functional description of the project, not more than 2 sentences.
    - Create an abstract diagram with the refined components and how they communicate with each other (one-two words with verb). Create the diagram in Mermaid format. This component diagram MUST NOT have more than 10 components.
    - Generate a brief description of each component, as a header 2 in the document.
"""