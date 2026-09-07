"""Tests for static_analyzer.engine.type_reference_builder."""

from pathlib import Path

from static_analyzer.cfg import CallGraph, EdgeKind, ReferenceEdge
from static_analyzer.config import NodeType
from static_analyzer.engine.models import TypeReferenceSite
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.engine.type_reference_builder import (
    TypeIndex,
    TypeReferenceStats,
    build_type_references,
    complete_type_references,
    simple_type_name,
)
from static_analyzer.node import Node


def _class(qualified_name: str, path: Path, start: int = 1, end: int = 20) -> Node:
    return Node(qualified_name, NodeType.CLASS, str(path), start, end)


def _method(qualified_name: str, path: Path, start: int, end: int) -> Node:
    return Node(qualified_name, NodeType.METHOD, str(path), start, end)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _site(path: Path, name: str, line: int = 2, qualifier: str = "") -> TypeReferenceSite:
    return TypeReferenceSite(file=str(path), line=line, column=1, name=name, qualifier=qualifier)


def _resolved_name(index: TypeIndex, site: TypeReferenceSite) -> str | None:
    node = index.resolve(site)
    return node.fully_qualified_name if node is not None else None


def test_simple_type_name_strips_path_and_arity():
    assert simple_type_name("a.b.Foo<T>") == "Foo"
    assert simple_type_name("Foo") == "Foo"


class TestTypeIndexCSharp:
    def test_a_unique_name_resolves_from_anywhere(self, tmp_path: Path):
        foo = _write(tmp_path / "lib" / "Foo.cs", "namespace Lib;\npublic class Foo { }\n")
        user = _write(tmp_path / "app" / "User.cs", "namespace App;\npublic class User { }\n")
        index = TypeIndex([_class("lib.Foo", foo)], SourceInspector())
        assert _resolved_name(index, _site(user, "Foo")) == "lib.Foo"

    def test_the_enclosing_namespace_wins(self, tmp_path: Path):
        a = _write(tmp_path / "a" / "Foo.cs", "namespace A;\npublic class Foo { }\n")
        b = _write(tmp_path / "b" / "Foo.cs", "namespace B;\npublic class Foo { }\n")
        user = _write(tmp_path / "c" / "User.cs", "namespace B;\npublic class User { }\n")
        index = TypeIndex([_class("a.Foo", a), _class("b.Foo", b)], SourceInspector())
        assert _resolved_name(index, _site(user, "Foo")) == "b.Foo"

    def test_a_using_directive_makes_a_namespace_visible(self, tmp_path: Path):
        a = _write(tmp_path / "a" / "Foo.cs", "namespace A;\npublic class Foo { }\n")
        b = _write(tmp_path / "b" / "Foo.cs", "namespace B;\npublic class Foo { }\n")
        user = _write(tmp_path / "c" / "User.cs", "using A;\nnamespace C;\npublic class User { }\n")
        index = TypeIndex([_class("a.Foo", a), _class("b.Foo", b)], SourceInspector())
        assert _resolved_name(index, _site(user, "Foo")) == "a.Foo"

    def test_a_parent_namespace_is_visible(self, tmp_path: Path):
        a = _write(tmp_path / "a" / "Foo.cs", "namespace Root;\npublic class Foo { }\n")
        b = _write(tmp_path / "b" / "Foo.cs", "namespace Other;\npublic class Foo { }\n")
        user = _write(tmp_path / "c" / "User.cs", "namespace Root.Sub;\npublic class User { }\n")
        index = TypeIndex([_class("a.Foo", a), _class("b.Foo", b)], SourceInspector())
        assert _resolved_name(index, _site(user, "Foo")) == "a.Foo"

    def test_the_written_qualifier_narrows_candidates(self, tmp_path: Path):
        a = _write(tmp_path / "a" / "Foo.cs", "namespace A;\npublic class Foo { }\n")
        b = _write(tmp_path / "b" / "Foo.cs", "namespace B;\npublic class Foo { }\n")
        user = _write(tmp_path / "c" / "User.cs", "namespace A;\npublic class User { }\n")
        index = TypeIndex([_class("a.Foo", a), _class("b.Foo", b)], SourceInspector())
        assert _resolved_name(index, _site(user, "Foo", qualifier="B")) == "b.Foo"

    def test_equally_visible_copies_prefer_the_closest_directory(self, tmp_path: Path):
        """Every project template declares the same ``Program``; a reference means its own copy."""
        first = _write(tmp_path / "t1" / "Program.cs", "public class Program { }\n")
        second = _write(tmp_path / "t2" / "Program.cs", "public class Program { }\n")
        user = _write(tmp_path / "t1" / "Startup.cs", "public class Startup { }\n")
        index = TypeIndex([_class("t1.Program", first), _class("t2.Program", second)], SourceInspector())
        assert _resolved_name(index, _site(user, "Program")) == "t1.Program"

    def test_equidistant_copies_are_undecidable(self, tmp_path: Path):
        x = _write(tmp_path / "x" / "Foo.cs", "namespace N;\npublic class Foo { }\n")
        y = _write(tmp_path / "y" / "Foo.cs", "namespace N;\npublic class Foo { }\n")
        user = _write(tmp_path / "z" / "User.cs", "namespace N;\npublic class User { }\n")
        index = TypeIndex([_class("x.Foo", x), _class("y.Foo", y)], SourceInspector())
        assert index.resolve(_site(user, "Foo")) is None
        assert index.has_candidates("Foo")

    def test_an_unknown_name_is_none(self, tmp_path: Path):
        user = _write(tmp_path / "User.cs", "public class User { }\n")
        index = TypeIndex([_class("User", user)], SourceInspector())
        assert index.resolve(_site(user, "Guid")) is None
        assert not index.has_candidates("Guid")


class TestTypeIndexScript:
    def test_resolves_through_a_relative_import(self, tmp_path: Path):
        svc = _write(tmp_path / "svc.ts", "export class Svc { }\n")
        user = _write(tmp_path / "user.ts", "import { Svc } from './svc';\n")
        index = TypeIndex([_class("svc.Svc", svc)], SourceInspector())
        assert _resolved_name(index, _site(user, "Svc")) == "svc.Svc"

    def test_an_alias_resolves_to_the_exported_name(self, tmp_path: Path):
        svc = _write(tmp_path / "svc.ts", "export class Svc { }\n")
        user = _write(tmp_path / "user.ts", "import { Svc as S } from './svc';\n")
        index = TypeIndex([_class("svc.Svc", svc)], SourceInspector())
        assert _resolved_name(index, _site(user, "S")) == "svc.Svc"

    def test_a_namespace_import_resolves_the_qualified_name(self, tmp_path: Path):
        svc = _write(tmp_path / "lib" / "svc.ts", "export class Svc { }\n")
        user = _write(tmp_path / "user.ts", "import * as NS from './lib/svc';\n")
        index = TypeIndex([_class("lib.svc.Svc", svc)], SourceInspector())
        assert _resolved_name(index, _site(user, "Svc", qualifier="NS")) == "lib.svc.Svc"

    def test_a_package_import_is_external_even_when_a_repo_class_shares_the_name(self, tmp_path: Path):
        svc = _write(tmp_path / "svc.ts", "export class Svc { }\n")
        user = _write(tmp_path / "user.ts", "import { Svc } from '@abp/ng.core';\n")
        index = TypeIndex([_class("svc.Svc", svc)], SourceInspector())
        assert index.resolve(_site(user, "Svc")) is None

    def test_a_same_file_class_needs_no_import(self, tmp_path: Path):
        user = _write(tmp_path / "user.ts", "class Local { }\n")
        index = TypeIndex([_class("user.Local", user)], SourceInspector())
        assert _resolved_name(index, _site(user, "Local")) == "user.Local"

    def test_an_unimported_name_resolves_only_when_unique(self, tmp_path: Path):
        first = _write(tmp_path / "a" / "svc.ts", "export class Svc { }\n")
        second = _write(tmp_path / "b" / "svc.ts", "export class Svc { }\n")
        user = _write(tmp_path / "user.ts", "const x = 1;\n")
        assert TypeIndex([_class("a.svc.Svc", first)], SourceInspector()).resolve(_site(user, "Svc")) is not None
        both = TypeIndex([_class("a.svc.Svc", first), _class("b.svc.Svc", second)], SourceInspector())
        assert both.resolve(_site(user, "Svc")) is None


class TestContainerAt:
    def test_innermost_declaration_wins(self, tmp_path: Path):
        path = tmp_path / "A.cs"
        method = _method("A.M()", path, 5, 10)
        index = TypeIndex([_class("A", path, 1, 20), method], SourceInspector())
        assert index.container_at(str(path), 7) is method
        outer = index.container_at(str(path), 3)
        assert outer is not None and outer.fully_qualified_name == "A"
        assert index.container_at(str(path), 30) is None
        assert index.container_at("elsewhere.cs", 7) is None


class TestBuildTypeReferences:
    def _fixture(self, tmp_path: Path) -> tuple[Path, Path, list[Node]]:
        module = _write(
            tmp_path / "app" / "AppModule.cs",
            "using Volo;\n"
            "namespace App;\n"
            "[DependsOn(typeof(CoreModule))]\n"
            "public class AppModule : AbpModule\n"
            "{\n"
            "    public void Configure(AbpOptions options) { }\n"
            "}\n",
        )
        core = _write(
            tmp_path / "volo" / "Core.cs",
            "namespace Volo;\npublic class AbpModule { }\npublic class CoreModule { }\npublic class AbpOptions { }\n",
        )
        nodes = [
            _class("app.AppModule", module, 3, 7),
            _method("app.AppModule.Configure(AbpOptions options)", module, 6, 6),
            _class("volo.Core.AbpModule", core, 2, 2),
            _class("volo.Core.CoreModule", core, 3, 3),
            _class("volo.Core.AbpOptions", core, 4, 4),
        ]
        return module, core, nodes

    def test_edges_run_from_the_enclosing_declaration_to_the_named_type(self, tmp_path: Path):
        module, core, nodes = self._fixture(tmp_path)
        inspector = SourceInspector()
        stats = TypeReferenceStats()
        pairs = build_type_references(inspector, TypeIndex(nodes, inspector), [module, core], stats)
        assert set(pairs) == {
            ("app.AppModule", "volo.Core.CoreModule"),
            ("app.AppModule", "volo.Core.AbpModule"),
            ("app.AppModule.Configure(AbpOptions options)", "volo.Core.AbpOptions"),
        }
        assert (stats.sites, stats.resolved, stats.unresolved, stats.ambiguous) == (3, 3, 0, 0)

    def test_a_declaration_naming_its_own_type_is_not_an_edge(self, tmp_path: Path):
        path = _write(tmp_path / "A.cs", "public class A\n{\n    public A Clone(A other) { return other; }\n}\n")
        nodes = [_class("A", path, 1, 4), _method("A.Clone(A other)", path, 3, 3)]
        inspector = SourceInspector()
        stats = TypeReferenceStats()
        assert build_type_references(inspector, TypeIndex(nodes, inspector), [path], stats) == []
        assert stats.resolved == 2

    def test_complete_type_references_replaces_stale_edges_and_keeps_other_kinds(self, tmp_path: Path):
        module, core, nodes = self._fixture(tmp_path)
        graph = CallGraph(language="csharp")
        for node in nodes:
            graph.add_node(node)
        graph.add_reference_edge(ReferenceEdge("app.AppModule", "volo.Core.AbpOptions", EdgeKind.TYPEREF))
        graph.add_reference_edge(ReferenceEdge("app.AppModule", "volo.Core.AbpModule", EdgeKind.INHERITS))

        stats = complete_type_references(graph, [module, core], SourceInspector())
        stats_again = complete_type_references(graph, [module, core], SourceInspector())

        typerefs = {(ref.src, ref.dst) for ref in graph.reference_edges if ref.kind is EdgeKind.TYPEREF}
        assert typerefs == {
            ("app.AppModule", "volo.Core.CoreModule"),
            ("app.AppModule", "volo.Core.AbpModule"),
            ("app.AppModule.Configure(AbpOptions options)", "volo.Core.AbpOptions"),
        }
        assert stats.edges == stats_again.edges == 3
        assert sum(1 for ref in graph.reference_edges if ref.kind is EdgeKind.TYPEREF) == 3
        assert ReferenceEdge("app.AppModule", "volo.Core.AbpModule", EdgeKind.INHERITS) in graph.reference_edges
        assert "3 sites -> 3 edges" in stats.summary()
