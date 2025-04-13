import os
from typing import List, Optional, Union, Literal
from dotenv import load_dotenv
from pydantic import BaseModel, RootModel, Field
from langchain.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI


def init_llm():
    load_dotenv()
    api_key = os.getenv("API_KEY")
    return ChatGoogleGenerativeAI(
        model="gemini-2.0-flash-001",
        temperature=0,
        max_tokens=None,
        timeout=None,
        max_retries=2,
        google_api_key=api_key,
    )


class CodeEntrypoint(BaseModel):
    type: Literal["code"] = "code"
    python_code: str = Field(description="The full Python code block needed to programmatically run the tool or library.")
    description: Optional[str] = Field(default=None, description="A short description of what the code does.")


class CLIEntrypoint(BaseModel):
    type: Literal["cli"] = "cli"
    command: str = Field(description="The full command-line string used to run the tool.")
    description: Optional[str] = Field(default=None, description="A short description of what the CLI command does.")

    @property
    def python_code(self) -> str:
        return (
            "import subprocess\n"
            f"subprocess.run({self.command.split()}, check=True)\n"
        )


class Entrypoints(RootModel[List[Union[CodeEntrypoint, CLIEntrypoint]]]):
    pass


def scan_for_readme(root: str = "markitdown") -> Optional[str]:
    for dirpath, _, filenames in os.walk(root):
        for filename in filenames:
            if filename.lower() in {"readme.md", "readme.txt", "readme"}:
                return os.path.join(dirpath, filename)
    return None


code_prompt_template = ChatPromptTemplate.from_messages([
    ("system", (
        "You are a code extraction assistant. Given a README, extract only the **Python code blocks** that demonstrate how to "
        "programmatically use the library or tool (i.e., no CLI examples). Each code block should be minimal and complete, like "
        "importing the module, initializing the object, and invoking a method with sample data."
    )),
    ("user", "{readme}\n\n{format_instructions}")
])

cli_prompt_template = ChatPromptTemplate.from_messages([
    ("system", (
        "You are a CLI extraction assistant. Given a README, extract only the **command-line examples** used to run the tool. "
        "Each CLI entrypoint should include the full command string (e.g., `toolname --flag value`) and a short description."
    )),
    ("user", "{readme}\n\n{format_instructions}")
])


def extract_entrypoints(readme_content: str, prompt_template: ChatPromptTemplate, parser: PydanticOutputParser) -> Entrypoints:
    prompt = prompt_template.format_messages(
        readme=readme_content,
        format_instructions=parser.get_format_instructions()
    )
    llm = init_llm()
    response = llm.invoke(prompt)
    raw_output = response.content.strip()
    if raw_output.startswith("```json"):
        raw_output = raw_output.strip("```json").strip("```").strip()
    return parser.parse(raw_output)


if __name__ == "__main__":
    readme_path = scan_for_readme()
    if readme_path is None:
        print("No README file found.")
        exit()

    with open(readme_path, 'r', encoding='utf-8') as f:
        readme_content = f.read()

    parser = PydanticOutputParser(pydantic_object=Entrypoints)

    code_entrypoints = extract_entrypoints(readme_content, code_prompt_template, parser)
    cli_entrypoints = extract_entrypoints(readme_content, cli_prompt_template, parser)

    all_entrypoints = Entrypoints(root=code_entrypoints.root + cli_entrypoints.root)

    for ep in all_entrypoints.root:
        print(f"Entrypoint Type: {ep.type}")
        print(ep.python_code)
        if ep.description:
            print("Description:", ep.description)
        print("-" * 40)
