"""One reader per build manifest: the directory it builds, and the names it declares for it.

A manifest declares a unit when it builds something runnable. A Maven aggregator (`packaging=pom`)
and a Cargo workspace root build nothing themselves and declare nothing; an npm package is a unit
when a workspace lists it or it is the repository's own package, because every other `package.json`
in a tree is a dependency of one of those.
"""

from __future__ import annotations

import os
import re
import tomllib
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field

from static_analyzer.wiring.images import ImageBuild, image_ref
from static_analyzer.wiring.scan import FileKind, Scan, listing, mapping, parent_dir, repo_path
from static_analyzer.wiring_results import DiagnosticCode, UnitKind

_ASSEMBLY_NAME = re.compile(r"<AssemblyName>\s*([^<$]+?)\s*</AssemblyName>")
_SETUP_NAME = re.compile(r"""\bname\s*=\s*["']([^"']+)["']""")
_GO_MODULE = re.compile(r"(?m)^module\s+(\S+)")
_GEMSPEC_NAME = re.compile(r"""\.name\s*=\s*["']([^"']+)["']""")
_MIX_APP = re.compile(r"app:\s*:(\w+)")
_GRADLE_ROOT_NAME = re.compile(r"""rootProject\.name\s*=\s*["']([^"']+)["']""")
_PROPERTY = re.compile(r"\$\{([^{}]+)\}")
_TAG_FLAGS = ("-t", "--tag")

#: Which manifest speaks for a directory when several sit in it, strongest build system first.
_MANIFEST_ORDER: tuple[FileKind, ...] = (
    FileKind.DOTNET_PROJECT,
    FileKind.MAVEN,
    FileKind.GRADLE,
    FileKind.GO_MODULE,
    FileKind.CARGO,
    FileKind.PYTHON_PROJECT,
    FileKind.NPM,
    FileKind.COMPOSER,
    FileKind.GEMSPEC,
    FileKind.GEMFILE,
    FileKind.MIX,
    FileKind.REQUIREMENTS,
    FileKind.DOCKERFILE,
)

_SIMPLE_KINDS: dict[FileKind, UnitKind] = {
    FileKind.GO_MODULE: UnitKind.GO,
    FileKind.CARGO: UnitKind.RUST,
    FileKind.COMPOSER: UnitKind.PHP,
    FileKind.GEMFILE: UnitKind.RUBY,
    FileKind.GEMSPEC: UnitKind.RUBY,
    FileKind.MIX: UnitKind.ELIXIR,
}


@dataclass(frozen=True)
class Declaration:
    """What one file says about a directory: that it is a unit, what it answers to, what builds it."""

    directory: str
    kind: UnitKind
    manifest: str = ""
    aliases: tuple[str, ...] = ()
    builds: tuple[str, ...] = ()
    variant: tuple[str, ...] = ()


@dataclass
class Reading:
    """What a reader found: the units it declares and the images it says this repository builds."""

    declarations: list[Declaration] = field(default_factory=list)
    images: list[ImageBuild] = field(default_factory=list)


def build_manifests(scan: Scan) -> Reading:
    """Every unit this repository's own build files declare, and the images those files tag."""
    reading = Reading()
    for path in scan.paths_of(FileKind.DOTNET_PROJECT):
        reading.declarations.append(dotnet_project(scan, path))
    _read_maven(scan, reading)
    _read_gradle(scan, reading)
    _read_npm(scan, reading)
    _read_python(scan, reading)
    for kind, unit_kind in _SIMPLE_KINDS.items():
        for path in scan.paths_of(kind):
            declaration = simple_manifest(scan, path, unit_kind)
            if declaration is not None:
                reading.declarations.append(declaration)
    return reading


def manifest_in(scan: Scan, directory: str) -> Declaration | None:
    """The manifest that speaks for a directory a deployment file named, or None when it has none.

    Why: a directory a compose file builds is a unit whether or not a workspace lists its
    `package.json`, and the manifest sitting in it still says what the thing is called.
    """
    for kind in _MANIFEST_ORDER:
        for name in scan.names_in(directory):
            path = f"{directory}/{name}" if directory else name
            found = scan.files.get(path)
            if found is None or found.kind is not kind:
                continue
            declaration = _declaration_of(scan, path, kind)
            if declaration is not None:
                return declaration
    return None


def dotnet_project(scan: Scan, path: str) -> Declaration:
    """A .NET project: its file name is the project name, and `AssemblyName` renames the output."""
    aliases = [os.path.basename(path).rsplit(".", 1)[0]]
    aliases += [name for name in _ASSEMBLY_NAME.findall(scan.text(path))]
    return Declaration(directory=parent_dir(path), kind=UnitKind.CSPROJ, manifest=path, aliases=_names(aliases))


def project_references(scan: Scan, path: str) -> tuple[str, ...]:
    """The projects a .NET project references, as repository-relative paths."""
    root = _xml(scan, path)
    if root is None:
        return ()
    found = []
    for element in root.iter():
        if _local(element.tag) != "ProjectReference":
            continue
        include = (element.get("Include") or "").replace("\\", "/")
        target = repo_path(parent_dir(path), include)
        if target:
            found.append(target)
    return tuple(dict.fromkeys(found))


def simple_manifest(scan: Scan, path: str, kind: UnitKind) -> Declaration | None:
    """A manifest whose only unit fact is that its directory is one, plus the name it declares."""
    name = ""
    if kind is UnitKind.GO:
        module = _GO_MODULE.search(scan.text(path))
        name = module.group(1) if module else ""
    elif kind is UnitKind.RUST:
        package = _toml(scan, path).get("package")
        if not isinstance(package, dict):
            return None
        name = str(package.get("name") or "")
    elif kind is UnitKind.PHP:
        name = str(scan.json_object(path).get("name") or "")
    elif kind is UnitKind.RUBY and path.endswith(".gemspec"):
        found = _GEMSPEC_NAME.search(scan.text(path))
        name = found.group(1) if found else ""
    elif kind is UnitKind.ELIXIR:
        found = _MIX_APP.search(scan.text(path))
        name = found.group(1) if found else ""
    return Declaration(directory=parent_dir(path), kind=kind, manifest=path, aliases=_names([name]))


def _declaration_of(scan: Scan, path: str, kind: FileKind) -> Declaration | None:
    if kind is FileKind.DOTNET_PROJECT:
        return dotnet_project(scan, path)
    if kind is FileKind.MAVEN:
        pom = _Pom.read(scan, path)
        return None if pom is None or pom.packaging == "pom" else pom.declaration(scan)
    if kind is FileKind.GRADLE:
        return _gradle_declaration(scan, path)
    if kind is FileKind.NPM:
        return _npm_declaration(scan, path)
    if kind is FileKind.PYTHON_PROJECT:
        return _python_declaration(scan, path)
    if kind is FileKind.REQUIREMENTS:
        return Declaration(directory=parent_dir(path), kind=UnitKind.PYTHON, manifest=path)
    if kind is FileKind.DOCKERFILE:
        return Declaration(directory=parent_dir(path), kind=UnitKind.DOCKER, manifest=path)
    unit_kind = _SIMPLE_KINDS.get(kind)
    return None if unit_kind is None else simple_manifest(scan, path, unit_kind)


# -- Maven ---------------------------------------------------------------------------------------


@dataclass
class _Pom:
    path: str
    directory: str
    artifact_id: str
    packaging: str
    properties: dict[str, str]
    modules: tuple[str, ...]
    parent: str
    templates: tuple[str, ...]

    @classmethod
    def read(cls, scan: Scan, path: str) -> _Pom | None:
        root = _xml(scan, path)
        if root is None:
            return None
        directory = parent_dir(path)
        properties = {}
        for element in _children(root, "properties"):
            properties[_local(element.tag)] = (element.text or "").strip()
        modules = tuple(
            found
            for element in _children(root, "modules")
            if (found := repo_path(directory, (element.text or "").strip()))
        )
        parent = ""
        for element in _children(root, "parent"):
            if _local(element.tag) == "relativePath" and (element.text or "").strip():
                parent = repo_path(directory, (element.text or "").strip())
        return cls(
            path=path,
            directory=directory,
            artifact_id=_child_text(root, "artifactId"),
            packaging=_child_text(root, "packaging") or "jar",
            properties=properties,
            modules=modules,
            parent=parent,
            templates=_image_templates(root),
        )

    def declaration(self, scan: Scan) -> Declaration:
        aliases = [self.artifact_id, *_spring_application_names(scan, self.directory)]
        return Declaration(directory=self.directory, kind=UnitKind.MAVEN, manifest=self.path, aliases=_names(aliases))


def _read_maven(scan: Scan, reading: Reading) -> None:
    poms: dict[str, _Pom] = {}
    for path in scan.paths_of(FileKind.MAVEN):
        pom = _Pom.read(scan, path)
        if pom is not None:
            poms[pom.directory] = pom
    parents = {pom.directory: _parent_of(pom, poms) for pom in poms.values()}
    for pom in sorted(poms.values(), key=lambda p: p.path):
        if pom.packaging != "pom":
            reading.declarations.append(pom.declaration(scan))
        for template in pom.templates:
            reading.images += _maven_images(pom, template, poms, parents)


def _parent_of(pom: _Pom, poms: dict[str, _Pom]) -> str:
    """The pom a pom inherits from: the one its `relativePath` names, else the one above it."""
    if pom.parent and parent_dir(pom.parent) in poms:
        return parent_dir(pom.parent)
    above = parent_dir(pom.directory)
    while above and above not in poms:
        if above == parent_dir(above):
            return ""
        above = parent_dir(above)
    return above if above in poms and above != pom.directory else ""


def _effective_properties(directory: str, poms: dict[str, _Pom], parents: dict[str, str]) -> dict[str, str]:
    chain = []
    current = directory
    seen: set[str] = set()
    while current in poms and current not in seen:
        seen.add(current)
        chain.append(poms[current])
        current = parents.get(current, "")
    properties: dict[str, str] = {}
    for pom in reversed(chain):
        properties.update(pom.properties)
    own = poms[directory]
    properties.update({"project.artifactId": own.artifact_id, "project.name": own.artifact_id})
    return properties


def _maven_images(pom: _Pom, template: str, poms: dict[str, _Pom], parents: dict[str, str]) -> list[ImageBuild]:
    """The image a pom's plugin tags, resolved per module when the template names the project.

    A parent declares the plugin once and every module inherits it, so `${project.artifactId}` in a
    parent is one image per module; a template with no project property is the pom's own image.
    """
    targets = _descendants(pom.directory, poms) if "${project." in template else [pom.directory]
    builds = []
    for directory in targets:
        if directory not in poms or poms[directory].packaging == "pom":
            continue
        resolved = _resolve(template, _effective_properties(directory, poms, parents))
        ref = image_ref(resolved)
        if ref.repository:
            builds.append(ImageBuild(ref=ref, directory=directory, declared_by=pom.path))
    return builds


def _descendants(directory: str, poms: dict[str, _Pom]) -> list[str]:
    found: list[str] = []
    queue = [directory]
    while queue:
        current = queue.pop()
        if current not in poms or current in found:
            continue
        found.append(current)
        queue += list(poms[current].modules)
    return found


def _image_templates(root: ElementTree.Element) -> tuple[str, ...]:
    """Every image name a build plugin tags: jib's `<to><image>`, `<image><name>`, `docker build -t`."""
    templates: list[str] = []
    arguments: list[str] = []

    def walk(element: ElementTree.Element, parent: str) -> None:
        tag = _local(element.tag)
        text = (element.text or "").strip()
        if text:
            if (tag == "image" and parent == "to") or (tag == "name" and parent == "image") or tag == "repository":
                templates.append(text)
            if tag == "argument":
                arguments.append(text)
        for child in element:
            walk(child, tag)

    walk(root, "")
    templates += [argument for before, argument in zip(arguments, arguments[1:]) if before in _TAG_FLAGS]
    return tuple(dict.fromkeys(templates))


def _resolve(template: str, properties: dict[str, str]) -> str:
    for _ in range(3):
        replaced = _PROPERTY.sub(lambda match: properties.get(match.group(1), match.group(0)), template)
        if replaced == template:
            break
        template = replaced
    return "" if "${" in template else template


def _spring_application_names(scan: Scan, directory: str) -> list[str]:
    """`spring.application.name` as a Spring Boot application's own resources declare it."""
    resources = f"{directory}/src/main/resources" if directory else "src/main/resources"
    names = []
    for name in scan.names_in(resources):
        path = f"{resources}/{name}"
        found = scan.files.get(path)
        if found is None or found.kind is not FileKind.SPRING_CONFIG:
            continue
        if path.endswith(".properties"):
            for line in scan.text(path).splitlines():
                key, separator, value = line.partition("=")
                if separator and key.strip() == "spring.application.name":
                    names.append(value.strip())
            continue
        for document in scan.documents(path):
            application = ((document.get("spring") or {}).get("application") or {}) if document.get("spring") else {}
            if isinstance(application, dict) and isinstance(application.get("name"), str):
                names.append(application["name"])
    return [name for name in names if "${" not in name]


# -- Gradle, npm, Python -------------------------------------------------------------------------


def _read_gradle(scan: Scan, reading: Reading) -> None:
    directories = {parent_dir(path) for path in scan.paths_of(FileKind.GRADLE)}
    for directory in sorted(directories):
        declaration = _gradle_declaration(scan, f"{directory}/build.gradle" if directory else "build.gradle")
        if declaration is not None:
            reading.declarations.append(declaration)


def _gradle_declaration(scan: Scan, path: str) -> Declaration | None:
    """A Gradle project. Its name is `rootProject.name` where settings give one, else its directory."""
    directory = parent_dir(path)
    manifest = ""
    for name in ("build.gradle", "build.gradle.kts"):
        candidate = f"{directory}/{name}" if directory else name
        if candidate in scan.files:
            manifest = candidate
            break
    if not manifest:
        return None
    declared = ""
    for name in ("settings.gradle", "settings.gradle.kts"):
        settings = f"{directory}/{name}" if directory else name
        found = _GRADLE_ROOT_NAME.search(scan.text(settings)) if settings in scan.files else None
        declared = found.group(1) if found else declared
    alias = declared or os.path.basename(directory)
    return Declaration(directory=directory, kind=UnitKind.GRADLE, manifest=manifest, aliases=_names([alias]))


def _read_npm(scan: Scan, reading: Reading) -> None:
    members: set[str] = set()
    roots: set[str] = set()
    for path in scan.paths_of(FileKind.NPM):
        patterns = _workspace_patterns(scan.json_object(path).get("workspaces"))
        if patterns:
            roots.add(parent_dir(path))
            members |= _workspace_members(scan, parent_dir(path), patterns)
    for path in scan.paths_of(FileKind.PNPM_WORKSPACE):
        for document in scan.documents(path):
            patterns = [entry for entry in listing(document.get("packages")) if isinstance(entry, str)]
            if patterns:
                roots.add(parent_dir(path))
                members |= _workspace_members(scan, parent_dir(path), patterns)
    for path in scan.paths_of(FileKind.LERNA):
        patterns = [entry for entry in listing(scan.json_object(path).get("packages")) if isinstance(entry, str)]
        if patterns:
            roots.add(parent_dir(path))
            members |= _workspace_members(scan, parent_dir(path), patterns)
    for directory in sorted(members | roots | {""}):
        path = f"{directory}/package.json" if directory else "package.json"
        declaration = _npm_declaration(scan, path)
        if declaration is not None and declaration.aliases:
            reading.declarations.append(declaration)


def _npm_declaration(scan: Scan, path: str) -> Declaration | None:
    if path not in scan.files:
        return None
    name = scan.json_object(path).get("name")
    return Declaration(
        directory=parent_dir(path),
        kind=UnitKind.NPM,
        manifest=path,
        aliases=_names([name if isinstance(name, str) else ""]),
    )


def _workspace_patterns(declared: object) -> list[str]:
    if isinstance(declared, dict):
        declared = declared.get("packages")
    return [entry for entry in listing(declared) if isinstance(entry, str)]


def _workspace_members(scan: Scan, base: str, patterns: list[str]) -> set[str]:
    """The directories a workspace's globs name, out of the directories that hold a `package.json`.

    Why not a gitignore matcher: `packages/*` there also matches everything under a match, so a
    package nested inside a member would join the workspace. A workspace glob is a glob.
    """
    wanted = [_glob(pattern) for pattern in patterns if not pattern.startswith("!")]
    unwanted = [_glob(pattern[1:]) for pattern in patterns if pattern.startswith("!")]
    members = set()
    for directory in {parent_dir(path) for path in scan.paths_of(FileKind.NPM)}:
        inside = (
            directory[len(base) + 1 :] if base and directory.startswith(base + "/") else directory if not base else ""
        )
        if inside and any(pattern.fullmatch(inside) for pattern in wanted):
            if not any(pattern.fullmatch(inside) for pattern in unwanted):
                members.add(directory)
    return members


def _glob(pattern: str) -> re.Pattern[str]:
    """A workspace glob as a matcher: `*` stops at a directory boundary, `**` crosses them."""
    translated, index, text = [], 0, pattern.strip().rstrip("/")
    while index < len(text):
        if text.startswith("**", index):
            translated.append(".*")
            index += 2
        elif text[index] == "*":
            translated.append("[^/]*")
            index += 1
        elif text[index] == "?":
            translated.append("[^/]")
            index += 1
        else:
            translated.append(re.escape(text[index]))
            index += 1
    return re.compile("".join(translated))


def _read_python(scan: Scan, reading: Reading) -> None:
    for path in scan.paths_of(FileKind.PYTHON_PROJECT):
        declaration = _python_declaration(scan, path)
        if declaration is not None:
            reading.declarations.append(declaration)
    declared = {parent_dir(path) for path in scan.paths_of(FileKind.PYTHON_PROJECT)}
    for path in scan.paths_of(FileKind.REQUIREMENTS):
        directory = parent_dir(path)
        if directory in declared:
            continue
        # A requirements file is a unit's when code sits anywhere below it (a `src/` layout) or a
        # Dockerfile beside it installs it; one at the root of a docs tree declares nothing.
        beside = {scan.files[f"{directory}/{n}" if directory else n].kind for n in scan.names_in(directory)
                  if (f"{directory}/{n}" if directory else n) in scan.files}  # fmt: skip
        if FileKind.DOCKERFILE in beside or any(path.endswith(".py") for path in scan.paths_below(directory)):
            reading.declarations.append(Declaration(directory=directory, kind=UnitKind.PYTHON, manifest=path))


def _python_declaration(scan: Scan, path: str) -> Declaration | None:
    """A Python distribution: `[project].name`, Poetry's name, or a `setup(name=...)` literal."""
    if path.endswith("setup.py"):
        text = scan.text(path)
        call = text.find("setup(")
        found = _SETUP_NAME.search(text, call) if call >= 0 else None
        return Declaration(
            directory=parent_dir(path),
            kind=UnitKind.PYTHON,
            manifest=path,
            aliases=_names([found.group(1) if found else ""]),
        )
    data = _toml(scan, path)
    tables = [mapping(data.get("project")), mapping(mapping(data.get("tool")).get("poetry"))]
    declared = [table["name"] for table in tables if isinstance(table.get("name"), str)]
    if not declared:
        return None
    name = declared[0]
    return Declaration(directory=parent_dir(path), kind=UnitKind.PYTHON, manifest=path, aliases=_names([name]))


# -- shared --------------------------------------------------------------------------------------


def _names(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip() for value in values if value and value.strip()))


def _xml(scan: Scan, path: str) -> ElementTree.Element | None:
    text = scan.text(path)
    if not text:
        return None
    try:
        return ElementTree.fromstring(text)
    except ElementTree.ParseError as error:
        scan.diagnose(DiagnosticCode.UNREADABLE_MANIFEST, f"{path} is not XML: {error.msg}", path)
        return None


def _toml(scan: Scan, path: str) -> dict:
    text = scan.text(path)
    if not text:
        return {}
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        scan.diagnose(DiagnosticCode.UNREADABLE_MANIFEST, f"{path} is not TOML: {error}", path)
        return {}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(root: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return [child for element in root if _local(element.tag) == name for child in element]


def _child_text(root: ElementTree.Element, name: str) -> str:
    for element in root:
        if _local(element.tag) == name:
            return (element.text or "").strip()
    return ""
