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
    def get_cfg_details_message(self) -> str:
        pass

    @abstractmethod
    def get_details_message(self) -> str:
        pass

    @abstractmethod
    def get_incremental_grouping_message(self) -> str:
        pass

    @abstractmethod
    def get_planning_message(self) -> str:
        pass

    @abstractmethod
    def get_scope_relations_message(self) -> str:
        pass

    def get_api_surfaces_message(self) -> str:
        return API_SURFACES_MESSAGE

    def get_relation_analysis_message(self) -> str:
        return RELATION_ANALYSIS_MESSAGE


API_SURFACES_MESSAGE = """Analyze component API surfaces for `{project_name}`.

Project Context:
{meta_context}

Project Type: {project_type}

Components:
{component_summaries}

Known static call evidence between components (incomplete; do not treat as the full communication model):
{static_call_evidence}

Identify each component's communication surface. For every component, describe:
- provided_interfaces: important methods/classes/config symbols it exposes or uses as entrypoints
- consumed_interfaces: important methods/classes/config symbols it calls, configures, imports, or expects from others
- incoming_mechanisms and outgoing_mechanisms: direct calls, runtime dispatch, plugin hooks, REST, queues, files, config, reflection/import, subprocesses, etc.

Static call evidence is incomplete. Reason from component APIs, registries, protocols, runtime dispatch, plugin hooks, configuration, and data flow."""


RELATION_ANALYSIS_MESSAGE = """Discover architectural communication relations for `{project_name}`.

Project Context:
{meta_context}

Project Type: {project_type}

Components:
{component_summaries}

Component API surfaces:
{api_surfaces}

Known static call evidence between components (incomplete; use as evidence only):
{static_call_evidence}

Create the component relations. Do not limit relations to static calls. First reason from component APIs, runtime dispatch, plugin hooks, REST/queues/files/config, reflection/imports, and data flow. Use static calls only as one evidence source.

For each relation:
- src_name and dst_name must exactly match component names
- relation should be a short architectural phrase
- evidence should concisely explain the communication mechanism
- key_edges should contain 1-3 important source-to-target code references when possible, similar to key_entities
- avoid generic implementation-only calls and avoid adding relations solely because a static edge exists"""
