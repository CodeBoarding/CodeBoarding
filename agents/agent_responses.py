import abc
import json
from abc import abstractmethod
from typing import get_origin, get_args, Any

from pydantic import BaseModel, Field, field_validator


def _parse_stringified_json(value: Any) -> Any:
    """Parse stringified JSON objects that Vercel AI Gateway sometimes returns.

    When using Gemini via Vercel's AI gateway with tool calling, nested objects
    in arrays are sometimes returned as JSON strings instead of actual objects.
    This helper parses them back into dicts.
    """
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


class LLMBaseModel(BaseModel, abc.ABC):
    @abstractmethod
    def llm_str(self):
        raise NotImplementedError("LLM String has to be implemented.")

    @classmethod
    def extractor_str(cls) -> str:
        """Generate an extraction prompt for instructor.

        Override this in subclasses to provide model-specific extraction prompts
        that are more effective for structured output extraction.
        """
        return cls._default_extractor_str()

    @classmethod
    def _default_extractor_str(cls) -> str:
        lines = ["Extract the following fields as structured JSON:\n"]
        for fname, fvalue in cls.model_fields.items():
            ftype = fvalue.annotation
            if get_origin(ftype) is list:
                args = get_args(ftype)
                inner_type = args[0] if args else "item"
                if isinstance(inner_type, type) and issubclass(inner_type, LLMBaseModel):
                    nested_fields = ", ".join(f"{f}: {v.description}" for f, v in inner_type.model_fields.items())
                    lines.append(f"- {fname}: List of objects with ({nested_fields})")
                else:
                    lines.append(f"- {fname}: List of {fvalue.description}")
            else:
                lines.append(f"- {fname}: {fvalue.description}")
        lines.append("\nContent to extract from:\n")
        return "\n".join(lines)


class SourceCodeReference(LLMBaseModel):
    qualified_name: str = Field(
        description="Qualified name of the source code, e.g., `langchain.tools.tool` or `langchain_core.output_parsers.JsonOutputParser` or `langchain_core.output_parsers.JsonOutputParser:parse`."
    )

    reference_file: str | None = Field(
        description="File path where the source code is located, e.g., `langchain/tools/tool.py` or `langchain_core/output_parsers/json_output_parser.py`."
    )

    reference_start_line: int | None = Field(
        description="The line number in the source code where the reference starts. Only if you are absolutely sure add this, otherwise None."
    )
    reference_end_line: int | None = Field(
        description="The line number in the source code where the reference ends. Only if you are absolutely sure add this, otherwise None."
    )

    def llm_str(self):
        if self.reference_start_line is None or self.reference_end_line is None:
            return f"QName:`{self.qualified_name}` FileRef: `{self.reference_file}`"
        if (
            self.reference_start_line <= self.reference_end_line <= 0
            or self.reference_start_line == self.reference_end_line
        ):
            return f"QName:`{self.qualified_name}` FileRef: `{self.reference_file}`"
        return f"QName:`{self.qualified_name}` FileRef: `{self.reference_file}`, Lines:({self.reference_start_line}:{self.reference_end_line})"

    def __str__(self):
        if self.reference_start_line is None or self.reference_end_line is None:
            return f"`{self.qualified_name}`"
        if (
            self.reference_start_line <= self.reference_end_line <= 0
            or self.reference_start_line == self.reference_end_line
        ):
            return f"`{self.qualified_name}`"
        return f"`{self.qualified_name}`:{self.reference_start_line}-{self.reference_end_line}"


class Relation(LLMBaseModel):
    relation: str = Field(description="Single phrase used for the relationship of two components.")
    src_name: str = Field(description="Source component name")
    dst_name: str = Field(description="Target component name")

    def llm_str(self):
        return f"({self.src_name}, {self.relation}, {self.dst_name})"


class ClusterComponent(LLMBaseModel):
    cluster_id: int = Field(description="The cluster ID from the CFG analysis (e.g., 1, 2, 3)")
    name: str = Field(description="Descriptive name for this cluster/component (2-4 words)")
    description: str = Field(description="One sentence explaining what this cluster does")

    def llm_str(self):
        return f"**Cluster {self.cluster_id}: {self.name}**\n   {self.description}"


class ClusterAnalysis(LLMBaseModel):
    cluster_components: list[ClusterComponent] = Field(
        description="Interpretation of each cluster. Use cluster_id to reference clusters from the CFG."
    )

    def llm_str(self):
        if not self.cluster_components:
            return "No clusters analyzed."
        title = "# Cluster-Based Components\n"
        body = "\n".join(cc.llm_str() for cc in self.cluster_components)
        return title + body


class Component(LLMBaseModel):
    name: str = Field(description="Name of the component")
    description: str = Field(description="A short description of the component.")

    # LLM picks these: The MOST IMPORTANT/critical methods and classes
    key_entities: list[SourceCodeReference] = Field(
        description="The most important/critical classes and methods that represent this component's core functionality. Pick 2-5 key entities."
    )

    # Deterministic from static analysis: ALL files belonging to this component
    assigned_files: list[str] = Field(
        description="All source files assigned to this component (populated deterministically).",
        default_factory=list,
        exclude=True,
    )

    source_cluster_ids: list[int] = Field(
        description="List of cluster IDs from the CFG analysis that this component encompasses.",
        default_factory=list,
    )

    def llm_str(self):
        n = f"**Component:** `{self.name}`"
        d = f"   - *Description*: {self.description}"
        qn = ""
        if self.key_entities:
            qn += "   - *Key Entities*: "
            qn += ", ".join(f"`{q.llm_str()}`" for q in self.key_entities)
        return "\n".join([n, d, qn]).strip()


class AnalysisInsights(LLMBaseModel):
    description: str = Field(
        description="One paragraph explaining the functionality which is represented by this graph. What the main flow is and what is its purpose."
    )
    components: list[Component] = Field(description="List of the components identified in the project.")
    components_relations: list[Relation] = Field(description="List of relations among the components.")

    @classmethod
    def extractor_str(cls) -> str:
        return """Extract the analysis insights from the following content as structured JSON.

Provide:
- description: One paragraph explaining the main functionality and purpose

For each component provide:
- name: The component name
- description: A short description of the component
- referenced_source_code: List of source code references, each with:
  - qualified_name: Qualified name (e.g., "langchain.tools.tool")
  - reference_file: File path (e.g., "langchain/tools/tool.py") or null
  - reference_start_line: Starting line number or null
  - reference_end_line: Ending line number or null

For each relation provide:
- relation: The relationship phrase
- src_name: Source component name
- dst_name: Target component name

Content to extract from:
"""

    def llm_str(self):
        if not self.components:
            return "No abstract components found."
        title = "# 📦 Abstract Components Overview\n"
        body = "\n".join(ac.llm_str() for ac in self.components)
        relations = "\n".join(cr.llm_str() for cr in self.components_relations)
        return title + body + relations


class CFGComponent(LLMBaseModel):
    name: str = Field(description="Name of the abstract component")
    description: str = Field(description="One paragraph explaining the component.")
    referenced_source: list[str] = Field(
        description="List of the qualified names of the methods and classes that are within this component."
    )

    def llm_str(self):
        n = f"**Component:** `{self.name}`"
        d = f"   - *Description*: {self.description}"
        qn = ""
        if self.referenced_source:
            qn += "   - *Related Classes/Methods*: "
            qn += ", ".join(f"`{q}`" for q in self.referenced_source)
        return "\n".join([n, d, qn]).strip()


class CFGAnalysisInsights(LLMBaseModel):
    components: list[CFGComponent] = Field(description="List of components identified in the CFG.")
    components_relations: list[Relation] = Field(description="List of relations among the components in the CFG.")

    # Pydantic field validators - automatically called during model instantiation
    @field_validator("components", mode="before")
    @classmethod
    def parse_stringified_components(cls, value: Any) -> Any:
        """Handle stringified JSON from Vercel AI Gateway."""
        if isinstance(value, list):
            return [_parse_stringified_json(item) for item in value]
        return value

    @field_validator("components_relations", mode="before")
    @classmethod
    def parse_stringified_relations(cls, value: Any) -> Any:
        """Handle stringified JSON from Vercel AI Gateway."""
        if isinstance(value, list):
            return [_parse_stringified_json(item) for item in value]
        return value

    @classmethod
    def extractor_str(cls) -> str:
        return """Extract all components and their relationships from the following CFG analysis as structured JSON.

For each component provide:
- name: The component name (e.g., "CLI Service", "Analysis Engine")
- description: A brief description of what the component does
- referenced_source: List of qualified method/class names (e.g., ["main.main", "analyzer.analyze"])

For each relation provide:
- relation: The relationship phrase (e.g., "Triggers Analysis", "Returns Results")
- src_name: Source component name (must match a component name exactly)
- dst_name: Target component name (must match a component name exactly)

Content to extract from:
"""

    def llm_str(self):
        if not self.components:
            return "No abstract components found in the CFG."
        title = "# 📦 Abstract Components Overview from CFG\n"
        body = "\n".join(ac.llm_str() for ac in self.components)
        relations = "\n".join(cr.llm_str() for cr in self.components_relations)
        return title + body + relations


class ExpandComponent(LLMBaseModel):
    should_expand: bool = Field(description="Whether the component should be expanded in detail or not.")
    reason: str = Field(description="Reasoning behind the decision to expand or not.")

    def llm_str(self):
        return f"- *Should Expand:* {self.should_expand}\n- *Reason:* {self.reason}"


class ValidationInsights(LLMBaseModel):
    is_valid: bool = Field(description="Indicates whether the validation results in valid or not.")
    additional_info: str | None = Field(
        default=None, description="Any additional information or context related to the validation."
    )

    def llm_str(self):
        return f"**Feedback Information:**\n{self.additional_info}"


class UpdateAnalysis(LLMBaseModel):
    update_degree: int = Field(
        description="Degree to which the diagram needs update. 0 means no update, 10 means complete update."
    )
    feedback: str = Field(description="Feedback provided on the analysis.")

    def llm_str(self):
        return f"**Feedback:**\n{self.feedback}"


class MetaAnalysisInsights(LLMBaseModel):
    project_type: str = Field(
        description="Type/category of the project (e.g., web framework, data processing, ML library, etc.)"
    )
    domain: str = Field(
        description="Domain or field the project belongs to (e.g., web development, data science, DevOps, etc.)"
    )
    architectural_patterns: list[str] = Field(description="Main architectural patterns typically used in such projects")
    expected_components: list[str] = Field(description="Expected high-level components/modules based on project type")
    technology_stack: list[str] = Field(description="Main technologies, frameworks, and libraries used")
    architectural_bias: str = Field(
        description="Guidance on how to interpret and organize components for this project type"
    )

    def llm_str(self):
        title = "# 🎯 Project Metadata Analysis\n"
        content = f"""
**Project Type:** {self.project_type}
**Domain:** {self.domain}
**Technology Stack:** {', '.join(self.technology_stack)}
**Architectural Patterns:** {', '.join(self.architectural_patterns)}
**Expected Components:** {', '.join(self.expected_components)}
**Architectural Bias:** {self.architectural_bias}
"""
        return title + content


class FileClassification(LLMBaseModel):
    component_name: str = Field(description="Name of the component or module")
    file_path: str = Field(description="Path to the file")

    def llm_str(self):
        return f"`{self.file_path}` -> Component: `{self.component_name}`"


class ComponentFiles(LLMBaseModel):
    file_paths: list[FileClassification] = Field(
        description="All files with their classifications for each of the files assigned to a component."
    )

    def llm_str(self):
        if not self.file_paths:
            return "No files classified."
        title = "# 📄 Component File Classifications\n"
        body = "\n".join(f"- `{fc.file_path}` -> Component: `{fc.component_name}`" for fc in self.file_paths)
        return title + body


class FilePath(LLMBaseModel):
    file_path: str = Field(description="Full file path for the reference")
    start_line: int | None = Field(
        default=None, description="Starting line number in the file for the reference (if applicable)."
    )
    end_line: int | None = Field(
        default=None, description="Ending line number in the file for the reference (if applicable)."
    )

    def llm_str(self):
        return f"`{self.file_path}`: ({self.start_line}:{self.end_line})"
