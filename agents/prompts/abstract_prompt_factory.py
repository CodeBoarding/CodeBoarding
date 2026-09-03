"""
Abstract Prompt Factory Module

Defines the abstract base class for prompt factories with all prompt methods.
"""

from abc import ABC, abstractmethod

TREE_PLAN_MESSAGE = """You are grouping the scopes of a codebase into the components a maintainer would draw.

Scope: {scope}

Each candidate group below has already merged the scopes that share a name. Fold them into at most
{budget} components where they serve one purpose together, for example the web app, the mobile app
and the hybrid app into "Customer experiences", and leave apart what does not belong together.

Rules:
- Never split a candidate group; a component gathers whole groups.
- Every candidate group label must appear in exactly one component's members.
- Name each component for its one responsibility. Never join two things with "and" or "&".
- `owns` is domain vocabulary a component claims beyond its groups' names: words that appear in the
  identifiers below, never words naming how software is built (Handler, Service, Repository).

Candidate groups:
{groups}
"""


class AbstractPromptFactory(ABC):
    """Abstract base class for prompt factories."""

    @abstractmethod
    def get_system_message(self) -> str:
        pass

    @abstractmethod
    def get_final_analysis_message(self) -> str:
        pass

    @abstractmethod
    def get_planner_system_message(self) -> str:
        pass

    @abstractmethod
    def get_expansion_prompt(self) -> str:
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
    def get_validation_feedback_message(self) -> str:
        pass

    @abstractmethod
    def get_system_details_message(self) -> str:
        pass

    @abstractmethod
    def get_details_message(self) -> str:
        pass

    @abstractmethod
    def get_scope_relations_message(self) -> str:
        pass

    @abstractmethod
    def get_api_surfaces_message(self) -> str:
        pass

    @abstractmethod
    def get_relation_analysis_message(self) -> str:
        pass

    def get_tree_plan_message(self) -> str:
        """Fold a scope's candidate groups into components.

        Concrete rather than abstract: this asks for a structured grouping, not for prose,
        so no provider needs its own wording.
        """
        return TREE_PLAN_MESSAGE
