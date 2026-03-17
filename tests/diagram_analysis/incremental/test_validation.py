"""Tests for incremental analysis validation utilities."""

import pytest
from unittest.mock import MagicMock, patch

from agents.agent_responses import AnalysisInsights, Component, FileMethodGroup, Relation
from agents.validation import ValidationResult
from diagram_analysis.incremental.validation import validate_incremental_update
from static_analyzer.analysis_result import StaticAnalysisResults


@pytest.fixture
def sample_analysis() -> AnalysisInsights:
    """Create a sample analysis for testing."""
    return AnalysisInsights(
        description="Test analysis",
        components=[
            Component(
                name="ComponentA",
                description="Test component A",
                key_entities=[],
                file_methods=[FileMethodGroup(file_path="src/a.py")],
                source_cluster_ids=[1],
            ),
            Component(
                name="ComponentB",
                description="Test component B",
                key_entities=[],
                file_methods=[FileMethodGroup(file_path="src/b.py")],
                source_cluster_ids=[2],
            ),
        ],
        components_relations=[Relation(relation="calls", src_name="ComponentA", dst_name="ComponentB")],
    )


@pytest.fixture
def mock_static_analysis():
    """Create a mock static analysis object."""
    static_analysis = MagicMock(spec=StaticAnalysisResults)
    static_analysis.get_languages.return_value = ["Python"]
    static_analysis.get_cfg.return_value = {}
    return static_analysis


def create_mock_validator(name: str, return_value: ValidationResult, side_effect=None):
    """Create a mock validator function with proper __name__ attribute."""
    mock = MagicMock(return_value=return_value)
    mock.__name__ = name
    if side_effect:
        mock.side_effect = side_effect
    return mock


VALID_RESULT = ValidationResult(is_valid=True, feedback_messages=[])


def _set_all_passing(mock_entities, mock_qnames, mock_relations):
    """Configure all three validator mocks to pass."""
    mock_entities.return_value = VALID_RESULT
    mock_entities.__name__ = "validate_key_entities"
    mock_qnames.return_value = VALID_RESULT
    mock_qnames.__name__ = "validate_qualified_names"
    mock_relations.return_value = VALID_RESULT
    mock_relations.__name__ = "validate_relation_component_names"


class TestValidateIncrementalUpdate:
    """Tests for validate_incremental_update function."""

    @patch("diagram_analysis.incremental.validation.build_all_cluster_results")
    @patch("diagram_analysis.incremental.validation.validate_relation_component_names")
    @patch("diagram_analysis.incremental.validation.validate_qualified_names")
    @patch("diagram_analysis.incremental.validation.validate_key_entities")
    def test_returns_true_when_all_validators_pass(
        self,
        mock_validate_entities,
        mock_validate_qnames,
        mock_validate_relations,
        mock_build_clusters,
        sample_analysis: AnalysisInsights,
        mock_static_analysis,
    ):
        """Test that function returns True when all validators pass."""
        mock_build_clusters.return_value = {}
        _set_all_passing(mock_validate_entities, mock_validate_qnames, mock_validate_relations)

        result = validate_incremental_update(sample_analysis, mock_static_analysis)

        assert result is True
        mock_validate_qnames.assert_called_once()
        mock_validate_entities.assert_called_once()
        mock_validate_relations.assert_called_once()

    @patch("diagram_analysis.incremental.validation.build_all_cluster_results")
    @patch("diagram_analysis.incremental.validation.validate_relation_component_names")
    @patch("diagram_analysis.incremental.validation.validate_qualified_names")
    @patch("diagram_analysis.incremental.validation.validate_key_entities")
    def test_returns_false_when_one_validator_fails(
        self,
        mock_validate_entities,
        mock_validate_qnames,
        mock_validate_relations,
        mock_build_clusters,
        sample_analysis: AnalysisInsights,
        mock_static_analysis,
    ):
        """Test that function returns False when any validator fails."""
        mock_build_clusters.return_value = {}
        _set_all_passing(mock_validate_entities, mock_validate_qnames, mock_validate_relations)
        mock_validate_entities.return_value = ValidationResult(is_valid=False, feedback_messages=["Missing entity"])

        result = validate_incremental_update(sample_analysis, mock_static_analysis)

        assert result is False

    @patch("diagram_analysis.incremental.validation.build_all_cluster_results")
    @patch("diagram_analysis.incremental.validation.validate_relation_component_names")
    @patch("diagram_analysis.incremental.validation.validate_qualified_names")
    @patch("diagram_analysis.incremental.validation.validate_key_entities")
    def test_returns_false_when_all_validators_fail(
        self,
        mock_validate_entities,
        mock_validate_qnames,
        mock_validate_relations,
        mock_build_clusters,
        sample_analysis: AnalysisInsights,
        mock_static_analysis,
    ):
        """Test that function returns False when all validators fail."""
        mock_build_clusters.return_value = {}
        mock_validate_entities.return_value = ValidationResult(is_valid=False, feedback_messages=["Missing entity"])
        mock_validate_entities.__name__ = "validate_key_entities"
        mock_validate_qnames.return_value = ValidationResult(is_valid=False, feedback_messages=["Bad qname"])
        mock_validate_qnames.__name__ = "validate_qualified_names"
        mock_validate_relations.return_value = ValidationResult(is_valid=False, feedback_messages=["Bad relation"])
        mock_validate_relations.__name__ = "validate_relation_component_names"

        result = validate_incremental_update(sample_analysis, mock_static_analysis)

        assert result is False

    @patch("diagram_analysis.incremental.validation.build_all_cluster_results")
    @patch("diagram_analysis.incremental.validation.validate_relation_component_names")
    @patch("diagram_analysis.incremental.validation.validate_qualified_names")
    @patch("diagram_analysis.incremental.validation.validate_key_entities")
    def test_handles_validator_exception(
        self,
        mock_validate_entities,
        mock_validate_qnames,
        mock_validate_relations,
        mock_build_clusters,
        sample_analysis: AnalysisInsights,
        mock_static_analysis,
    ):
        """Test that function handles exceptions from validators gracefully."""
        mock_build_clusters.return_value = {}
        _set_all_passing(mock_validate_entities, mock_validate_qnames, mock_validate_relations)
        mock_validate_entities.side_effect = RuntimeError("boom")

        result = validate_incremental_update(sample_analysis, mock_static_analysis)

        assert result is False

    @patch("diagram_analysis.incremental.validation.build_all_cluster_results")
    @patch("diagram_analysis.incremental.validation.validate_relation_component_names")
    @patch("diagram_analysis.incremental.validation.validate_qualified_names")
    @patch("diagram_analysis.incremental.validation.validate_key_entities")
    def test_builds_cluster_results_with_static_analysis(
        self,
        mock_validate_entities,
        mock_validate_qnames,
        mock_validate_relations,
        mock_build_clusters,
        sample_analysis: AnalysisInsights,
        mock_static_analysis,
    ):
        """Test that function builds cluster results from static analysis."""
        mock_build_clusters.return_value = {"Python": {"clusters": []}}
        _set_all_passing(mock_validate_entities, mock_validate_qnames, mock_validate_relations)

        validate_incremental_update(sample_analysis, mock_static_analysis)

        mock_build_clusters.assert_called_once_with(mock_static_analysis)

    @patch("diagram_analysis.incremental.validation.build_all_cluster_results")
    @patch("diagram_analysis.incremental.validation.validate_relation_component_names")
    @patch("diagram_analysis.incremental.validation.validate_qualified_names")
    @patch("diagram_analysis.incremental.validation.validate_key_entities")
    def test_creates_validation_context_with_cfg(
        self,
        mock_validate_entities,
        mock_validate_qnames,
        mock_validate_relations,
        mock_build_clusters,
        sample_analysis: AnalysisInsights,
        mock_static_analysis,
    ):
        """Test that function creates validation context with CFG graphs."""
        mock_build_clusters.return_value = {}
        mock_static_analysis.get_cfg.return_value = {"nodes": []}
        _set_all_passing(mock_validate_entities, mock_validate_qnames, mock_validate_relations)

        validate_incremental_update(sample_analysis, mock_static_analysis)

        # Verify that get_cfg was called for each language
        mock_static_analysis.get_cfg.assert_called_with("Python")

    @patch("diagram_analysis.incremental.validation.build_all_cluster_results")
    @patch("diagram_analysis.incremental.validation.validate_relation_component_names")
    @patch("diagram_analysis.incremental.validation.validate_qualified_names")
    @patch("diagram_analysis.incremental.validation.validate_key_entities")
    def test_returns_false_when_relation_validator_fails(
        self,
        mock_validate_entities,
        mock_validate_qnames,
        mock_validate_relations,
        mock_build_clusters,
        sample_analysis: AnalysisInsights,
        mock_static_analysis,
    ):
        """Test that function returns False when relation component names are invalid."""
        mock_build_clusters.return_value = {}
        _set_all_passing(mock_validate_entities, mock_validate_qnames, mock_validate_relations)
        mock_validate_relations.return_value = ValidationResult(
            is_valid=False, feedback_messages=["Unknown component name in relation"]
        )

        result = validate_incremental_update(sample_analysis, mock_static_analysis)

        assert result is False
        mock_validate_relations.assert_called_once()
