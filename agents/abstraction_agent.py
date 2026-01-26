import logging
from pathlib import Path

from langchain_core.prompts import PromptTemplate
from pydantic import BaseModel, Field

from agents.agent import LargeModelAgent
from agents.agent_responses import (
    AnalysisInsights,
    ClusterAnalysis,
    MetaAnalysisInsights,
)
from agents.cluster_methods_mixin import ClusterMethodsMixin
from agents.prompts import (
    get_system_message,
    get_cluster_grouping_message,
    get_final_analysis_message,
    get_feedback_message,
)
from agents.validation import (
    ValidationContext,
    validate_cluster_coverage,
    validate_component_relationships,
)
from monitoring import trace
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cluster_helpers import build_all_cluster_results, get_all_cluster_ids
from static_analyzer.graph import ClusterResult

logger = logging.getLogger(__name__)


# Response model for new file classification
class FileClassification(BaseModel):
    """Classification of a single file to a component."""

    file_path: str = Field(description="The file path being classified")
    component_name: str = Field(
        description="Name of the existing component this file belongs to, or 'NEW_COMPONENT' if it should be a new component"
    )
    new_component_name: str | None = Field(
        default=None, description="If component_name is 'NEW_COMPONENT', the suggested name for the new component"
    )
    new_component_description: str | None = Field(
        default=None, description="If component_name is 'NEW_COMPONENT', a description of what the new component does"
    )
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="Confidence score for this classification (0-1)")
    reasoning: str = Field(description="Brief explanation of why this file belongs to this component")


class NewFilesClassificationResult(BaseModel):
    """Result of classifying new files into components."""

    classifications: list[FileClassification] = Field(description="List of file classifications")


# Prompt template for classifying new files
NEW_FILES_CLASSIFICATION_PROMPT = """You are classifying new source code files into existing software components.

## Project: {project_name}

## Existing Components
{components_summary}

## New Files to Classify
{new_files_summary}

## Instructions
For each new file, determine which existing component it most likely belongs to based on:
1. File path and naming patterns (e.g., files in `agents/` likely belong to an Agent component)
2. The file's apparent purpose based on its name
3. Similar files already in each component

If a file clearly doesn't fit any existing component and represents genuinely new functionality,
classify it as "NEW_COMPONENT" and suggest a name and description.

Be conservative about creating new components - only do so if the file represents distinct functionality
not covered by existing components.

Respond with a classification for each file."""


class AbstractionAgent(ClusterMethodsMixin, LargeModelAgent):
    def __init__(
        self,
        repo_dir: Path,
        static_analysis: StaticAnalysisResults,
        project_name: str,
        meta_context: MetaAnalysisInsights,
    ):
        super().__init__(repo_dir, static_analysis, get_system_message())

        self.project_name = project_name
        self.meta_context = meta_context

        self.prompts = {
            "group_clusters": PromptTemplate(
                template=get_cluster_grouping_message(),
                input_variables=["project_name", "cfg_clusters", "meta_context", "project_type"],
            ),
            "final_analysis": PromptTemplate(
                template=get_final_analysis_message(),
                input_variables=["project_name", "cluster_analysis", "meta_context", "project_type"],
            ),
            "feedback": PromptTemplate(template=get_feedback_message(), input_variables=["analysis", "feedback"]),
        }

    @trace
    def step_clusters_grouping(self, cluster_results: dict[str, ClusterResult]) -> ClusterAnalysis:
        logger.info(f"[AbstractionAgent] Grouping CFG clusters for: {self.project_name}")

        meta_context_str = self.meta_context.llm_str() if self.meta_context else "No project context available."
        project_type = self.meta_context.project_type if self.meta_context else "unknown"

        programming_langs = self.static_analysis.get_languages()

        # Build cluster string using the pre-computed cluster results
        cluster_str = self._build_cluster_string(programming_langs, cluster_results)

        prompt = self.prompts["group_clusters"].format(
            project_name=self.project_name,
            cfg_clusters=cluster_str,
            meta_context=meta_context_str,
            project_type=project_type,
        )

        cluster_analysis = self._validation_invoke(
            prompt,
            ClusterAnalysis,
            validators=[validate_cluster_coverage],
            context=ValidationContext(
                cluster_results=cluster_results,
                expected_cluster_ids=get_all_cluster_ids(cluster_results),
            ),
        )
        return cluster_analysis

    @trace
    def step_final_analysis(
        self, cluster_analysis: ClusterAnalysis, cluster_results: dict[str, ClusterResult]
    ) -> AnalysisInsights:
        logger.info(f"[AbstractionAgent] Generating final analysis for: {self.project_name}")

        meta_context_str = self.meta_context.llm_str() if self.meta_context else "No project context available."
        project_type = self.meta_context.project_type if self.meta_context else "unknown"

        cluster_str = cluster_analysis.llm_str() if cluster_analysis else "No cluster analysis available."

        prompt = self.prompts["final_analysis"].format(
            project_name=self.project_name,
            cluster_analysis=cluster_str,
            meta_context=meta_context_str,
            project_type=project_type,
        )

        # Build validation context with CFG graphs for edge checking
        context = ValidationContext(
            cluster_results=cluster_results,
            cfg_graphs={lang: self.static_analysis.get_cfg(lang) for lang in self.static_analysis.get_languages()},
        )

        return self._validation_invoke(
            prompt, AnalysisInsights, validators=[validate_component_relationships], context=context
        )

    def run(self):
        # Build full cluster results dict for all languages ONCE
        cluster_results = build_all_cluster_results(self.static_analysis)

        # Step 1: Group related clusters together into logical components
        cluster_analysis = self.step_clusters_grouping(cluster_results)

        # Step 2: Generate abstract components from grouped clusters
        analysis = self.step_final_analysis(cluster_analysis, cluster_results)
        # Step 3: Sanitize cluster IDs (remove invalid ones)
        self._sanitize_component_cluster_ids(analysis, cluster_results=cluster_results)
        # Step 4: Assign files to components (deterministic + LLM-based with validation)
        self.classify_files(analysis, cluster_results)
        # Step 5: Fix source code reference lines (resolves reference_file paths for key_entities)
        analysis = self.fix_source_code_reference_lines(analysis)
        # Step 6: Ensure unique key entities across components
        self._ensure_unique_key_entities(analysis)

        return analysis, cluster_results

    @trace
    def classify_new_files(
        self,
        new_files: list[str],
        existing_analysis: AnalysisInsights,
    ) -> dict[str, list[str]]:
        """Classify new files into existing or new components.

        This is a lightweight operation for incremental analysis that determines
        where new files should be assigned without full re-analysis.

        Args:
            new_files: List of new file paths to classify
            existing_analysis: The existing AnalysisInsights with current components

        Returns:
            Dictionary mapping component_name -> list of file paths
            Special key "NEW_COMPONENTS" contains tuples of (name, description, files)
        """
        if not new_files:
            return {}

        logger.info(f"[AbstractionAgent] Classifying {len(new_files)} new files")

        # Build components summary
        components_summary = []
        for comp in existing_analysis.components:
            file_examples = comp.assigned_files[:5] if comp.assigned_files else []
            file_str = ", ".join(file_examples)
            if len(comp.assigned_files) > 5:
                file_str += f" (and {len(comp.assigned_files) - 5} more)"
            components_summary.append(f"**{comp.name}**: {comp.description}\n   Files: {file_str}")

        # Build new files summary with file content hints
        new_files_summary = []
        for file_path in new_files:
            full_path = self.repo_dir / file_path
            hint = ""
            if full_path.exists():
                try:
                    content = full_path.read_text(encoding="utf-8", errors="replace")
                    # Get first few lines for context
                    first_lines = content.split("\n")[:10]
                    hint = "\n".join(f"    {line}" for line in first_lines if line.strip())
                    if len(hint) > 500:
                        hint = hint[:500] + "..."
                except Exception:
                    hint = "(could not read file)"
            new_files_summary.append(f"- {file_path}\n{hint}")

        prompt = NEW_FILES_CLASSIFICATION_PROMPT.format(
            project_name=self.project_name,
            components_summary="\n".join(components_summary),
            new_files_summary="\n".join(new_files_summary),
        )

        try:
            result = self._structured_invoke(prompt, NewFilesClassificationResult)

            # Build result mapping
            assignments: dict[str, list[str]] = {}
            new_components: list[tuple[str, str, list[str]]] = []

            # Cast to our response type
            if isinstance(result, NewFilesClassificationResult):
                classification_result = result
            elif hasattr(result, "classifications"):
                classification_result = result  # type: ignore
            else:
                classification_result = NewFilesClassificationResult.model_validate(result.model_dump())

            for classification in classification_result.classifications:
                if classification.component_name == "NEW_COMPONENT":
                    # Track new component suggestions
                    name = classification.new_component_name or f"New_{classification.file_path.split('/')[-1]}"
                    desc = classification.new_component_description or "Newly added functionality"
                    new_components.append((name, desc, [classification.file_path]))
                else:
                    # Assign to existing component
                    if classification.component_name not in assignments:
                        assignments[classification.component_name] = []
                    assignments[classification.component_name].append(classification.file_path)

            # Consolidate new components with similar names
            if new_components:
                assignments["__NEW_COMPONENTS__"] = new_components  # type: ignore

            logger.info(
                f"[AbstractionAgent] Classified {len(new_files)} files: "
                f"{sum(len(v) for k, v in assignments.items() if k != '__NEW_COMPONENTS__')} to existing, "
                f"{len(new_components)} to new components"
            )

            return assignments

        except Exception as e:
            logger.warning(f"[AbstractionAgent] Failed to classify new files: {e}")
            # Fall back to simple heuristic classification
            return self._heuristic_classify_files(new_files, existing_analysis)

    def _heuristic_classify_files(
        self,
        new_files: list[str],
        existing_analysis: AnalysisInsights,
    ) -> dict[str, list[str]]:
        """Fallback heuristic classification based on file paths.

        Assigns files to components based on path similarity with existing assigned files.
        """
        assignments: dict[str, list[str]] = {}

        for new_file in new_files:
            new_path = Path(new_file)
            best_match = None
            best_score = 0

            for comp in existing_analysis.components:
                for assigned in comp.assigned_files:
                    assigned_path = Path(assigned)
                    # Score based on shared path components
                    shared = set(new_path.parts) & set(assigned_path.parts)
                    score = len(shared)
                    if score > best_score:
                        best_score = score
                        best_match = comp.name

            if best_match and best_score > 1:  # Require at least 2 shared path components
                if best_match not in assignments:
                    assignments[best_match] = []
                assignments[best_match].append(new_file)
            else:
                # Unclassified
                if "Unclassified" not in assignments:
                    assignments["Unclassified"] = []
                assignments["Unclassified"].append(new_file)

        return assignments

    def _structured_invoke(self, prompt: str, response_model: type) -> BaseModel:
        """Invoke LLM with structured output parsing."""
        from langchain_core.messages import HumanMessage

        # Use trustcall extractor for structured output
        from trustcall import create_extractor

        extractor = create_extractor(self.llm, tools=[response_model], tool_choice=response_model.__name__)
        result = extractor.invoke([HumanMessage(content=prompt)])

        # Extract the structured response
        if result and "responses" in result and result["responses"]:
            return result["responses"][0]
        raise ValueError("Failed to get structured response from LLM")
