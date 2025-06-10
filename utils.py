import re
import os
from pathlib import Path
import shutil
import uuid
from langchain_google_genai import ChatGoogleGenerativeAI
from git import Git, GitCommandError

from agents.agent_responses import AnalysisInsights


class NoGithubTokenFoundError(Exception):
    pass


class CFGGenerationError(Exception):
    pass


class RepoDontExistError(Exception):
    pass


class RepoIsNone(Exception):
    pass


def create_temp_repo_folder():
    unique_id = uuid.uuid4().hex
    temp_dir = os.path.join('temp', unique_id)
    os.makedirs(temp_dir, exist_ok=False)
    return Path(temp_dir)


def remove_temp_repo_folder(temp_path: str):
    p = Path(temp_path)
    if not p.parts or p.parts[0] != "temp":
        raise ValueError(f"Refusing to delete outside of 'temp/': {temp_path!r}")
    shutil.rmtree(temp_path)


def init_llm():
    api_key = os.getenv("API_KEY")

    return ChatGoogleGenerativeAI(
        model="gemini-2.0-flash-001",
        temperature=0,
        max_tokens=None,
        timeout=None,
        max_retries=2,
        google_api_key=api_key,
    )


def caching_enabled():
    return os.getenv('CACHING_DOCUMENTATION', 'false').lower() in ('1', 'true', 'yes')


def remote_repo_exists(repo_url: str) -> bool:
    if repo_url is None:
        return False
    try:
        Git().ls_remote(repo_url)
        return True
    except GitCommandError as e:
        stderr = (e.stderr or "").lower()
        if "not found" in stderr or "repository not found" in stderr:
            return False
        # something else went wrong (auth, network); re-raise so caller can decide
        raise


def sanitize_repo_url(repo_url: str) -> str:
    if not repo_url or not isinstance(repo_url, str):
        raise RepoIsNone("No repo URL provided")
    clean = repo_url.strip().strip('"').strip("'")
    if not clean:
        raise ValueError("Repo URL is empty after sanitization")
    return clean


def generate_markdown(insights: AnalysisInsights, project: str = "", link_files=True, repo_url="") -> str:
    """
    Generate a Mermaid 'graph TD' diagram from an AnalysisInsights object.
    """

    def sanitize(name: str) -> str:
        # Replace non-alphanumerics with underscores so IDs are valid Mermaid identifiers
        return re.sub(r'\W+', '_', name)

    lines = ["```mermaid", "graph LR"]

    # 1. Define each component as a node, including its description
    for comp in insights.components:
        node_id = sanitize(comp.name)
        # Show name and short description in the node label
        label = f"{comp.name}"
        lines.append(f'    {node_id}["{label}"]')

    # 2. Add relations as labeled edges
    for rel in insights.components_relations:
        src_id = sanitize(rel.src_name)
        dst_id = sanitize(rel.dst_name)
        # Use the relation phrase as the edge label
        lines.append(f'    {src_id} -- "{rel.relation}" --> {dst_id}')

    # 3. Add clickable links to the components
    if link_files:
        for comp in insights.components:
            node_id = sanitize(comp.name)
            # Use the component name as the link text
            lines.append(
                f'    click {node_id} href "https://github.com/CodeBoarding/GeneratedOnBoardings/blob/main/{project}/{comp.name}.md" "Details"')

    lines.append("```")

    lines.append(
        "[![CodeBoarding](https://img.shields.io/badge/Generated%20by-CodeBoarding-9cf?style=flat-square)](https://github.com/CodeBoarding/GeneratedOnBoardings)[![Demo](https://img.shields.io/badge/Try%20our-Demo-blue?style=flat-square)](https://www.codeboarding.org/demo)[![Contact](https://img.shields.io/badge/Contact%20us%20-%20contact@codeboarding.org-lightgrey?style=flat-square)](mailto:contact@codeboarding.org)")

    detail_lines = ["\n## Component Details\n", f"{insights.description}\n"]

    root_dir = os.getenv('REPO_ROOT') + "/" + project

    for comp in insights.components:
        detail_lines.append(f"### {comp.name}")
        detail_lines.append(f"{comp.description}")
        if comp.referenced_source_code:
            qn_list = []
            for reference in comp.referenced_source_code:
                print(reference.reference_file, root_dir)
                if reference.reference_start_line is None or reference.reference_end_line is None:
                    qn_list.append(f"{reference.llm_str()}")
                    continue
                if not reference.reference_file.startswith(root_dir):
                    qn_list.append(f"{reference.llm_str()}")
                    continue
                ref_url = repo_url + "/blob/master" + reference.reference_file.split(root_dir)[1] \
                          + f"#L{reference.reference_start_line}-L{reference.reference_end_line}"
                qn_list.append(
                    f'<a href="{ref_url}" target="_blank" rel="noopener noreferrer">{reference.llm_str()}</a>')
            # Join the list into an unordered markdown list, without the leading dash
            references = ""
            for item in qn_list:
                references += f"- {item}\n"

            detail_lines.append(f"\n\n**Related Classes/Methods**:\n\n{references}")
        else:
            detail_lines.append(f"\n\n**Related Classes/Methods**: _None_")
        detail_lines.append("")  # blank line between components

    detail_lines.append(
        "\n\n### [FAQ](https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main?tab=readme-ov-file#faq)")
    return "\n".join(lines + detail_lines)


if __name__ == "__main__":
    # Example
    from dotenv import load_dotenv

    load_dotenv()
    dirs = os.listdir('./temp')
    for subd in dirs:
        # Read all json files in subd and load them in AnalysisInsights
        subd_path = Path('./temp') / subd
        if subd_path.is_dir():
            json_files = list(subd_path.glob('*.json'))
            project_name = None
            repo_url = None
            for json_file in json_files:
                with open(json_file, 'r') as f:
                    analysis = AnalysisInsights.model_validate_json(f.read())
                    print(analysis.llm_str())
                # Now create the markdown file
                if project_name is None:
                    project_name = input("Enter the project name: ")
                    repo_url = input("Enter the repository URL (or leave empty for no links): ").strip()
                if not repo_url:
                    continue
                markdown_response = generate_markdown(analysis, project_name,
                                                      link_files=("analysis.json" in json_file.name), repo_url=repo_url)
                fname = json_file.name.split(".json")[0]
                fname = "on_boarding" if fname.endswith("analysis") else fname
                with open(f"{subd_path}/{fname}.md", "w") as f:
                    f.write(markdown_response.strip())
                    print(f"Generated markdown file: {fname}.md in {subd_path}")
        else:
            print(f"{subd} is not a directory, skipping.")
