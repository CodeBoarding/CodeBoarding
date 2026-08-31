"""
Abstract Prompt Factory Module

Defines the abstract base class for prompt factories with all prompt methods.
"""

from abc import ABC, abstractmethod

NAMING_MODEL_MESSAGE = """You are reading a codebase through its identifiers only.

Name the top-level architectural components a maintainer of this repo would name, and the
vocabulary each one owns.

Rules:
- `machinery` is vocabulary naming how software is built rather than what this system is
  about: Handler, Repository, Controller, View, Context, Dto, Options, Factory, Entry, Item,
  Event, Service. A machinery word must never own a component, or every handler in the system
  lands in one box -- which is a layer, not a component.
- A component's `owns` is domain vocabulary: the words naming the problem this system solves.
  Use words that actually appear in the identifiers below.
- Every component must be one responsibility. Never name a component by joining two things
  with "and" or "&".
- Where a scope below is named after part of the problem, name a component after it too, so
  that the scope and the vocabulary agree.

Top-level scopes:
{scopes}

Most frequent identifier words, with counts:
{vocabulary}

A sample of identifiers:
{identifiers}
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

    def get_naming_model_message(self) -> str:
        """Read a repo's architecture out of its identifiers.

        Concrete rather than abstract: this asks for a structured reading of a vocabulary,
        not for prose, so no provider needs its own wording.
        """
        return NAMING_MODEL_MESSAGE
