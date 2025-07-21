import logging

from langchain_core.prompts import PromptTemplate
from langgraph.prebuilt import create_react_agent

from agents.agent import CodeBoardingAgent
from agents.agent_responses import ValidationInsights, AnalysisInsights
from agents.prompts import COMPONENT_VALIDATION_COMPONENT, RELATIONSHIPS_VALIDATION, VALIDATOR_SYSTEM_MESSAGE


class ValidatorAgent(CodeBoardingAgent):
    def __init__(self, repo_dir, output_dir, cfg):
        super().__init__(repo_dir, output_dir, cfg, VALIDATOR_SYSTEM_MESSAGE)
        self.agent = create_react_agent(model=self.llm, tools=[self.read_source_reference, self.read_packages_tool,
                                                               self.read_file_structure, self.read_structure_tool,
                                                               self.read_file_tool, self.read_cfg_tool,
                                                               self.read_method_invocations_tool])

        self.valid_component_prompt = PromptTemplate(template=COMPONENT_VALIDATION_COMPONENT,
                                                     input_variables=["analysis"])
        self.valid_relations_prompt = PromptTemplate(template=RELATIONSHIPS_VALIDATION,
                                                     input_variables=["analysis"])

    def validate_components(self, analysis: AnalysisInsights):
        return self._parse_invoke(self.valid_component_prompt.format(analysis=analysis.llm_str()),
                                  ValidationInsights)

    def validate_relations(self, analysis: AnalysisInsights):
        return self._parse_invoke(self.valid_relations_prompt.format(analysis=analysis.llm_str()),
                                  ValidationInsights)

    def validate_references(self, analysis: AnalysisInsights):
        info = []
        for component in analysis.components:
            if not component.referenced_source_code:
                info.append(f"Component {component.name} has no source code references. "
                            f"Retry finding the proper source code reference via `getPythonSourceCode` tool. Or at least the correct file path with the `readFile` path.")
            for ref in component.referenced_source_code:
                if not ref.reference_file:
                    info.append(f"Component {component.name} has incorrect source references: '{ref.llm_str()}'. "
                                f"Retry finding the proper source code reference via `getPythonSourceCode` tool. Or at least the correct file path with the `readFile` path.")
        if info:
            return ValidationInsights(is_valid=False,
                                      additional_info="\n".join(info))
        return ValidationInsights(is_valid=True, additional_info="All references are valid.")

    def validate_component_relations(self, analysis: AnalysisInsights):
        info = []
        for relation in analysis.components_relations:
            src = relation.src_name
            dst = relation.dst_name
            if not any(c.name == src for c in analysis.components):
                info.append(
                    f"Source component '{src}' in relation {relation.llm_str()} does not exist in the analysis.")
            if not any(c.name == dst for c in analysis.components):
                info.append(
                    f"Destination component '{dst}' in relation {relation.llm_str()} does not exist in the analysis.")

        if info:
            return ValidationInsights(is_valid=False,
                                      additional_info="\n".join(info))
        return ValidationInsights(is_valid=True, additional_info="All component relations are valid.")

    def run(self, analysis: AnalysisInsights):
        """
        Run the validation process on the provided analysis.
        This method should return a tuple containing invalid components and invalid relations.
        """
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
        logging.info(f"[ValidatorAgent] Validation result is {valid} with insights: {insights}")

        return ValidationInsights(is_valid=valid,
                                  additional_info=insights)
