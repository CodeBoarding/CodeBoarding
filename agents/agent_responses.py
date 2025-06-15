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
            return f"`{self.qualified_name}` (full file reference)"
        return f"`{self.qualified_name}` ({self.reference_start_line}:{self.reference_end_line})"


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
