from typing import List, Optional

from pydantic import BaseModel, Field


class SourceCodeReference(BaseModel):
    qualified_name: str = Field(
        description="Qualified name of the source code, e.g., `langchain.tools.tool` or `langchain_core.output_parsers.JsonOutputParser` or `langchain_core.output_parsers.JsonOutputParser:parse`."
    )

    reference_file: Optional[str] = Field(
        description="File path where the source code is located, e.g., `langchain/tools/tool.py` or `langchain_core/output_parsers/json_output_parser.py`."
    )

    reference_start_line: Optional[int] = Field(
        description="The line number in the source code where the reference starts."
    )
    reference_end_line: Optional[int] = Field(
        description="The line number in the source code where the reference ends."
    )

    def llm_str(self):
        if self.reference_start_line is None or self.reference_end_line is None:
            return f"QName:`{self.qualified_name}` FileRef: `{self.reference_file}`"
        if (self.reference_start_line <= self.reference_end_line <= 0 or
                self.reference_start_line == self.reference_end_line):
            return f"QName:`{self.qualified_name}` FileRef: `{self.reference_file}`"
        return f"QName:`{self.qualified_name}` FileRef: `{self.reference_file}`, Lines:({self.reference_start_line}:{self.reference_end_line})"

    def __str__(self):
        if self.reference_start_line is None or self.reference_end_line is None:
            return f"`{self.qualified_name}`"
        if (self.reference_start_line <= self.reference_end_line <= 0 or
                self.reference_start_line == self.reference_end_line):
            return f"`{self.qualified_name}`"
        return f"`{self.qualified_name}`:{self.reference_start_line}-{self.reference_end_line}"


class Relation(BaseModel):
    relation: str = Field(description="Single phrase used for the relationship of two components.")
    src_name: str = Field(description="Source component name")
    dst_name: str = Field(description="Target component name")

    def llm_str(self):
        return f"({self.src_name}, {self.relation}, {self.dst_name})"


class Component(BaseModel):
    name: str = Field(description="Name of the component")
    description: str = Field(description="A short description of the component.")
    referenced_source_code: List[SourceCodeReference] = Field(
        description="A list of source code names of referenced methods and classes to the component."
    )

    def llm_str(self):
        n = f"**Component:** `{self.name}`"
        d = f"   - *Description*: {self.description}"
        qn = ""
        if self.referenced_source_code:
            qn += "   - *Related Classes/Methods*: "
            qn += ", ".join(f"`{q.llm_str()}`" for q in self.referenced_source_code)
        return "\n".join([n, d, qn]).strip()


class AnalysisInsights(BaseModel):
    description: str = Field(
        "One paragraph explaining the functionality which is represented by this graph. What the main flow is and what is its purpose.")
    components: List[Component] = Field(
        description="List of the components identified in the project.")
    components_relations: List[Relation] = Field(
        description="List of relations among the components."
    )

    def llm_str(self):
        if not self.components:
            return "No abstract components found."
        title = "# 📦 Abstract Components Overview\n"
        body = "\n".join(ac.llm_str() for ac in self.components)
        relations = "\n".join(cr.llm_str() for cr in self.components_relations)
        return title + body + relations


class CFGComponent(BaseModel):
    name: str = Field(description="Name of the abstract component")
    description: str = Field(description="One paragraph explaining the component.")
    referenced_source: List[str] = Field(
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


class CFGAnalysisInsights(BaseModel):
    components: List[CFGComponent] = Field(description="List of components identified in the CFG.")
    components_relations: List[Relation] = Field(
        description="List of relations among the components in the CFG."
    )

    def llm_str(self):
        if not self.components:
            return "No abstract components found in the CFG."
        title = "# 📦 Abstract Components Overview from CFG\n"
        body = "\n".join(ac.llm_str() for ac in self.components)
        relations = "\n".join(cr.llm_str() for cr in self.components_relations)
        return title + body + relations


class ExpandComponent(BaseModel):
    should_expand: bool = Field(description="Whether the component should be expanded in detail or not.")
    reason: str = Field(description="Reasoning behind the decision to expand or not.")

    def llm_str(self):
        return f"- *Should Expand:* {self.should_expand}\n- *Reason:* {self.reason}"


class ValidationInsights(BaseModel):
    is_valid: bool = Field(
        description="Indicates whether the validation results in valid or not."
    )
    additional_info: Optional[str] = Field(
        default=None, description="Any additional information or context related to the validation."
    )

    def llm_str(self):
        return f"**Feedback Information:**\n{self.additional_info}"


class UpdateAnalysis(BaseModel):
    update_degree: int = Field(
        description="Degree to which the diagram needs update. 0 means no update, 10 means complete update."
    )
    feedback: str = Field(description="Feedback provided on the analysis.")

    def llm_str(self):
        return f"**Updated Analysis:**\n{self.analysis.llm_str()}\n\n**Feedback:**\n{self.feedback}"


class MetaAnalysisInsights(BaseModel):
    project_type: str = Field(
        description="Type/category of the project (e.g., web framework, data processing, ML library, etc.)")
    domain: str = Field(
        description="Domain or field the project belongs to (e.g., web development, data science, DevOps, etc.)")
    architectural_patterns: List[str] = Field(description="Main architectural patterns typically used in such projects")
    expected_components: List[str] = Field(description="Expected high-level components/modules based on project type")
    technology_stack: List[str] = Field(description="Main technologies, frameworks, and libraries used")
    architectural_bias: str = Field(
        description="Guidance on how to interpret and organize components for this project type")

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
