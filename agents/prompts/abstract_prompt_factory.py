"""
Abstract Prompt Factory Module

Defines the abstract base class for prompt factories with all prompt methods.
"""

from abc import ABC, abstractmethod

TREE_PLAN_SYSTEM_MESSAGE = (
    "You group the candidate groups of one scope of a codebase into the components a maintainer "
    "would draw in an architecture diagram. Answer with JSON only."
)

TREE_PLAN_MESSAGE = """Scope: {scope} ({units} files in {count} candidate groups, listed largest first)
{groups}

Each candidate group has already merged the scopes that share a name. Fold them into at most
{budget} components, each for one responsibility, for example the web app, the mobile app and the
hybrid app into "Customer experiences".

Rules:
- Every label appears in exactly one component's members. Never split a candidate group.
- A component holds at least {floor} files. A group with fewer files joins the component whose
  purpose it serves; it never stands alone.
- Keep apart what does not belong together: use the budget before lumping unrelated groups.
- Name each component for its one responsibility. Never join two things with "and" or "&".
- owns: at most 5 lowercase single words this component claims beyond its group names, taken from
  the identifiers listed under its own groups. Never words naming how software is built (handler,
  service, repository, converter).

Answer with JSON only, no prose, in this shape:
{{"groups": [{{"name": "Customer experiences", "members": ["G1", "G4"], "owns": ["customer"]}}]}}
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
