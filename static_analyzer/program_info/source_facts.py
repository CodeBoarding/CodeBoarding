"""Immutable syntax facts extracted from parser-classified source nodes."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from tree_sitter import Node as TreeSitterNode


class Visibility(StrEnum):
    UNKNOWN = "unknown"
    PUBLIC = "public"
    PROTECTED = "protected"
    PRIVATE = "private"
    INTERNAL = "internal"
    PACKAGE = "package"


class FactKind(StrEnum):
    IMPORT = "import"
    TYPE_USE = "type_use"
    ANNOTATION = "annotation"
    DECLARATION = "declaration"


@dataclass(frozen=True, order=True)
class SourceSpan:
    start_line: int
    start_column: int
    end_line: int
    end_column: int
    start_byte: int = 0
    end_byte: int = 0

    def contains(self, other: SourceSpan) -> bool:
        return self.start_byte <= other.start_byte and self.end_byte >= other.end_byte


@dataclass(frozen=True, order=True)
class SourceEvidence:
    language: str
    file_path: str
    span: SourceSpan
    spelling: str
    node_kind: str
    confidence: float = 1.0
    provenance: str = "tree-sitter"


@dataclass(frozen=True, order=True)
class ImportedBinding:
    name: str
    alias: str = ""
    target: str = ""
    is_wildcard: bool = False


@dataclass(frozen=True, order=True)
class ImportFact:
    evidence: SourceEvidence
    path: str
    bindings: tuple[ImportedBinding, ...] = ()
    relative_level: int = 0
    is_static: bool = False
    is_side_effect: bool = False
    is_global: bool = False


@dataclass(frozen=True, order=True)
class TypeUseFact:
    evidence: SourceEvidence
    name: str


@dataclass(frozen=True, order=True)
class AnnotationFact:
    evidence: SourceEvidence
    name: str


@dataclass(frozen=True, order=True)
class DeclarationFact:
    evidence: SourceEvidence
    name: str
    visibility: Visibility = Visibility.UNKNOWN
    modifiers: tuple[str, ...] = ()
    annotations: tuple[AnnotationFact, ...] = ()


@dataclass(frozen=True, order=True)
class ExtractionDiagnostic:
    file_path: str
    code: str
    message: str
    span: SourceSpan = SourceSpan(0, 0, 0, 0)


@dataclass(frozen=True)
class FileSourceFacts:
    language: str
    file_path: str
    imports: tuple[ImportFact, ...] = ()
    type_uses: tuple[TypeUseFact, ...] = ()
    annotations: tuple[AnnotationFact, ...] = ()
    declarations: tuple[DeclarationFact, ...] = ()
    diagnostics: tuple[ExtractionDiagnostic, ...] = ()
    supported: bool = True

    def __post_init__(self) -> None:
        for name in ("imports", "type_uses", "annotations", "declarations", "diagnostics"):
            values = getattr(self, name)
            if values != tuple(sorted(values)):
                raise ValueError(f"{name} must be deterministically ordered")


@dataclass(frozen=True, order=True)
class ResolutionDiagnostic:
    file_path: str
    code: str
    spelling: str
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedSourceFacts:
    type_edges: tuple[tuple[str, str], ...] = ()
    import_edges: tuple[tuple[str, str], ...] = ()
    diagnostics: tuple[ResolutionDiagnostic, ...] = ()


_IMPORT_NODES = {
    "python": {"import_statement", "import_from_statement"},
    "javascript": {"import_statement", "lexical_declaration", "variable_declaration"},
    "typescript": {"import_statement", "import_alias", "lexical_declaration", "variable_declaration"},
    "go": {"import_declaration", "import_spec"},
    "java": {"import_declaration"},
    "php": {"namespace_use_declaration", "namespace_use_group"},
    "rust": {"use_declaration"},
    "csharp": {"using_directive"},
}

_TYPE_NODES = {
    "python": {"type", "generic_type", "union_type", "type_parameter"},
    "javascript": set(),
    "typescript": {
        "predefined_type",
        "type_identifier",
        "generic_type",
        "nested_type_identifier",
        "union_type",
        "intersection_type",
        "array_type",
        "tuple_type",
        "type_query",
        "literal_type",
    },
    "go": {"type_identifier", "qualified_type", "pointer_type", "slice_type", "array_type", "map_type", "channel_type"},
    "java": {
        "type_identifier",
        "generic_type",
        "scoped_type_identifier",
        "integral_type",
        "floating_point_type",
        "void_type",
    },
    "php": {"named_type", "primitive_type", "union_type", "intersection_type", "optional_type"},
    "rust": {
        "type_identifier",
        "scoped_type_identifier",
        "generic_type",
        "primitive_type",
        "reference_type",
        "pointer_type",
        "tuple_type",
    },
    "csharp": {
        "identifier",
        "generic_name",
        "qualified_name",
        "predefined_type",
        "nullable_type",
        "array_type",
        "tuple_type",
    },
}

_ANNOTATION_NODES = {
    "python": {"decorator"},
    "javascript": {"decorator"},
    "typescript": {"decorator"},
    "go": set(),
    "java": {"marker_annotation", "annotation"},
    "php": {"attribute_group", "attribute"},
    "rust": {"attribute_item", "inner_attribute_item"},
    "csharp": {"attribute_list", "attribute"},
}

_DECLARATION_SUFFIXES = ("declaration", "definition")
_DECLARATION_EXTRAS = {
    "function_item",
    "impl_item",
    "trait_item",
    "struct_item",
    "enum_item",
    "method_declaration",
    "constructor_declaration",
    "class_definition",
    "function_definition",
}
_MODIFIERS = frozenset(
    {
        "public",
        "protected",
        "private",
        "internal",
        "static",
        "global",
        "abstract",
        "async",
        "const",
        "readonly",
        "sealed",
        "final",
        "native",
        "synchronized",
        "transient",
        "volatile",
        "extern",
        "unsafe",
        "virtual",
        "override",
        "partial",
        "new",
        "export",
        "default",
        "declare",
        "pub",
        "mut",
        "ref",
        "open",
    }
)
_PRIMITIVES = frozenset(
    {
        "str",
        "int",
        "float",
        "bool",
        "bytes",
        "none",
        "any",
        "number",
        "string",
        "boolean",
        "void",
        "unknown",
        "object",
        "never",
        "char",
        "byte",
        "short",
        "long",
        "double",
        "decimal",
        "uint",
        "ulong",
        "usize",
        "isize",
        "u8",
        "u16",
        "u32",
        "u64",
        "i8",
        "i16",
        "i32",
        "i64",
        "f32",
        "f64",
    }
)
_NAME_RE = re.compile(r"[A-Za-z_$][\w$]*(?:(?:::|\\|\.)[A-Za-z_$][\w$]*)*")


def extract_source_facts(language: str, file_path: Path, content: bytes, root: TreeSitterNode) -> FileSourceFacts:
    """Extract facts only from AST nodes classified for the selected language."""
    language = language.lower()
    path = str(file_path)
    if language not in _IMPORT_NODES:
        diagnostic = ExtractionDiagnostic(path, "unsupported-language", f"No source-fact parser for {language}")
        return FileSourceFacts(language, path, diagnostics=(diagnostic,), supported=False)
    imports: list[ImportFact] = []
    types: list[TypeUseFact] = []
    annotations: list[AnnotationFact] = []
    declarations: list[DeclarationFact] = []
    diagnostics: list[ExtractionDiagnostic] = []
    nodes = list(_walk(root))
    for node in nodes:
        if node.type not in _IMPORT_NODES[language] or _has_ancestor(node, _IMPORT_NODES[language]):
            continue
        text = _text(node, content)
        parsed = _parse_import(language, path, node, text)
        if parsed:
            imports.extend(parsed)
        elif _looks_like_import(language, text):
            diagnostics.append(ExtractionDiagnostic(path, "unparsed-import", text, _span(node)))
    for node in nodes:
        if node.type != "ERROR" or _has_ancestor(node, {"ERROR"} | _IMPORT_NODES[language]):
            continue
        text = _text(node, content).strip()
        if _looks_like_import(language, text):
            diagnostics.append(ExtractionDiagnostic(path, "unparsed-import", text, _span(node)))
    annotation_nodes: list[tuple[TreeSitterNode, AnnotationFact]] = []
    for node in nodes:
        if node.type in _ANNOTATION_NODES[language] and not _has_ancestor(node, _ANNOTATION_NODES[language]):
            spelling = _text(node, content).strip()
            name = _annotation_name(spelling)
            fact = AnnotationFact(_evidence(language, path, node, spelling), name)
            annotations.append(fact)
            annotation_nodes.append((node, fact))
    declaration_nodes = [node for node in nodes if _is_declaration(node) and node.child_by_field_name("name")]
    annotations_by_declaration: dict[tuple[int, int], list[AnnotationFact]] = {}
    for annotation_node, annotation in annotation_nodes:
        owner = _annotation_declaration(annotation_node, declaration_nodes)
        if owner is not None:
            annotations_by_declaration.setdefault((owner.start_byte, owner.end_byte), []).append(annotation)
    for node in nodes:
        if _is_type_node(language, node) and not _inside_import_or_annotation(language, node):
            types.extend(_type_facts(language, path, node, content))
        if _is_declaration(node):
            declaration = _declaration_fact(language, path, node, content, annotations_by_declaration)
            if declaration is not None:
                declarations.append(declaration)
    return FileSourceFacts(
        language,
        path,
        tuple(sorted(set(imports))),
        tuple(sorted(set(types))),
        tuple(sorted(set(annotations))),
        tuple(sorted(set(declarations))),
        tuple(sorted(set(diagnostics))),
    )


def enrich_and_resolve_source_facts(
    facts: tuple[FileSourceFacts, ...], symbols: Mapping[str, object]
) -> ResolvedSourceFacts:
    """Attach facts to narrow symbols and resolve only unique structural targets."""
    primary = list({getattr(symbol, "qualified_name"): symbol for symbol in symbols.values()}.values())
    by_file: dict[str, list[object]] = {}
    by_simple: dict[str, list[object]] = {}
    for symbol in primary:
        by_file.setdefault(str(getattr(symbol, "file_path")), []).append(symbol)
        by_simple.setdefault(getattr(symbol, "name"), []).append(symbol)
    type_edges: set[tuple[str, str]] = set()
    import_edges: set[tuple[str, str]] = set()
    diagnostics: list[ResolutionDiagnostic] = []
    aliases: dict[tuple[str, str], str] = {}
    for file_facts in facts:
        file_symbols = by_file.get(file_facts.file_path, [])
        for declaration in file_facts.declarations:
            owner = _owner(file_symbols, declaration.evidence.span, allow_preceding=True)
            if owner is None:
                continue
            setattr(owner, "visibility", declaration.visibility.value)
            setattr(owner, "modifiers", declaration.modifiers)
            setattr(owner, "annotations", declaration.annotations)
        for imported in file_facts.imports:
            owner = _owner(file_symbols, imported.evidence.span)
            if owner is not None:
                current = tuple(getattr(owner, "import_evidence", ()))
                setattr(owner, "import_evidence", tuple(sorted(set(current + (imported,)))))
            for binding in imported.bindings:
                local = binding.alias or binding.name
                aliases[(file_facts.file_path, local)] = binding.target or _join_target(imported.path, binding.name)
                target, candidates = _resolve_target(
                    binding.target or _join_target(imported.path, binding.name), primary, by_simple
                )
                if owner is not None and target is not None:
                    import_edges.add((getattr(owner, "qualified_name"), getattr(target, "qualified_name")))
                elif target is None:
                    diagnostics.append(
                        ResolutionDiagnostic(
                            file_facts.file_path,
                            "unresolved-import" if not candidates else "ambiguous-import",
                            binding.name,
                            candidates,
                        )
                    )
        for type_use in file_facts.type_uses:
            owner = _owner(file_symbols, type_use.evidence.span)
            if owner is not None:
                current = tuple(getattr(owner, "type_use_evidence", ()))
                setattr(owner, "type_use_evidence", tuple(sorted(set(current + (type_use,)))))
            alias_target = aliases.get((file_facts.file_path, type_use.name), type_use.name)
            target, candidates = _resolve_target(alias_target, primary, by_simple, owner)
            if owner is not None and target is not None and target is not owner:
                type_edges.add((getattr(owner, "qualified_name"), getattr(target, "qualified_name")))
            elif target is None:
                diagnostics.append(
                    ResolutionDiagnostic(
                        file_facts.file_path,
                        "unresolved-type" if not candidates else "ambiguous-type",
                        type_use.name,
                        candidates,
                    )
                )
    return ResolvedSourceFacts(tuple(sorted(type_edges)), tuple(sorted(import_edges)), tuple(sorted(set(diagnostics))))


def _parse_import(language: str, path: str, node: TreeSitterNode, text: str) -> list[ImportFact]:
    evidence = _evidence(language, path, node, text.strip())
    if language == "python":
        return _python_import(evidence, text)
    if language in {"javascript", "typescript"}:
        return _ecmascript_import(evidence, text)
    if language == "go":
        return _go_import(evidence, text)
    if language == "java":
        match = re.fullmatch(r"\s*import\s+(static\s+)?([\w.]+)(\.\*)?\s*;?\s*", text, re.S)
        if not match:
            return []
        target, wildcard = match.group(2), bool(match.group(3))
        name = "*" if wildcard else target.rsplit(".", 1)[-1]
        return [
            ImportFact(
                evidence,
                target.rsplit(".", 1)[0] if not wildcard and "." in target else target,
                (ImportedBinding(name, target=target, is_wildcard=wildcard),),
                is_static=bool(match.group(1)),
            )
        ]
    if language == "php":
        return _php_import(evidence, text)
    if language == "rust":
        return _rust_import(evidence, text)
    if language == "csharp":
        match = re.fullmatch(r"\s*(global\s+)?using\s+(static\s+)?(?:(\w+)\s*=\s*)?([\w.]+)\s*;\s*", text)
        if not match:
            return []
        target = match.group(4)
        return [
            ImportFact(
                evidence,
                target,
                (ImportedBinding(target.rsplit(".", 1)[-1], match.group(3) or "", target),),
                is_static=bool(match.group(2)),
                is_global=bool(match.group(1)),
            )
        ]
    return []


def _python_import(evidence: SourceEvidence, text: str) -> list[ImportFact]:
    text = text.strip()
    if text.startswith("import "):
        result = []
        for item in text[7:].split(","):
            parts = re.split(r"\s+as\s+", item.strip())
            target = parts[0].strip()
            result.append(
                ImportFact(
                    evidence,
                    target,
                    (ImportedBinding(target.split(".")[0], parts[1] if len(parts) == 2 else "", target),),
                )
            )
        return result
    match = re.fullmatch(r"from\s+(\.*)([\w.]*)\s+import\s+(.+)", text, re.S)
    if not match:
        return []
    dots, module, names = match.groups()
    bindings = []
    for item in names.strip("() \n").split(","):
        parts = re.split(r"\s+as\s+", item.strip())
        if not parts[0]:
            continue
        bindings.append(
            ImportedBinding(
                parts[0], parts[1] if len(parts) == 2 else "", _join_target(module, parts[0]), parts[0] == "*"
            )
        )
    return [ImportFact(evidence, module, tuple(bindings), len(dots))]


def _ecmascript_import(evidence: SourceEvidence, text: str) -> list[ImportFact]:
    side = re.fullmatch(r"\s*import\s*['\"]([^'\"]+)['\"]\s*;?\s*", text)
    if side:
        return [ImportFact(evidence, side.group(1), is_side_effect=True)]
    match = re.fullmatch(r"\s*import\s+(.+?)\s+from\s+['\"]([^'\"]+)['\"]\s*;?\s*", text, re.S)
    if match:
        clause, module = match.groups()
        bindings: list[ImportedBinding] = []
        default = clause.split(",", 1)[0].strip()
        if default and not default.startswith(("{", "*")):
            bindings.append(ImportedBinding("default", default, module))
        namespace = re.search(r"\*\s+as\s+(\w+)", clause)
        if namespace:
            bindings.append(ImportedBinding("*", namespace.group(1), module, True))
        named = re.search(r"\{(.*?)\}", clause, re.S)
        if named:
            for item in named.group(1).split(","):
                parts = re.split(r"\s+as\s+", item.strip())
                if parts[0]:
                    bindings.append(
                        ImportedBinding(parts[0], parts[1] if len(parts) == 2 else "", _join_target(module, parts[0]))
                    )
        return [ImportFact(evidence, module, tuple(bindings))]
    require = re.fullmatch(
        r"\s*(?:const|let|var)\s+([\w${}, ]+)\s*=\s*require\(\s*['\"]([^'\"]+)['\"]\s*\)\s*;?\s*", text
    )
    if require:
        lhs, module = require.groups()
        names = [name for name in _NAME_RE.findall(lhs) if name not in {"const", "let", "var"}]
        return [
            ImportFact(
                evidence, module, tuple(ImportedBinding(name, target=_join_target(module, name)) for name in names)
            )
        ]
    return []


def _go_import(evidence: SourceEvidence, text: str) -> list[ImportFact]:
    result = []
    for alias, module in re.findall(r"(?m)(?:(\w+|[._])\s+)?\"([^\"]+)\"", text):
        name = module.rsplit("/", 1)[-1]
        result.append(ImportFact(evidence, module, (ImportedBinding(name, alias, module, alias == "."),)))
    return result


def _php_import(evidence: SourceEvidence, text: str) -> list[ImportFact]:
    body = re.sub(r"^\s*use\s+|;\s*$", "", text.strip())
    group = re.fullmatch(r"([^{}]+)\\\{(.+)\}", body, re.S)
    prefix, entries = (group.group(1), group.group(2)) if group else ("", body)
    bindings = []
    for entry in entries.split(","):
        parts = re.split(r"\s+as\s+", entry.strip(), flags=re.I)
        target = (prefix.rstrip("\\") + "\\" if prefix else "") + parts[0]
        bindings.append(ImportedBinding(parts[0].rsplit("\\", 1)[-1], parts[1] if len(parts) == 2 else "", target))
    return [ImportFact(evidence, prefix.rstrip("\\"), tuple(bindings))] if bindings else []


def _rust_import(evidence: SourceEvidence, text: str) -> list[ImportFact]:
    body = re.sub(r"^\s*(?:pub\s+)?use\s+|;\s*$", "", text.strip())
    bindings: list[ImportedBinding] = []

    def visit(value: str, prefix: str = "") -> None:
        group = re.fullmatch(r"(.*?)\{(.*)\}", value.strip(), re.S)
        if group:
            base = _join_rust(prefix, group.group(1).rstrip(":"))
            for item in _split_group(group.group(2)):
                visit(item, base)
            return
        parts = re.split(r"\s+as\s+", value.strip())
        name = parts[0]
        target = _join_rust(prefix, name)
        simple = prefix.rsplit("::", 1)[-1] if name == "self" else name.rsplit("::", 1)[-1]
        bindings.append(ImportedBinding(simple, parts[1] if len(parts) == 2 else "", target, name == "*"))

    visit(body)
    path = body.split("::{", 1)[0].rstrip(":")
    return [ImportFact(evidence, path, tuple(bindings))] if bindings else []


def _type_facts(language: str, path: str, node: TreeSitterNode, content: bytes) -> list[TypeUseFact]:
    if any(parent.type in _TYPE_NODES[language] for parent in _parents(node)):
        return []
    spelling = _text(node, content).strip()
    if language == "python" and _NAME_RE.fullmatch(spelling) and spelling not in _PRIMITIVES:
        return [TypeUseFact(_evidence(language, path, node, spelling), spelling)]
    result = []
    for candidate in _walk(node):
        if candidate.type not in {
            "type_identifier",
            "identifier",
            "name",
            "qualified_type",
            "scoped_type_identifier",
            "generic_name",
            "nested_type_identifier",
            "qualified_name",
        }:
            continue
        if _is_name_field(candidate):
            continue
        spelling = _text(candidate, content).strip()
        if spelling in _PRIMITIVES or not _NAME_RE.fullmatch(spelling.replace("::", ".").replace("\\", ".")):
            continue
        result.append(TypeUseFact(_evidence(language, path, candidate, spelling), spelling))
    if not result:
        if not _is_name_field(node) and _NAME_RE.fullmatch(spelling) and spelling not in _PRIMITIVES:
            result.append(TypeUseFact(_evidence(language, path, node, spelling), spelling))
    return result


def _declaration_fact(
    language: str,
    path: str,
    node: TreeSitterNode,
    content: bytes,
    annotations: dict[tuple[int, int], list[AnnotationFact]],
) -> DeclarationFact | None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return None
    name = _text(name_node, content).strip()
    raw = _text(node, content)[: max(0, name_node.end_byte - node.start_byte)]
    if language in {"javascript", "typescript"} and node.type in {"class_declaration", "abstract_class_declaration"}:
        for parent in _parents(node):
            parent_prefix = _text(parent, content)[: max(0, name_node.end_byte - parent.start_byte)]
            if re.search(r"(?:^|\s)export\s+class\b", parent_prefix):
                raw = parent_prefix
                break
    for annotation in annotations.get((node.start_byte, node.end_byte), []):
        raw = raw.replace(annotation.evidence.spelling, "", 1)
    words = [word.lower() for word in _NAME_RE.findall(raw)]
    modifiers = tuple(sorted({word for word in words if word in _MODIFIERS}))
    visibility = Visibility.UNKNOWN
    for value in (Visibility.PRIVATE, Visibility.PROTECTED, Visibility.INTERNAL, Visibility.PUBLIC):
        if value.value in modifiers or (value == Visibility.PUBLIC and "pub" in modifiers):
            visibility = value
            break
    if language == "java" and visibility == Visibility.UNKNOWN:
        visibility = Visibility.PACKAGE
    attached = annotations.get((node.start_byte, node.end_byte), [])
    return DeclarationFact(
        _evidence(language, path, node, name), name, visibility, modifiers, tuple(sorted(set(attached)))
    )


def _resolve_target(
    name: str, symbols: list[object], by_simple: dict[str, list[object]], owner: object | None = None
) -> tuple[object | None, tuple[str, ...]]:
    normalized = name.replace("::", ".").replace("\\", ".").strip(".")
    exact = [symbol for symbol in symbols if getattr(symbol, "qualified_name") == normalized]
    if len(exact) == 1:
        return exact[0], ()
    suffix = [symbol for symbol in symbols if getattr(symbol, "qualified_name").endswith("." + normalized)]
    if len(suffix) == 1:
        return suffix[0], ()
    if owner is not None:
        owner_name = getattr(owner, "qualified_name")
        scoped = [
            symbol
            for symbol in symbols
            if owner_name.rsplit(".", 1)[0] + "." + normalized == getattr(symbol, "qualified_name")
        ]
        if len(scoped) == 1:
            return scoped[0], ()
    candidates = by_simple.get(normalized.rsplit(".", 1)[-1], [])
    if len(candidates) == 1:
        return candidates[0], ()
    return None, tuple(sorted(getattr(candidate, "qualified_name") for candidate in candidates or suffix or exact))


def _owner(symbols: list[object], span: SourceSpan, allow_preceding: bool = False) -> object | None:
    candidates = []
    for symbol in symbols:
        start = getattr(symbol, "start_line")
        end = getattr(symbol, "end_line")
        contains = start <= span.start_line <= end
        precedes = allow_preceding and 0 <= start - span.end_line <= 4
        if contains or precedes:
            candidates.append(symbol)
    return (
        min(
            candidates,
            key=lambda symbol: (
                getattr(symbol, "end_line") - getattr(symbol, "start_line"),
                -len(getattr(symbol, "qualified_name")),
            ),
        )
        if candidates
        else None
    )


def _is_type_node(language: str, node: TreeSitterNode) -> bool:
    if node.type not in _TYPE_NODES[language]:
        return False
    if node.parent is not None and node.parent.child_by_field_name("name") == node and _is_declaration(node.parent):
        return False
    if language == "csharp" and node.type == "identifier":
        return node.parent is not None and node.parent.child_by_field_name("type") == node
    return True


def _is_name_field(node: TreeSitterNode) -> bool:
    """Return whether a candidate identifies a declaration or value rather than a type."""
    parent = node.parent
    return parent is not None and parent.child_by_field_name("name") == node


def _annotation_declaration(
    annotation: TreeSitterNode, declarations: list[TreeSitterNode]
) -> TreeSitterNode | None:
    """Assign an annotation to the nearest declaration container, never a nested child."""
    ancestors = list(_parents(annotation))
    containing = [declaration for declaration in declarations if declaration in ancestors]
    if containing:
        return min(containing, key=lambda item: item.end_byte - item.start_byte)
    following = [declaration for declaration in declarations if declaration.start_byte >= annotation.end_byte]
    if not following:
        return None
    nearest = min(following, key=lambda item: (item.start_byte, item.end_byte - item.start_byte))
    annotation_parent = annotation.parent
    declaration_ancestors = set(_parents(nearest))
    if annotation_parent is not None and annotation_parent not in declaration_ancestors and nearest.parent != annotation_parent:
        return None
    return nearest


def _inside_import_or_annotation(language: str, node: TreeSitterNode) -> bool:
    return any(parent.type in _IMPORT_NODES[language] | _ANNOTATION_NODES[language] for parent in _parents(node))


def _is_declaration(node: TreeSitterNode) -> bool:
    return node.type.endswith(_DECLARATION_SUFFIXES) or node.type in _DECLARATION_EXTRAS


def _annotation_name(spelling: str) -> str:
    names = _NAME_RE.findall(spelling.lstrip("#@!["))
    return names[0] if names else spelling


def _looks_like_import(language: str, text: str) -> bool:
    keyword = {"rust": "use", "csharp": "using", "php": "use"}.get(language, "import")
    return text.lstrip().startswith(keyword)


def _join_target(path: str, name: str) -> str:
    return f"{path}.{name}" if path and name != "*" else path or name


def _join_rust(path: str, name: str) -> str:
    if name == "self":
        return path
    return f"{path}::{name}" if path else name


def _split_group(text: str) -> list[str]:
    result, start, depth = [], 0, 0
    for index, char in enumerate(text):
        depth += char == "{"
        depth -= char == "}"
        if char == "," and depth == 0:
            result.append(text[start:index])
            start = index + 1
    result.append(text[start:])
    return [item.strip() for item in result if item.strip()]


def _evidence(language: str, path: str, node: TreeSitterNode, spelling: str) -> SourceEvidence:
    return SourceEvidence(language, path, _span(node), spelling, node.type, 0.7 if node.has_error else 1.0)


def _span(node: TreeSitterNode) -> SourceSpan:
    return SourceSpan(
        node.start_point.row,
        node.start_point.column,
        node.end_point.row,
        node.end_point.column,
        node.start_byte,
        node.end_byte,
    )


def _text(node: TreeSitterNode, content: bytes) -> str:
    return content[node.start_byte : node.end_byte].decode(errors="replace")


def _parents(node: TreeSitterNode):
    parent = node.parent
    while parent is not None:
        yield parent
        parent = parent.parent


def _has_ancestor(node: TreeSitterNode, kinds: set[str]) -> bool:
    return any(parent.type in kinds for parent in _parents(node))


def _walk(node: TreeSitterNode):
    yield node
    for child in node.children:
        yield from _walk(child)
