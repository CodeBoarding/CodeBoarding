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

    def run(self, analysis: AnalysisInsights):
        """
        Run the validation process on the provided analysis.
        This method should return a tuple containing invalid components and invalid relations.
        """
        insights = ""
        invalid_components = self.validate_components(analysis)
        if not invalid_components.is_valid:
            insights += f"{invalid_components.llm_str()}\n\n"
        invalid_relations = self.validate_relations(analysis)
        if not invalid_relations.is_valid:
            insights += f"{invalid_relations.llm_str()}\n\n"
        return ValidationInsights(is_valid=invalid_components.is_valid or invalid_relations.is_valid,
                                  additional_info=insights)
