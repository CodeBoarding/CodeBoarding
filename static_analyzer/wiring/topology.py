"""What the deployment topology says a directory is: compose, Kubernetes, skaffold, Helm, Aspire, CI.

A deployment file builds nothing by itself. It either names a directory — a build context, a
skaffold artifact, an Aspire project — or names an image, which the image index resolves back to
the directory that builds it. What it adds to a unit is the names it answers to at run time: a
compose service and container name, a Kubernetes workload and the Services selecting it, an Aspire
resource name, a chart.
"""

from __future__ import annotations

import re
from typing import Any

from static_analyzer.wiring.compose import ComposeProject, ComposeService
from static_analyzer.wiring.images import (
    Dockerfile,
    ImageBuild,
    ImageIndex,
    build_directory,
    image_ref,
    read_dockerfile,
)
from static_analyzer.wiring.manifests import Declaration, Reading, project_references
from static_analyzer.wiring.scan import FileKind, Scan, listing, mapping, parent_dir, repo_path
from static_analyzer.wiring_results import DiagnosticCode, UnitKind

#: The Aspire hosting calls that name a directory of this repository, and the argument that holds it.
_ADD_PROJECT_TYPED = re.compile(r"AddProject\s*<\s*Projects\.(\w+)\s*>\s*\(\s*\"([^\"]+)\"")
_ADD_PROJECT_PATH = re.compile(r"AddProject\s*\(\s*\"([^\"]+)\"\s*,\s*\"([^\"]+)\"")
_ADD_APP = re.compile(
    r"Add(?:NpmApp|ViteApp|NodeApp|PythonApp|PythonModule|UvicornApp|JavaApp|GolangApp|Dockerfile)"
    r"\s*\(\s*\"([^\"]+)\"\s*,\s*\"([^\"]+)\""
)
#: What a project says when it is an Aspire AppHost rather than an ordinary .NET project.
ASPIRE_HOST = ("Aspire.AppHost.Sdk", "Aspire.Hosting.AppHost", "<IsAspireHost>true")
_WORKLOADS = ("Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job", "CronJob", "Pod")

#: `${{ ... }}`, and the actions whose inputs say what image a workflow builds.
_EXPRESSION = re.compile(r"\$\{\{\s*(.+?)\s*\}\}")
_BUILD_ACTIONS = ("docker/build-push-action", "depot/build-push-action", "docker/bake-action")
_METADATA_ACTION = "docker/metadata-action"
_DOCKER_BUILD = re.compile(r"\bdocker(?:\s+buildx)?\s+build\b([^\n]*)")
_BUILD_TAG = re.compile(r"(?:^|\s)(?:-t|--tag)[=\s]+(\S+)")
_BUILD_FILE = re.compile(r"(?:^|\s)(?:-f|--file)[=\s]+(\S+)")


def compose_builds(scan: Scan, projects: list[ComposeProject]) -> list[ImageBuild]:
    """What each compose service builds, so that a service running the image finds its directory."""
    builds = []
    for project in projects:
        for service in project.services:
            ref = image_ref(service.image)
            if not ref.repository or not (service.context or service.dockerfile):
                continue
            builds.append(
                ImageBuild(
                    ref=ref,
                    directory=build_directory(scan, service.context, service.dockerfile),
                    declared_by=service.files[0],
                    builds_source=builds_source(scan, service),
                )
            )
    return builds


def builds_source(scan: Scan, service: ComposeService) -> bool:
    """Whether a service's build brings code of this repository into the image.

    A Dockerfile that is not here builds nothing here (a template's, a stale path); one that copies
    nothing from its context is a toolchain or dev-container image, and one that copies only
    configuration customises the image it starts from (§6).
    """
    if not service.dockerfile or not scan.on_disk(service.dockerfile):
        return False
    return read_dockerfile(scan, service.dockerfile, service.context).copies_source


def configures_image(scan: Scan, service: ComposeService) -> Dockerfile | None:
    """The Dockerfile a service builds when it copies configuration and no code onto a stock image (§6)."""
    if not service.dockerfile or not scan.on_disk(service.dockerfile):
        return None
    read = read_dockerfile(scan, service.dockerfile, service.context)
    return read if read.copies_context and not read.copies_source else None


def compose_units(scan: Scan, projects: list[ComposeProject], index: ImageIndex, repo_name: str) -> Reading:
    """Every compose service that is a directory of this repository, with the names it answers to."""
    reading = Reading()
    for project in projects:
        declared = 0
        for service in project.services:
            configured = configures_image(scan, service)
            if configured is not None:
                # A Prometheus with its scrape list, a Grafana with its dashboards: the image it
                # starts from is what runs, and the directory holds its settings, not a unit's code.
                declared += 1
                scan.diagnose(
                    DiagnosticCode.CONFIGURED_IMAGE,
                    f"{configured.path} configures {configured.base_image or 'an image'} "
                    "and builds no code of this repository",
                    configured.path,
                )
                continue
            directory = _service_directory(scan, service, index, repo_name)
            if directory is None:
                continue
            declared += 1
            ref = image_ref(service.image)
            reading.declarations.append(
                Declaration(
                    directory=directory,
                    kind=UnitKind.COMPOSE,
                    aliases=_names([*service.names, _image_alias(index, ref.repository)]),
                    builds=tuple(path for path in (service.dockerfile, *service.files) if path),
                    variant=service.profiles,
                )
            )
        if not declared:
            scan.diagnose(
                DiagnosticCode.IGNORED_MANIFEST,
                f"{', '.join(project.files)} builds nothing from this repository and runs none of its images",
                *project.files,
            )
    return reading


def skaffold_builds(scan: Scan) -> list[ImageBuild]:
    """Every skaffold artifact's image, and the directory that builds it."""
    builds = []
    for path, artifact, directory in _skaffold_artifacts(scan):
        ref = image_ref(str(artifact.get("image") or ""))
        if ref.repository and directory is not None:
            builds.append(ImageBuild(ref=ref, directory=directory, declared_by=path))
    return builds


def skaffold_units(scan: Scan, index: ImageIndex) -> Reading:
    """A skaffold artifact is a unit at the directory it builds, named by the image it publishes."""
    reading = Reading()
    for path, artifact, directory in _skaffold_artifacts(scan):
        image = str(artifact.get("image") or "")
        if directory is None:
            scan.diagnose(
                DiagnosticCode.UNRESOLVED_IMAGE,
                f"{path} builds {image or 'an artifact'} from a directory that is not here",
                path,
            )
            continue
        reading.declarations.append(
            Declaration(
                directory=directory,
                kind=UnitKind.SKAFFOLD,
                aliases=_names([_image_alias(index, image_ref(image).repository)]),
                builds=(path,),
            )
        )
    return reading


def kubernetes(scan: Scan, index: ImageIndex, repo_name: str) -> Reading:
    """Kubernetes workloads running an image of this repository, named by workload and Service."""
    workloads: list[tuple[str, dict, list, str]] = []
    services: list[tuple[str, dict]] = []
    for path in scan.paths_of(FileKind.YAML):
        text = scan.text(path)
        if "apiVersion" not in text or "kind:" not in text:
            continue
        for document in scan.documents(path):
            kind = document.get("kind")
            metadata, specification = mapping(document.get("metadata")), mapping(document.get("spec"))
            name = metadata.get("name")
            if not isinstance(name, str) or not specification:
                continue
            if kind in _WORKLOADS:
                template = pod_template(kind, specification, metadata)
                labels = mapping(mapping(template.get("metadata")).get("labels"))
                containers = listing(mapping(template.get("spec")).get("containers"))
                workloads.append((name, labels, containers, path))
            elif kind == "Service":
                services.append((name, mapping(specification.get("selector"))))

    reading = Reading()
    for name, labels, containers, path in workloads:
        directories = {
            directory
            for container in containers
            if isinstance(container, dict)
            for directory in [_image_directory(str(container.get("image") or ""), index, repo_name)]
            if directory is not None
        }
        if len(directories) != 1:
            continue
        selecting = [service for service, selector in services if selector and _selects(selector, labels)]
        reading.declarations.append(
            Declaration(
                directory=directories.pop(),
                kind=UnitKind.K8S,
                aliases=_names([name, *selecting]),
                builds=(path,),
            )
        )
    return reading


def pod_template(kind: object, specification: dict, metadata: dict) -> dict:
    """The pod a workload runs, whichever nesting its kind uses; a bare Pod is its own template."""
    if kind == "CronJob":
        specification = mapping(mapping(specification.get("jobTemplate")).get("spec"))
    template = specification.get("template")
    if isinstance(template, dict):
        return template
    return {"spec": specification, "metadata": metadata} if kind == "Pod" else {}


def helm(scan: Scan, index: ImageIndex, repo_name: str) -> Reading:
    """A chart deploying exactly one image of this repository: the chart's name is that unit's."""
    reading = Reading()
    for path in scan.paths_of(FileKind.HELM_CHART):
        chart = parent_dir(path)
        name = ""
        for document in scan.documents(path):
            name = str(document.get("name") or "")
        directories = set()
        for values in scan.paths_of(FileKind.HELM_VALUES):
            if parent_dir(values) != chart:
                continue
            for document in scan.documents(values):
                for image in _values_images(document):
                    directory = _image_directory(image, index, repo_name)
                    if directory is not None:
                        directories.add(directory)
        if name and len(directories) == 1:
            reading.declarations.append(
                Declaration(directory=directories.pop(), kind=UnitKind.HELM, aliases=(name,), builds=(path,))
            )
    return reading


def aspire(scan: Scan) -> Reading:
    """An Aspire AppHost: every resource it registers names the project or directory behind it."""
    reading = Reading()
    for host in apphost_projects(scan):
        directory = parent_dir(host)
        by_identifier = {
            re.sub(r"[^A-Za-z0-9_]", "_", reference.rsplit("/", 1)[-1].rsplit(".", 1)[0]): parent_dir(reference)
            for reference in project_references(scan, host)
        }
        for source in _sources_under(scan, directory, ".cs"):
            text = scan.text(source)
            for identifier, name in _ADD_PROJECT_TYPED.findall(text):
                if identifier in by_identifier:
                    reading.declarations.append(_aspire_unit(by_identifier[identifier], name, source))
            for name, path in _ADD_PROJECT_PATH.findall(text) + _ADD_APP.findall(text):
                target = repo_path(directory, path.replace("\\", "/"))
                target = parent_dir(target) if target.endswith((".csproj", ".fsproj")) else target
                if target and scan.has_dir(target):
                    reading.declarations.append(_aspire_unit(target, name, source))
    return reading


def apphost_projects(scan: Scan) -> list[str]:
    """The .NET projects that are Aspire AppHosts: manifests written in C#, one per topology."""
    return [path for path in scan.paths_of(FileKind.DOTNET_PROJECT) if any(m in scan.text(path) for m in ASPIRE_HOST)]


def workflow_builds(scan: Scan) -> list[ImageBuild]:
    """The images a CI workflow builds, and the directory each one's Dockerfile sits in."""
    builds: list[ImageBuild] = []
    for path in scan.paths_of(FileKind.WORKFLOW):
        for document in scan.documents(path):
            workflow_env = _strings(document.get("env"))
            for job in mapping(document.get("jobs")).values():
                if not isinstance(job, dict):
                    continue
                environment = {**workflow_env, **_strings(job.get("env"))}
                for matrix in _matrix_rows(job):
                    builds += _job_builds(scan, path, job, matrix, environment)
    return builds


def _job_builds(
    scan: Scan, path: str, job: dict, matrix: dict[str, str], environment: dict[str, str]
) -> list[ImageBuild]:
    steps = [step for step in listing(job.get("steps")) if isinstance(step, dict)]
    metadata_images = [
        image
        for step in steps
        if _METADATA_ACTION in str(step.get("uses") or "")
        for image in _lines(_expand(str(mapping(step.get("with")).get("images") or ""), matrix, environment))
    ]
    builds = []
    for step in steps:
        uses = str(step.get("uses") or "")
        inputs = mapping(step.get("with"))
        if any(action in uses for action in _BUILD_ACTIONS):
            images = _step_images(inputs, matrix, environment) or metadata_images
            context = _workflow_context(_expand(str(inputs.get("context") or ""), matrix, environment))
            dockerfile = repo_path(context, _expand(str(inputs.get("file") or ""), matrix, environment))
            builds += _image_builds(scan, path, images, context, dockerfile)
            continue
        for arguments in _DOCKER_BUILD.findall(_expand(str(step.get("run") or ""), matrix, environment)):
            tags = _BUILD_TAG.findall(arguments)
            file = _BUILD_FILE.findall(arguments)
            words = [word for word in arguments.split() if not word.startswith("-")]
            context = _workflow_context(words[-1] if words else "")
            builds += _image_builds(scan, path, tags, context, repo_path(context, file[0]) if file else "")
    return builds


def _image_builds(scan: Scan, path: str, images: list[str], context: str, dockerfile: str) -> list[ImageBuild]:
    if dockerfile and not scan.on_disk(dockerfile):
        return []
    directory = build_directory(scan, context, dockerfile)
    if not scan.has_dir(directory):
        return []
    builds = []
    for image in images:
        ref = image_ref(image)
        if ref.repository:
            builds.append(ImageBuild(ref=ref, directory=directory, declared_by=path))
    return builds


def _step_images(inputs: dict, matrix: dict[str, str], environment: dict[str, str]) -> list[str]:
    """The images a build step names, in `tags` or in the `name=` of a buildx output."""
    images = _lines(_expand(str(inputs.get("tags") or ""), matrix, environment))
    outputs = _expand(str(inputs.get("outputs") or ""), matrix, environment)
    for part in re.split(r"[,\n]", outputs):
        key, separator, value = part.strip().partition("=")
        if separator and key.strip() == "name" and value:
            images.append(value)
    return [image for image in images if image]


def _matrix_rows(job: dict) -> list[dict[str, str]]:
    """One row per `matrix.include` entry, so a matrix build reads as the builds it expands into."""
    included = listing(mapping(mapping(job.get("strategy")).get("matrix")).get("include"))
    rows = [_strings(entry) for entry in included if isinstance(entry, dict)]
    return rows or [{}]


def _expand(text: str, matrix: dict[str, str], environment: dict[str, str], depth: int = 0) -> str:
    """A workflow expression with the matrix row and the workflow's own `env` substituted."""
    return _EXPRESSION.sub(lambda match: _lookup(match.group(1), matrix, environment, depth), text)


def _lookup(expression: str, matrix: dict[str, str], environment: dict[str, str], depth: int) -> str:
    """One expression's value, or an empty string when nothing in the file knows it.

    Only `matrix` and `env` are written down here: `vars` and `secrets` live in the repository's
    settings, so an expression falls through them to whatever literal its `||` offers.
    """
    for alternative in expression.split("||"):
        candidate = alternative.strip()
        if len(candidate) > 1 and candidate[0] == candidate[-1] and candidate[0] in "\"'":
            return candidate[1:-1]
        found = re.fullmatch(r"(env|matrix)(?:\.(\w+)|\[\s*matrix\.(\w+)\s*\])", candidate)
        if found is None:
            continue
        context = matrix if found.group(1) == "matrix" else environment
        value = context.get(found.group(2) or matrix.get(found.group(3) or "", ""), "")
        if value:
            return _expand(value, matrix, environment, depth + 1) if depth < 3 else value
    return ""


def _workflow_context(value: str) -> str:
    """A build context as a workflow writes it: `{{defaultContext}}` is the repository, `:sub` a part."""
    cleaned = value.strip()
    if cleaned.startswith("{{defaultContext}}"):
        cleaned = cleaned[len("{{defaultContext}}") :].lstrip(":")
    return repo_path("", cleaned or ".")


def _skaffold_artifacts(scan: Scan) -> list[tuple[str, dict, str | None]]:
    """Each artifact with the directory it builds, or None when that directory is not here."""
    found = []
    for path in scan.paths_of(FileKind.SKAFFOLD):
        for document in scan.documents(path):
            for artifact in _artifacts(document):
                context = repo_path(parent_dir(path), str(artifact.get("context") or "."))
                found.append((path, artifact, _artifact_directory(scan, artifact, context)))
    return found


def _image_alias(index: ImageIndex, repository: str) -> str:
    """An image name is a unit's own name only when one directory here builds it (§6 rule 1)."""
    return repository if repository and index.sole_builder(repository) else ""


def _service_directory(scan: Scan, service: ComposeService, index: ImageIndex, repo_name: str) -> str | None:
    if service.context or service.dockerfile:
        directory = build_directory(scan, service.context, service.dockerfile)
        return directory if builds_source(scan, service) and scan.has_dir(directory) else None
    return _image_directory(service.image, index, repo_name)


def _image_directory(image: str, index: ImageIndex, repo_name: str) -> str | None:
    """The directory behind an image a deployment file runs, or None when this repository builds none."""
    build = index.build_of(image)
    if build is not None:
        return build.directory if build.builds_source else None
    return "" if index.names_this_repository(image, repo_name) else None


def _artifact_directory(scan: Scan, artifact: dict, context: str) -> str | None:
    project = str(mapping(artifact.get("jib")).get("project") or "")
    if project:
        target = repo_path(context, project)
        return target if target and scan.has_dir(target) else None
    dockerfile = repo_path(context, str(mapping(artifact.get("docker")).get("dockerfile") or ""))
    directory = build_directory(scan, context, dockerfile)
    return directory if scan.has_dir(directory) else None


def _artifacts(document: dict) -> list[dict]:
    artifacts = list(listing(mapping(document.get("build")).get("artifacts")))
    for profile in listing(document.get("profiles")):
        artifacts += list(listing(mapping(mapping(profile).get("build")).get("artifacts")))
    return [artifact for artifact in artifacts if isinstance(artifact, dict) and artifact.get("image")]


def _selects(selector: dict, labels: dict) -> bool:
    return all(labels.get(key) == value for key, value in selector.items())


def _values_images(document: dict) -> list[str]:
    """Every image a chart's values name, as `image: repo:tag` or `image: {repository, tag}`."""
    images: list[str] = []

    def walk(node: Any, key: str) -> None:
        if isinstance(node, dict):
            if key == "image" and isinstance(node.get("repository"), str):
                images.append(node["repository"])
            for name, value in node.items():
                walk(value, str(name))
        elif isinstance(node, list):
            for value in node:
                walk(value, key)
        elif isinstance(node, str) and key == "image":
            images.append(node)

    walk(document, "")
    return images


def _sources_under(scan: Scan, directory: str, suffix: str) -> list[str]:
    prefix = f"{directory}/" if directory else ""
    return sorted(
        f"{where}/{name}" if where else name
        for where, names in scan.by_dir.items()
        if where == directory or where.startswith(prefix)
        for name in names
        if name.endswith(suffix)
    )


def _aspire_unit(directory: str, name: str, source: str) -> Declaration:
    return Declaration(directory=directory, kind=UnitKind.ASPIRE, aliases=_names([name]), builds=(source,))


def _strings(mapping_node: object) -> dict[str, str]:
    return {
        str(key): str(value)
        for key, value in mapping(mapping_node).items()
        if isinstance(value, (str, int, float)) and not isinstance(value, bool)
    }


def _lines(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,\n]", text) if part.strip()]


def _names(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip() for value in values if value and value.strip()))
