import os
from typing import List

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI


class MarkdownEnhancer:
    def __init__(self):
        load_dotenv()
        self.api_key = os.getenv("GOOGLE_API_KEY")

        self.llm = ChatGoogleGenerativeAI(
            model="gemini-2.0-flash",
            temperature=0,
            max_retries=2,
            google_api_key=self.api_key
        )

    def fix_diagram(self, markdown_str: str) -> str:
        """
        Fix the diagram in the markdown string.
        """
        print(f"[Markdown Enhancer] Fixing diagram in markdown.")
        prompt = f'Check the mermaid diagram in the markdown string.\n{markdown_str}\n\nIf there are labels such as A[Component name (extra information about it)] change to A["Component name (extra information about it)"], same if you have A(B(D)) to A("B(D)"). DON\'T change anything else, just change the labels. Return the full markdown string.\nReturn the full markdown string. It should not start and end with ```markdown```. Just give the content as if you are writing a markdown file.'
        response = self.llm.invoke(prompt)
        return response.content

    def link_components(self, markdown_str: str, project_name:str, files: List[str], base_url="https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/") -> str:
        """
        Link the components in the markdown string.
        """
        base_url = base_url + project_name + "/"
        print(f"[Markdown Enhancer] Linking components in markdown.")
        prompt = f'''Check the markdown string and link each component to its details file.
{markdown_str}

For each component in the **mermaid diagram**, make it a clickable link like: **click A href "{base_url}/RelevantFile.md"**.

Example of linked valid mermaid:
```mermaid
graph LR
    A([Request Handling]) -- Receives --> B([URL Routing])
    B -- Determines --> C(View Processing)
    C -- Interacts with --> D["Data Models (ORM)"]

click A href "{base_url}/Request%20Handling.md"
click B href "{base_url}/URL%20%Routing.md"
click C href "{base_url}/View%20%Processing.md"
click D href "{base_url}/Data%20Models%20(ORM).md"
```

The available files are: {files}. DON\'T change anything else, just add the links. Return the full markdown string.
For the links use the full links, not relative links.

Return the full markdown string with clickable linked mermaid components. It should not start with ```markdown and end with ```. Just give the content as if you are writing a markdown file.
'''
        response = self.llm.invoke(prompt)
        return response.content
