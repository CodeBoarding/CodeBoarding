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
    def get_cluster_analysis_message(self) -> str:
        pass

    @abstractmethod
    def get_final_analysis_message(self) -> str:
        pass

    @abstractmethod
    def get_cfg_message(self) -> str:
        pass

    @abstractmethod
    def get_source_message(self) -> str:
        pass

    @abstractmethod
    def get_classification_message(self) -> str:
        pass

    @abstractmethod
    def get_conclusive_analysis_message(self) -> str:
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
    def get_validator_system_message(self) -> str:
        pass

    @abstractmethod
    def get_component_validation_component(self) -> str:
        pass

    @abstractmethod
    def get_relationships_validation(self) -> str:
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
