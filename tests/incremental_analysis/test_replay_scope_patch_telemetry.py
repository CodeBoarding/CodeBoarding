"""Single-shot feedback loop for the scope-patch deterministic step.

Loads a captured (analysis, patch_scope, scope_patch) tuple from disk and runs
apply_scope_patch in isolation — no LLM, no e2e wrapper, no binary rebuild.
Lets you iterate on incremental_analysis/analysis_patcher.py changes in
sub-second cycles via:

    uv run pytest tests/incremental_analysis/test_replay_scope_patch_telemetry.py

The fixture is the empty-LLM-patch case captured during test 10's
"Refresh after adding backend/telemetry/" scenario: scope_id='1',
target_component_ids=['1.1','1.2','1.3','1.4'], 5 unallocated telemetry
files, scope_patch.new_components=[] and scope_patch.components=[].
"""

import pickle
from pathlib import Path

from incremental_analysis.analysis_patcher import apply_scope_patch

FIXTURE = Path(__file__).parent / "fixtures" / "scope_patch_telemetry_subsystem.pkl"

TELEMETRY_FILES = {
    "backend/telemetry/TelemetryClient.ts",
    "backend/telemetry/TelemetryConfig.ts",
    "backend/telemetry/TelemetryEvent.ts",
    "backend/telemetry/TelemetryQueue.ts",
    "backend/telemetry/TelemetryService.ts",
}


def _load() -> dict:
    with FIXTURE.open("rb") as f:
        return pickle.load(f)


def _component_files(component) -> set[str]:
    return {g.file_path for g in component.file_methods}


def test_apply_scope_patch_mints_fallback_component_for_orphaned_files() -> None:
    """When the LLM returns an empty patch, every unallocated file must still
    land on exactly one minted component. Without the deterministic fallback
    in apply_scope_patch this regresses to 0 owners and test 10 fails."""
    captured = _load()
    patched = apply_scope_patch(captured["analysis"], captured["patch_scope"], captured["scope_patch"])

    owners = [c for c in patched.components if TELEMETRY_FILES & _component_files(c)]
    assert len(owners) == 1, (
        f"expected exactly one component to own the telemetry files, got {len(owners)}: "
        f"{[(c.component_id, c.name) for c in owners]}"
    )

    owner = owners[0]
    owned = _component_files(owner)
    missing = TELEMETRY_FILES - owned
    assert not missing, f"owner {owner.component_id} ({owner.name!r}) missing files: {sorted(missing)}"


def test_apply_scope_patch_minted_component_is_brand_new_not_a_fold_in() -> None:
    """The owner of the telemetry files must not share a name with any existing
    component (test 10 asserts a created component, not a renamed/repurposed
    one). Catches the pre-1c5c8cc fold-in regression at the patcher level."""
    captured = _load()
    baseline_names = {c.name for c in captured["analysis"].components}

    patched = apply_scope_patch(captured["analysis"], captured["patch_scope"], captured["scope_patch"])

    owners = [c for c in patched.components if TELEMETRY_FILES & _component_files(c)]
    assert len(owners) == 1
    owner = owners[0]
    assert owner.name not in baseline_names, (
        f"owner {owner.name!r} already existed in baseline — patcher folded into an existing component "
        f"instead of minting a new one"
    )


def test_apply_scope_patch_minted_component_lives_under_scope_root() -> None:
    """The patcher contract is that components minted in a scope inherit the
    scope's component-id prefix (e.g. scope '1' → child '1.5'). Test 10's
    assertion #8 walks the tree to confirm the new component is rooted at '1'."""
    captured = _load()
    patched = apply_scope_patch(captured["analysis"], captured["patch_scope"], captured["scope_patch"])

    owners = [c for c in patched.components if TELEMETRY_FILES & _component_files(c)]
    assert len(owners) == 1
    owner = owners[0]
    scope_id = captured["patch_scope"].scope_id
    assert owner.component_id.startswith(
        f"{scope_id}."
    ), f"expected component_id prefixed with {scope_id!r}, got {owner.component_id!r}"
