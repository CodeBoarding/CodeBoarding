import logging
import os

from langchain_core.prompts import PromptTemplate
from langgraph.prebuilt import create_react_agent

from agents.agent import CodeBoardingAgent
from agents.agent_responses import ValidationInsights, AnalysisInsights
from agents.prompts import (
    get_component_validation_component,
    get_relationships_validation,
    get_validator_system_message,
)
from agents.prompts import get_component_validation_component, get_relationships_validation, get_validator_system_message
from agents.monitoring import monitoring
from static_analyzer.analysis_result import StaticAnalysisResults

logger = logging.getLogger(__name__)


class ValidatorAgent(CodeBoardingAgent):
    def __init__(self, repo_dir, static_analysis: StaticAnalysisResults):
        super().__init__(repo_dir, static_analysis, get_validator_system_message())
        self.agent = create_react_agent(
            model=self.llm,
            tools=[
                self.read_source_reference,
                self.read_packages_tool,
                self.read_file_structure,
                self.read_structure_tool,
                self.read_file_tool,
                self.read_cfg_tool,
                self.read_method_invocations_tool,
            ],
        )

        self.valid_component_prompt = PromptTemplate(
            template=get_component_validation_component(), input_variables=["analysis"]
        )
        self.valid_relations_prompt = PromptTemplate(
            template=get_relationships_validation(), input_variables=["analysis"]
        )

    @monitoring
    def validate_components(self, analysis: AnalysisInsights):
        return self._parse_invoke(self.valid_component_prompt.format(analysis=analysis.llm_str()), ValidationInsights)

    @monitoring
    def validate_relations(self, analysis: AnalysisInsights):
        return self._parse_invoke(self.valid_relations_prompt.format(analysis=analysis.llm_str()), ValidationInsights)

    @monitoring
    def validate_references(self, analysis: AnalysisInsights):
        """
        Validating for:
        - Each component has at least one source code reference.
        - Source code reference is linked to the actual source code.
        - Source code reference is correct i.e. we don't point to non-existing methods in existing files.
        """
        info = []
        for component in analysis.components:
            if not component.referenced_source_code:
                info.append(
                    f"Component {component.name} has no source code references. "
                    f"Each component MUST HAVE source code reference."
                    f"Find the source code reference via `getSourceCode` tool or if it is a file reference validate with `readFile`."
                )
                continue

            for ref in component.referenced_source_code:
                if not ref.reference_file:
                    info.append(
                        f"Component {component.name} has incorrect source references: '{ref.llm_str()}'. "
                        f"Retry finding the proper source code reference via `getSourceCode` tool or if it is a file reference validate with `readFile`."
                    )
                    continue
                # Now validate the actual reference
                no_code_reference = True
                for lang in self.static_analysis.get_languages():
                    try:
                        node = self.static_analysis.get_reference(lang, ref.qualified_name)
                        if node.file_path != ref.reference_file:
                            info.append(
                                f"Component {component.name} has incorrect source references: '{ref.llm_str()}'. "
                                f"Expected: '{node.file_path}' (Lines: {node.line_start, node.line_end}), but found: '{ref.reference_file}' (Lines: {ref.reference_start_line, ref.reference_end_line}). "
                                f"Apply the correct reference please, maybe it is a full file reference, then validate with `readFile` tool."
                            )
                            break
                        no_code_reference = False
                        break
                    except ValueError:
                        continue
                    except FileExistsError:
                        if os.path.exists(ref.reference_file):
                            no_code_reference = False
                            break
                if no_code_reference:  # check if it is a file reference
                    file_path = ref.qualified_name.replace(".", "/")  # Get file path
                    full_path = os.path.join(self.repo_dir, file_path)
                    # This is the case when the reference is a file path but wrong:
                    file_ref = ".".join(full_path.rsplit("/", 1))
                    paths = [full_path, f"{file_path}.py", f"{file_path}.ts", f"{file_path}.tsx", file_ref]
                    for path in paths:
                        if os.path.exists(path):
                            if ref.reference_file != path and (not ref.reference_file.startswith(path)):
                                info.append(
                                    f"Component {component.name} has an incorrect reference: '{ref.llm_str()}'. "
                                    f"Expected: '{path}', but found: '{ref.reference_file}'. "
                                    f"Apply the correct reference please, maybe it is a full file reference, then validate with `readFile` tool."
                                )
                            else:
                                break
                if not os.path.exists(ref.reference_file):
                    info.append(
                        f"Component {component.name} has {ref.qualified_name} as an incorrect reference: '{ref.llm_str()}'. "
                        f"{ref.qualified_name} is INCORRECT reference, there is no such module or function/class/method in the project. Please reconsider by using `getSourceCode` tool to find the correct reference or validate with `readFile` tool if it is a file reference."
                    )
        if info:
            return ValidationInsights(is_valid=False, additional_info="\n".join(info))
        return ValidationInsights(is_valid=True, additional_info="All references are valid.")

    @monitoring
    def validate_component_relations(self, analysis: AnalysisInsights):
        info = []
        for relation in analysis.components_relations:
            src = relation.src_name
            dst = relation.dst_name
            if not any(c.name == src for c in analysis.components):
                info.append(
                    f"Source component '{src}' in relation {relation.llm_str()} does not exist in the analysis."
                )
            if not any(c.name == dst for c in analysis.components):
                info.append(
                    f"Destination component '{dst}' in relation {relation.llm_str()} does not exist in the analysis."
                )

        if info:
            return ValidationInsights(is_valid=False, additional_info="\n".join(info))
        return ValidationInsights(is_valid=True, additional_info="All component relations are valid.")

    def run(self, analysis: AnalysisInsights):
        """
        Run the validation process on the provided analysis.
        This method should return a tuple containing invalid components and invalid relations.
        """
        logger.info(f"[ValidatorAgent] Running validation on the analysis.")
        insights = ""
        valid = True
        invalid_components = self.validate_components(analysis)
        if not invalid_components.is_valid:
            insights += f"{invalid_components.llm_str()}\n\n"
            valid = False
        invalid_relations = self.validate_relations(analysis)
        if not invalid_relations.is_valid:
            insights += f"{invalid_relations.llm_str()}\n\n"
            valid = False
        reference_validation = self.validate_references(analysis)
        if not reference_validation.is_valid:
            insights += f"{reference_validation.llm_str()}\n\n"
            valid = False
        component_relations = self.validate_component_relations(analysis)
        if not component_relations.is_valid:
            insights += f"{component_relations.llm_str()}\n\n"
            valid = False
        logger.info(f"[ValidatorAgent] Validation result: [Valid: {valid}] with insights: {insights}")

        return ValidationInsights(is_valid=valid, additional_info=insights)
