"""
Abstract Prompt Factory Module

Defines the abstract base class for prompt factories with all prompt methods.
"""

from abc import ABC, abstractmethod


class AbstractPromptFactory(ABC):
    """Abstract base class for prompt factories."""

    @abstractmethod
    def get_system_message(self) -> str:
        pass

    @abstractmethod
    def get_cluster_grouping_message(self) -> str:
        pass

    @abstractmethod
    def get_final_analysis_message(self) -> str:
        pass

    @abstractmethod
    def get_feedback_message(self) -> str:
        pass

    @abstractmethod
    def get_system_details_message(self) -> str:
        pass

    @abstractmethod
    def get_subcfg_details_message(self) -> str:
        pass

    @abstractmethod
    def get_cfg_details_message(self) -> str:
        pass

    @abstractmethod
    def get_enhance_structure_message(self) -> str:
        pass

    @abstractmethod
    def get_details_message(self) -> str:
        pass

    @abstractmethod
    def get_planner_system_message(self) -> str:
        pass

    @abstractmethod
    def get_expansion_prompt(self) -> str:
        pass

    @abstractmethod
    def get_system_diff_analysis_message(self) -> str:
        pass

    @abstractmethod
    def get_diff_analysis_message(self) -> str:
        pass

    @abstractmethod
    def get_system_meta_analysis_message(self) -> str:
        pass

    @abstractmethod
    def get_meta_information_prompt(self) -> str:
        pass

    @abstractmethod
    def get_file_classification_message(self) -> str:
        pass

    @abstractmethod
    def get_unassigned_files_classification_message(self) -> str:
        pass

    @abstractmethod
    def get_validation_feedback_message(self) -> str:
        """
        Get the validation feedback prompt template.

        This prompt is used when LLM output fails validation checks and needs to be corrected.
        The returned string should be a template with placeholders for:
        - {original_output}: The LLM's original output that failed validation
        - {feedback_list}: Bulleted list of validation issues found
        - {original_prompt}: The original prompt that generated the output

        Returns:
            Prompt template string for validation feedback
        """
        pass
