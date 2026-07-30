"""Evidence-bearing program information derived from static analysis."""

from static_analyzer.program_info.builder import build_program_information
from static_analyzer.program_info.flow import FlowFacts, analyze_flow
from static_analyzer.program_info.modules import ModuleAnalysis, ModuleFlow, ModuleProfile, analyze_modules
from static_analyzer.program_info.models import (
    Channel,
    ClusterProfile,
    EdgeEvidence,
    FlowLens,
    GraphStatistics,
    ProgramDelta,
    ProgramInformation,
    ProgramSnapshot,
    SymbolFact,
    SymbolProfile,
    SourceFactProfile,
)
from static_analyzer.program_info.source_facts import (
    AnnotationFact,
    DeclarationFact,
    ExtractionDiagnostic,
    FileSourceFacts,
    ImportFact,
    ImportedBinding,
    ResolutionDiagnostic,
    ResolvedSourceFacts,
    SourceEvidence,
    SourceSpan,
    TypeUseFact,
    Visibility,
)
from static_analyzer.program_info.topology import StrongRegion, TopologyFacts, analyze_topology

__all__ = [
    "Channel",
    "ClusterProfile",
    "EdgeEvidence",
    "FlowLens",
    "FlowFacts",
    "GraphStatistics",
    "ProgramDelta",
    "ProgramInformation",
    "ProgramSnapshot",
    "ModuleAnalysis",
    "ModuleFlow",
    "ModuleProfile",
    "StrongRegion",
    "SymbolFact",
    "SymbolProfile",
    "SourceFactProfile",
    "AnnotationFact",
    "DeclarationFact",
    "ExtractionDiagnostic",
    "FileSourceFacts",
    "ImportFact",
    "ImportedBinding",
    "ResolutionDiagnostic",
    "ResolvedSourceFacts",
    "SourceEvidence",
    "SourceSpan",
    "TypeUseFact",
    "Visibility",
    "TopologyFacts",
    "analyze_flow",
    "analyze_modules",
    "analyze_topology",
    "build_program_information",
]
