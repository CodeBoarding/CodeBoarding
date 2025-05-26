import re
import os
from agents.agent import AnalysisInsights
from pathlib import Path
import shutil
import uuid
from langchain_google_genai import ChatGoogleGenerativeAI
from git import Git, GitCommandError


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


def generate_mermaid(insights: AnalysisInsights, project: str = "", link_files=True) -> str:
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

    detail_lines = ["\n## Component Details\n", f"{insights.description}\n"]

    for comp in insights.components:
        detail_lines.append(f"### {comp.name}")
        detail_lines.append(f"{comp.description}")
        if comp.source_code_files:
            qn_list = ", ".join(f"`{qn}`" for qn in comp.source_code_files)
            detail_lines.append(f"- **Related Classes/Methods**: {qn_list}")
        else:
            detail_lines.append(f"- **Related Classes/Methods**: _None_")
        detail_lines.append("")  # blank line between components

    return "\n".join(lines + detail_lines)
