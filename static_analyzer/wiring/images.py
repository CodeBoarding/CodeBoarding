"""Image references, and the index of which images this repository builds.

A compose service, a Kubernetes workload or a Helm chart names an image, and whether that image is
a unit of this repository or someone else's software depends on whether anything here builds it.
The index answers that: every place that builds and tags an image — a compose service with both
`build` and `image`, a skaffold artifact, a Maven plugin's tag, a CI workflow's build step —
records the directory it builds from, so a service that only runs `langfuse/langfuse` still lands
on the directory whose Dockerfile makes it.

A build that copies nothing from its context builds no source of this repository: a toolchain image
that mounts the tree at run time, a dev container. It is recorded as such rather than dropped, so
the image it names resolves to nothing instead of falling through to a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from static_analyzer.wiring.scan import Scan, parent_dir

#: `COPY --from=build`, `COPY --chown=x` and so on. A source outside the context is not this repository.
_FLAG = re.compile(r"^--[\w-]+(?:=\S*)?$")
_CONTINUED = re.compile(r"\\\s*$")
_DOCKERFILE_NAME = re.compile(r"(?i)^(?:dockerfile|containerfile)|\.(?:dockerfile|containerfile)$")


@dataclass(frozen=True)
class ImageRef:
    """An image name split the way a registry reads it, with no host, tag or digest in the path."""

    repository: str
    tag: str

    @property
    def key(self) -> str:
        return f"{self.repository}:{self.tag}" if self.tag else self.repository


@dataclass(frozen=True)
class ImageBuild:
    """One place that builds an image, and the directory it builds."""

    ref: ImageRef
    directory: str
    declared_by: str
    builds_source: bool = True


@dataclass(frozen=True)
class Dockerfile:
    path: str
    copies_context: bool


def image_ref(image: str) -> ImageRef:
    """`docker.io/openzipkin/zipkin:3` as repository `openzipkin/zipkin`, tag `3`.

    Empty when the name still holds a variable nothing resolved: a name that is not a name cannot
    join anything (§6 rule 4).
    """
    reference = image.strip()
    if not reference or "${" in reference or "$(" in reference or "{{" in reference:
        return ImageRef("", "")
    reference = reference.split("@", 1)[0]
    parts = reference.split("/")
    if len(parts) > 1 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        parts = parts[1:]
    repository, _, tag = parts[-1].partition(":")
    parts[-1] = repository
    return ImageRef(repository="/".join(parts).lower(), tag=tag.lower())


def read_dockerfile(scan: Scan, path: str) -> Dockerfile:
    """A Dockerfile, and whether any instruction brings a file of this repository into the image."""
    text = scan.text(path)
    copies = False
    for instruction, arguments in _instructions(text):
        if instruction not in ("COPY", "ADD"):
            continue
        words = [word for word in arguments.split() if not _FLAG.fullmatch(word)]
        flags = [word for word in arguments.split() if _FLAG.fullmatch(word)]
        if any(flag.startswith("--from=") for flag in flags):
            continue
        sources = words[:-1] if len(words) > 1 else words
        copies = copies or any("://" not in source and not source.startswith("git@") for source in sources)
    return Dockerfile(path=path, copies_context=copies)


def build_directory(scan: Scan, context: str, dockerfile: str) -> str:
    """The directory a build declares: the Dockerfile's own when something else is in it.

    A context is what is sent to the daemon and is often the whole tree, so the Dockerfile is the
    better clue: `./src/cart/src/Dockerfile` builds the cart. But a `docker/` folder that holds
    nothing but Dockerfiles builds whatever its context is, not itself.
    """
    inside = parent_dir(dockerfile)
    if not dockerfile or inside == context:
        return context
    if context and not inside.startswith(context + "/"):
        return context
    return inside if _holds_more_than_dockerfiles(scan, inside) else context


def _holds_more_than_dockerfiles(scan: Scan, directory: str) -> bool:
    return any(not _DOCKERFILE_NAME.match(name) for name in scan.names_in(directory))


class ImageIndex:
    """What this repository builds, keyed by image, so a service that runs one lands on a directory."""

    def __init__(self) -> None:
        self._by_key: dict[str, list[ImageBuild]] = {}
        self._by_repository: dict[str, list[ImageBuild]] = {}

    def add(self, build: ImageBuild) -> None:
        if build.ref.repository:
            self._by_key.setdefault(build.ref.key, []).append(build)
            self._by_repository.setdefault(build.ref.repository, []).append(build)

    def build_of(self, image: str) -> ImageBuild | None:
        """The build behind an image: an exact repository and tag first, then the repository alone.

        Why the second try: a workflow tags what it builds `:latest` and a compose file pins
        `:2.1`, and they are the same image. None when nothing here builds it, and none when two
        directories do — one key with several providers draws nothing and is reported (§6 rule 7).
        """
        ref = image_ref(image)
        if not ref.repository:
            return None
        for candidates in (self._by_key.get(ref.key, []), self._by_repository.get(ref.repository, [])):
            directories = {build.directory for build in candidates}
            if len(directories) == 1:
                return candidates[0]
            if directories:
                return None
        return None

    def sole_builder(self, repository: str) -> str:
        """The one directory that builds this repository path, empty when several or none do.

        Why: an image several directories build under different tags — one demo image per service —
        is nobody's name, and making it an alias would make every one of them ambiguous.
        """
        directories = {build.directory for build in self._by_repository.get(repository, [])}
        return directories.pop() if len(directories) == 1 else ""

    def ambiguous(self) -> list[tuple[str, tuple[str, ...], tuple[str, ...]]]:
        """Every image two directories build: the image, the directories, the files that say so."""
        rows = []
        for key, builds in sorted(self._by_key.items()):
            directories = sorted({build.directory for build in builds})
            if len(directories) > 1:
                rows.append((key, tuple(directories), tuple(sorted({build.declared_by for build in builds}))))
        return rows

    def names_this_repository(self, image: str, repo_name: str) -> bool:
        """Whether an image nothing here builds is named after this repository.

        The last fallback of §6: `apache/superset` in a chart of the superset repository is that
        repository's own image. It never overrides a build, so a name a builder claims stays with it.
        """
        ref = image_ref(image)
        if not ref.repository or self.build_of(image) is not None:
            return False
        return ref.repository.split("/")[-1] == repo_name.lower()


def _instructions(text: str) -> list[tuple[str, str]]:
    """Every Dockerfile instruction as (INSTRUCTION, arguments), with continuation lines joined."""
    joined: list[str] = []
    buffer = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if not buffer and (not line.strip() or line.lstrip().startswith("#")):
            continue
        buffer = f"{buffer} {line.strip()}" if buffer else line.strip()
        if _CONTINUED.search(buffer):
            buffer = _CONTINUED.sub("", buffer).rstrip()
            continue
        joined.append(buffer)
        buffer = ""
    if buffer:
        joined.append(buffer)
    out = []
    for line in joined:
        instruction, _, arguments = line.partition(" ")
        out.append((instruction.upper(), arguments.strip()))
    return out
