from dataclasses import dataclass

from static_analyzer.constants import Language
from static_analyzer.internal_references import looks_internal_reference, parent_qualified_name


@dataclass
class _Node:
    fully_qualified_name: str


class _ReferenceSource:
    def __init__(self, names: list[str]):
        self._nodes = [_Node(name) for name in names]

    def get_languages(self) -> list[Language]:
        return [Language.PYTHON]

    def iter_reference_nodes(self, _lang: Language):
        return iter(self._nodes)


def test_internal_reference_uses_repeated_project_token_without_hardcoded_layout_names():
    source = _ReferenceSource(
        [
            "workspace.alpha.source.alpha.Service.run",
            "workspace.beta.source.beta.Worker.run",
        ]
    )

    assert looks_internal_reference(source, "alpha.MissingService")
    assert not looks_internal_reference(source, "source.ExternalClient")


def test_internal_reference_uses_second_token_under_shared_container_root():
    source = _ReferenceSource(
        [
            "src.service.OCR.extract_text",
            "src.core.Registry.load",
        ]
    )

    assert looks_internal_reference(source, "service._missing_helper")
    assert not looks_internal_reference(source, "openai.OpenAI")


def test_internal_reference_keeps_private_token_signal():
    source = _ReferenceSource(["app.pipeline._internal_dispatch.run"])

    assert looks_internal_reference(source, "plugin._internal_dispatch")


def test_parent_qualified_name_strips_member_and_signature():
    assert parent_qualified_name("pkg.Dog.__init__") == "pkg.Dog"
    assert parent_qualified_name("pkg.Dog(args).run") == "pkg.Dog"
    assert parent_qualified_name("standalone") == ""
