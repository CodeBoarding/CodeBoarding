"""Test script to verify depth computation works correctly with deep nesting."""

from diagram_analysis.analysis_json import _compute_depth_level
from agents.agent_responses import AnalysisInsights, Component, SourceCodeReference


def create_analysis_with_components(names: list[str]) -> AnalysisInsights:
    """Create a simple AnalysisInsights with the given component names."""
    components = [
        Component(
            name=name,
            description=f"Description for {name}",
            key_entities=[
                SourceCodeReference(
                    qualified_name=f"entity_{name}",
                    reference_file="test.py",
                    reference_start_line=1,
                    reference_end_line=10,
                )
            ],
            assigned_files=[],
            source_cluster_ids=[],
        )
        for name in names
    ]
    return AnalysisInsights(
        description="Test analysis",
        components=components,
        components_relations=[],
    )


def test_depth_computation():
    """Test that depth is computed correctly for various nesting levels."""

    # Test 1: No sub-analyses (depth 1)
    result = _compute_depth_level(None)
    assert result == 1, f"Expected depth 1 for None, got {result}"
    print("✓ Test 1 passed: No sub-analyses returns depth 1")

    # Test 2: Empty sub-analyses (depth 1)
    result = _compute_depth_level({})
    assert result == 1, f"Expected depth 1 for empty dict, got {result}"
    print("✓ Test 2 passed: Empty sub-analyses returns depth 1")

    # Test 3: One level of sub-analyses (depth 2)
    # Component A has sub-analysis with components B and C
    sub_a = create_analysis_with_components(["B", "C"])
    sub_analyses_2 = {
        "A": (sub_a, sub_a.components),  # A expands to B, C
    }
    result = _compute_depth_level(sub_analyses_2)
    assert result == 2, f"Expected depth 2 for one level, got {result}"
    print("✓ Test 3 passed: One level of sub-analyses returns depth 2")

    # Test 4: Two levels of sub-analyses (depth 3)
    # Component A has sub-analysis with component B
    # Component B has sub-analysis with component C
    sub_a = create_analysis_with_components(["B"])
    sub_b = create_analysis_with_components(["C"])
    sub_analyses_3 = {
        "A": (sub_a, sub_a.components),  # A expands to B
        "B": (sub_b, sub_b.components),  # B expands to C
    }
    result = _compute_depth_level(sub_analyses_3)
    assert result == 3, f"Expected depth 3 for two levels, got {result}"
    print("✓ Test 4 passed: Two levels of sub-analyses returns depth 3")

    # Test 5: Three levels of sub-analyses (depth 4)
    # Component A -> B -> C -> D
    sub_a = create_analysis_with_components(["B"])
    sub_b = create_analysis_with_components(["C"])
    sub_c = create_analysis_with_components(["D"])
    sub_analyses_4 = {
        "A": (sub_a, sub_a.components),  # A expands to B
        "B": (sub_b, sub_b.components),  # B expands to C
        "C": (sub_c, sub_c.components),  # C expands to D
    }
    result = _compute_depth_level(sub_analyses_4)
    assert result == 4, f"Expected depth 4 for three levels, got {result}"
    print("✓ Test 5 passed: Three levels of sub-analyses returns depth 4")

    # Test 6: Four levels of sub-analyses (depth 5)
    # Component A -> B -> C -> D -> E
    sub_a = create_analysis_with_components(["B"])
    sub_b = create_analysis_with_components(["C"])
    sub_c = create_analysis_with_components(["D"])
    sub_d = create_analysis_with_components(["E"])
    sub_analyses_5 = {
        "A": (sub_a, sub_a.components),  # A expands to B
        "B": (sub_b, sub_b.components),  # B expands to C
        "C": (sub_c, sub_c.components),  # C expands to D
        "D": (sub_d, sub_d.components),  # D expands to E
    }
    result = _compute_depth_level(sub_analyses_5)
    assert result == 5, f"Expected depth 5 for four levels, got {result}"
    print("✓ Test 6 passed: Four levels of sub-analyses returns depth 5")

    # Test 7: Multiple branches with different depths (depth 4)
    # Branch 1: A -> B -> C (depth 3)
    # Branch 2: A -> D -> E -> F (depth 4)
    sub_a = create_analysis_with_components(["B", "D"])
    sub_b = create_analysis_with_components(["C"])
    sub_c = create_analysis_with_components([])
    sub_d = create_analysis_with_components(["E"])
    sub_e = create_analysis_with_components(["F"])
    sub_analyses_multi = {
        "A": (sub_a, sub_a.components),  # A expands to B, D
        "B": (sub_b, sub_b.components),  # B expands to C
        "C": (sub_c, sub_c.components),  # C is leaf
        "D": (sub_d, sub_d.components),  # D expands to E
        "E": (sub_e, sub_e.components),  # E expands to F
    }
    result = _compute_depth_level(sub_analyses_multi)
    assert result == 4, f"Expected depth 4 for multiple branches, got {result}"
    print("✓ Test 7 passed: Multiple branches returns correct max depth 4")

    print("\n✅ All depth computation tests passed!")


if __name__ == "__main__":
    test_depth_computation()
