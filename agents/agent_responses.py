import abc
from abc import abstractmethod
from typing import get_origin, Optional

from pydantic import BaseModel, Field


class LLMBaseModel(BaseModel, abc.ABC):
    """Base model for LLM-parseable response types."""

    @abstractmethod
    def llm_str(self):
        raise NotImplementedError("LLM String has to be implemented.")

    @classmethod
    def extractor_str(cls):
        # Here iterate over the fields that we have and use their description like:
        result_str = "please extract the following: "
        for fname, fvalue in cls.model_fields.items():
            # check if the field type is Optional
            ftype = fvalue.annotation
            # Check if the type is a typing.List (e.g., typing.List[SomeType])
            if get_origin(ftype) is list:
                # get the type of the list:
                if ftype is not None and hasattr(ftype, "__args__"):
                    ftype = ftype.__args__[0]
                result_str += f"{fname} which is a list ("
            if ftype is Optional:
                result_str += f"{fname} ({fvalue.description}), "
            elif ftype is not None and isinstance(ftype, type) and issubclass(ftype, LLMBaseModel):
                # Now I need to call the extractor_str method of the field
                result_str += ftype.extractor_str()
            else:
                result_str += f"{fname} ({fvalue.description}), "
            if get_origin(ftype) is list:
                result_str += "), "
        return result_str


class SourceCodeReference(LLMBaseModel):
    """Reference to source code including qualified name and file location."""

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
    """A relationship between two components."""

    relation: str = Field(description="Single phrase used for the relationship of two components.")
    src_name: str = Field(description="Source component name")
    dst_name: str = Field(description="Target component name")

    def llm_str(self):
        return f"({self.src_name}, {self.relation}, {self.dst_name})"


class ClusterComponent(LLMBaseModel):
    """A component derived from cluster analysis."""

    cluster_id: int = Field(description="The cluster ID from the CFG analysis (e.g., 1, 2, 3)")
    name: str = Field(description="Descriptive name for this cluster/component (2-4 words)")
    description: str = Field(description="One sentence explaining what this cluster does")

    def llm_str(self):
        return f"**Cluster {self.cluster_id}: {self.name}**\n   {self.description}"


class ClusterAnalysis(LLMBaseModel):
    """Analysis results containing interpreted cluster components."""

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
    """A software component with name, description, and key entities."""

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
    """Project analysis insights including components and their relations."""

    description: str = Field(
        description="One paragraph explaining the functionality which is represented by this graph. What the main flow is and what is its purpose."
    )
    components: list[Component] = Field(description="List of the components identified in the project.")
    components_relations: list[Relation] = Field(description="List of relations among the components.")

    def llm_str(self):
        if not self.components:
            return "No abstract components found."
        title = "# 📦 Abstract Components Overview\n"
        body = "\n".join(ac.llm_str() for ac in self.components)
        relations = "\n".join(cr.llm_str() for cr in self.components_relations)
        return title + body + relations


class CFGComponent(LLMBaseModel):
    """A component derived from control flow graph analysis."""

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
    """Insights from control flow graph analysis including components and relations."""

    components: list[CFGComponent] = Field(description="List of components identified in the CFG.")
    components_relations: list[Relation] = Field(description="List of relations among the components in the CFG.")

    def llm_str(self):
        if not self.components:
            return "No abstract components found in the CFG."
        title = "# 📦 Abstract Components Overview from CFG\n"
        body = "\n".join(ac.llm_str() for ac in self.components)
        relations = "\n".join(cr.llm_str() for cr in self.components_relations)
        return title + body + relations


class ExpandComponent(LLMBaseModel):
    """Decision on whether to expand a component with reasoning."""

    should_expand: bool = Field(description="Whether the component should be expanded in detail or not.")
    reason: str = Field(description="Reasoning behind the decision to expand or not.")

    def llm_str(self):
        return f"- *Should Expand:* {self.should_expand}\n- *Reason:* {self.reason}"


class ValidationInsights(LLMBaseModel):
    """Validation results with status and additional information."""

    is_valid: bool = Field(description="Indicates whether the validation results in valid or not.")
    additional_info: str | None = Field(
        default=None, description="Any additional information or context related to the validation."
    )

    def llm_str(self):
        return f"**Feedback Information:**\n{self.additional_info}"


class UpdateAnalysis(LLMBaseModel):
    """Feedback on how much a diagram needs updating."""

    update_degree: int = Field(
        description="Degree to which the diagram needs update. 0 means no update, 10 means complete update."
    )
    feedback: str = Field(description="Feedback provided on the analysis.")

    def llm_str(self):
        return f"**Feedback:**\n{self.feedback}"


class MetaAnalysisInsights(LLMBaseModel):
    """Insights from analyzing project metadata including type, domain, and architecture."""

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
    """Classification of a file to a component."""

    component_name: str = Field(description="Name of the component or module")
    file_path: str = Field(description="Path to the file")

    def llm_str(self):
        return f"`{self.file_path}` -> Component: `{self.component_name}`"


class ComponentFiles(LLMBaseModel):
    """Collection of file classifications for components."""

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
    """File path with optional line range reference."""

    file_path: str = Field(description="Full file path for the reference")
    start_line: int | None = Field(
        default=None, description="Starting line number in the file for the reference (if applicable)."
    )
    end_line: int | None = Field(
        default=None, description="Ending line number in the file for the reference (if applicable)."
    )

    def llm_str(self):
        return f"`{self.file_path}`: ({self.start_line}:{self.end_line})"
