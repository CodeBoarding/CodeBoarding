# Incremental Update System - Implementation Plan

## Overview

This document outlines the implementation plan for adding incremental analysis updates to CodeBoarding.
The goal is to reduce analysis time from ~8 minutes (full) to seconds-1 minute for iterative changes.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Incremental Update Pipeline                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────┐    ┌──────────────┐    ┌───────────────┐                  │
│  │ Load State  │───▶│ Detect Diff  │───▶│ Classify      │                  │
│  │ (manifest)  │    │ (git)        │    │ Changes       │                  │
│  └─────────────┘    └──────────────┘    └───────────────┘                  │
│                                                │                            │
│                                                ▼                            │
│  ┌─────────────┐    ┌──────────────┐    ┌───────────────┐                  │
│  │ Write       │◀───│ Execute      │◀───│ Check Impact  │                  │
│  │ Results     │    │ Plan         │    │ (LSP/CFG)     │                  │
│  └─────────────┘    └──────────────┘    └───────────────┘                  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Phase 1: Manifest & State Persistence

### New Files
- `diagram_analysis/manifest.py` - Manifest data model and I/O

### Changes
- `diagram_analysis/diagram_generator.py` - Write manifest after analysis

### Manifest Schema

```python
class AnalysisManifest(BaseModel):
    """Persisted state for incremental updates."""
    
    schema_version: int = 1
    tool_version: str
    repo_state_hash: str           # From get_repo_state_hash()
    base_commit: str               # Commit at time of analysis
    
    # Core lookup: file path -> component name
    file_to_component: dict[str, str]
    
    # Reverse lookup for validation: component -> files
    component_files: dict[str, list[str]]
    
    # Track sub-analyses
    expanded_components: list[str]  # Components with detail JSONs
```

### Output Location
```
.codeboarding/
├── analysis.json
├── analysis_manifest.json        # NEW
├── codeboarding_version.json
├── <Component_Name>.json
└── cache/
    └── static_analysis_cache.pkl
```

---

## Phase 2: Rename-Aware Git Diff

### New Files
- `repo_utils/change_detector.py` - Enhanced change detection

### Changes
- `repo_utils/git_diff.py` - Add rename detection support

### Data Models

```python
class FileChangeType(Enum):
    ADDED = "A"
    MODIFIED = "M"
    DELETED = "D"
    RENAMED = "R"
    COPIED = "C"
    TYPE_CHANGED = "T"


class FileChange:
    """Enhanced file change with rename tracking."""
    
    change_type: FileChangeType
    file_path: str                 # Current path
    old_path: str | None           # For renames: previous path
    similarity: int | None         # For renames: 0-100%
    additions: int
    deletions: int
```

### Implementation

```python
def get_file_changes(repo_dir: Path, base_ref: str) -> list[FileChange]:
    """
    Get file changes using rename-aware git diff.
    
    Uses: git diff --name-status -M -C <base_ref>...HEAD
    
    Returns FileChange objects with proper rename tracking.
    """
```

---

## Phase 3: Change Classification & Impact Analysis

### New Files
- `diagram_analysis/incremental_analyzer.py` - Main incremental logic

### Change Classification

```python
class ChangeImpact(BaseModel):
    """Result of analyzing change impact."""
    
    # Files by change type
    renames: dict[str, str]        # old_path -> new_path
    modified_files: list[str]
    added_files: list[str]
    deleted_files: list[str]
    
    # Affected components
    dirty_components: set[str]
    
    # Escalation flags
    architecture_dirty: bool       # Level 1 needs refresh
    new_component_needed: bool     # New files don't fit existing
    
    # Recommended action
    action: UpdateAction

class UpdateAction(Enum):
    NONE = "none"                  # No changes detected
    PATCH_PATHS = "patch_paths"    # Rename only - no LLM
    UPDATE_COMPONENT = "update_component"  # Re-run DetailsAgent
    UPDATE_RELATIONS = "update_relations"  # Refresh cross-component edges
    FULL_REANALYSIS = "full"       # Too many changes
```

### Impact Detection Logic

```python
def analyze_impact(
    changes: list[FileChange],
    manifest: AnalysisManifest,
    cfg: CallGraph,
) -> ChangeImpact:
    """
    Determine the blast radius of changes.
    
    1. Map changed files to components
    2. For modified files, check if references cross boundaries
    3. Determine minimal update action
    """
```

### Cross-Boundary Detection

```python
def check_cross_boundary_impact(
    file_path: str,
    owning_component: str,
    cfg: CallGraph,
    file_to_component: dict[str, str],
) -> bool:
    """
    Check if a file's references cross component boundaries.
    
    Uses CFG edges to find:
    - Outgoing: functions this file calls
    - Incoming: functions that call into this file
    
    Returns True if any reference is in a different component.
    """
```

---

## Phase 4: Update Execution

### Update Handlers

```python
class IncrementalUpdater:
    """Executes incremental updates based on impact analysis."""
    
    def __init__(
        self,
        repo_dir: Path,
        output_dir: Path,
        manifest: AnalysisManifest,
        static_analysis: StaticAnalysisResults,
    ): ...
    
    def execute(self, impact: ChangeImpact) -> UpdateResult:
        """Route to appropriate handler based on action."""
        match impact.action:
            case UpdateAction.NONE:
                return self._no_update()
            case UpdateAction.PATCH_PATHS:
                return self._patch_paths(impact.renames)
            case UpdateAction.UPDATE_COMPONENT:
                return self._update_components(impact.dirty_components)
            case UpdateAction.UPDATE_RELATIONS:
                return self._update_relations(impact.dirty_components)
            case UpdateAction.FULL_REANALYSIS:
                return self._full_reanalysis()
```

### Path Patching (No LLM)

```python
def _patch_paths(self, renames: dict[str, str]) -> UpdateResult:
    """
    Update file paths in analysis artifacts without LLM.
    
    Updates:
    - assigned_files in each Component
    - reference_file in key_entities
    - file_to_component in manifest
    """
```

### Component Update (Targeted LLM)

```python
def _update_components(self, components: set[str]) -> UpdateResult:
    """
    Re-run DetailsAgent only for affected components.
    
    1. Load existing analysis
    2. Filter CFG to component's files
    3. Run DetailsAgent.run(component)
    4. Merge result back into analysis
    """
```

---

## Phase 5: Entry Points

### New CLI Options

```bash
# Incremental update (default when analysis exists)
python main.py --local /path/to/repo --project-name MyProject

# Force full reanalysis
python main.py --local /path/to/repo --project-name MyProject --full

# Show what would change (dry run)
python main.py --local /path/to/repo --project-name MyProject --dry-run
```

### Integration with DiagramGenerator

```python
class DiagramGenerator:
    def generate_analysis(self):
        # Check if we can do incremental update
        manifest = self._load_manifest()
        
        if manifest and not self.force_full:
            impact = self._analyze_impact(manifest)
            
            if impact.action != UpdateAction.FULL_REANALYSIS:
                return self._incremental_update(impact)
        
        # Fall back to full analysis
        return self._full_analysis()
```

---

## Implementation Order

### Week 1: Foundation ✅ COMPLETE
1. [x] `diagram_analysis/manifest.py` - Manifest model and I/O
2. [x] Update `DiagramGenerator` to write manifest after analysis
3. [x] `repo_utils/change_detector.py` - Rename-aware git diff

### Week 2: Impact Analysis ✅ COMPLETE
4. [x] `diagram_analysis/incremental_analyzer.py` - Change classification
5. [x] Cross-boundary detection using CFG
6. [x] Unit tests for impact analysis (18 tests passing)

### Week 3: Update Execution ✅ COMPLETE
7. [x] Path patching (rename handling)
8. [x] Targeted component updates (DetailsAgent integration)
9. [x] Result merging (add/delete file handling)

### Week 4: Integration ✅ COMPLETE
10. [x] CLI integration (--full and --incremental flags)
11. [x] Integration tests (29 tests passing)
12. [x] Documentation (this file)

---

## Decision Thresholds

| Metric | Threshold | Action |
|--------|-----------|--------|
| Pure renames | Any count | PATCH_PATHS |
| Modified files in 1 component | ≤10 files | UPDATE_COMPONENT |
| Modified files across components | ≤3 components | UPDATE_COMPONENT (each) |
| New cross-component edges | Any | UPDATE_RELATIONS |
| Added/deleted files | ≤5% of repo | Assign to existing component |
| Added/deleted files | >5% of repo | FULL_REANALYSIS |
| Component count change needed | Any | FULL_REANALYSIS |

---

## File Structure After Implementation

```
CodeBoarding/
├── diagram_analysis/
│   ├── __init__.py
│   ├── analysis_json.py
│   ├── diagram_generator.py
│   ├── incremental_analyzer.py    # NEW
│   ├── manifest.py                # NEW
│   └── version.py
├── repo_utils/
│   ├── __init__.py
│   ├── change_detector.py         # NEW
│   ├── git_diff.py                # ENHANCED
│   └── ...
└── ...
```

---

## Testing Strategy

### Unit Tests
- Manifest serialization/deserialization
- Rename detection from git output
- Impact classification for various scenarios
- Path patching correctness

### Integration Tests
- End-to-end: rename file → verify paths updated
- End-to-end: modify file → verify component updated
- End-to-end: cross-component change → verify relations updated

### Performance Tests
- Measure time for rename-only update (target: <1s)
- Measure time for single-component update (target: <30s)
- Measure time for multi-component update (target: <60s)
