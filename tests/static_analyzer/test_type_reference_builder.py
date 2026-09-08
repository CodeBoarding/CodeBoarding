"""Tests for static_analyzer.engine.type_reference_builder."""

from pathlib import Path

from static_analyzer.cfg import CallGraph, EdgeKind, ReferenceEdge
from static_analyzer.config import NodeType
from static_analyzer.engine.models import TypeReferenceSite
from static_analyzer.engine.source_inspector import SourceInspector, simple_type_name
from static_analyzer.engine.type_reference_builder import (
    TypeIndex,
    TypeReferenceStats,
    build_type_references,
    complete_type_references,
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
    return TypeReferenceSite(file_path=str(path), line=line, column=1, name=name, qualifier=qualifier)


def _resolved_name(index: TypeIndex, site: TypeReferenceSite) -> str | None:
    node = index.resolve(site)
    return node.fully_qualified_name if node is not None else None


def _pairs(edges: list[ReferenceEdge]) -> set[tuple[str, str]]:
    return {(edge.src, edge.dst) for edge in edges}


def test_simple_type_name_strips_path_and_arity():
    assert simple_type_name("a.b.Foo<T>") == "Foo"
    assert simple_type_name("Foo") == "Foo"
    # A dotted type argument must not win the last-dot split...
    assert simple_type_name("a.b.Box<x.y.T>") == "Box"
    # ...and a generic segment that is not the last one must not swallow the name after it.
    assert simple_type_name("A.B.Repo<T>.Entry") == "Entry"
    assert simple_type_name("A.Outer<x.y.T>.Inner") == "Inner"
    # A symbol whose name carries prose with an unbalanced bracket still yields its last segment.
    assert simple_type_name("suite('release <= version') callback.downloadUrls") == "downloadUrls"


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

    def test_a_qualifier_matches_whole_segments_only(self, tmp_path: Path):
        """``Domain`` written as a qualifier must not match a ``FooDomain`` namespace."""
        foo = _write(tmp_path / "a" / "Order.cs", "namespace Lib.FooDomain;\npublic class Order { }\n")
        domain = _write(tmp_path / "b" / "Order.cs", "namespace Lib.Domain;\npublic class Order { }\n")
        user = _write(tmp_path / "a" / "User.cs", "namespace Lib;\npublic class User { }\n")
        index = TypeIndex([_class("a.Order", foo), _class("b.Order", domain)], SourceInspector())
        # Without the segment rule the nearer ``a/Order.cs`` survives the suffix test and wins on proximity.
        assert _resolved_name(index, _site(user, "Order", qualifier="Domain")) == "b.Order"
        assert _resolved_name(index, _site(user, "Order", qualifier="FooDomain")) == "a.Order"

    def test_a_nested_type_resolves_through_its_enclosing_type(self, tmp_path: Path):
        outer = _write(
            tmp_path / "n" / "Outer.cs", "namespace N;\npublic class Outer\n{\n    public class Inner { }\n}\n"
        )
        other = _write(tmp_path / "m" / "Inner.cs", "namespace M;\npublic class Inner { }\n")
        user = _write(tmp_path / "u" / "User.cs", "namespace N;\npublic class User { }\n")
        nodes = [_class("n.Outer", outer, 2, 5), _class("n.Outer.Inner", outer, 4, 4), _class("m.Inner", other)]
        index = TypeIndex(nodes, SourceInspector())
        assert _resolved_name(index, _site(user, "Inner", qualifier="Outer")) == "n.Outer.Inner"
        assert _resolved_name(index, _site(user, "Inner", qualifier="N.Outer")) == "n.Outer.Inner"

    def test_a_nested_type_is_nameable_unqualified_inside_its_enclosing_type(self, tmp_path: Path):
        outer = _write(
            tmp_path / "n" / "Outer.cs",
            "namespace N;\npublic class Outer\n{\n    public class Inner { }\n    Inner Make() { return null; }\n}\n",
        )
        other = _write(tmp_path / "m" / "Inner.cs", "namespace M;\npublic class Inner { }\n")
        nodes = [
            _class("n.Outer", outer, 2, 6),
            _class("n.Outer.Inner", outer, 4, 4),
            _method("n.Outer.Make()", outer, 5, 5),
            _class("m.Inner", other),
        ]
        index = TypeIndex(nodes, SourceInspector())
        assert _resolved_name(index, _site(outer, "Inner", line=5)) == "n.Outer.Inner"

    def test_a_bare_name_does_not_reach_a_type_nested_in_another(self, tmp_path: Path):
        """A bare ``File`` is the framework's, whatever ``Options.File`` the repository declares."""
        outer = _write(
            tmp_path / "n" / "Outer.cs", "namespace N;\npublic class Outer\n{\n    public class Inner { }\n}\n"
        )
        user = _write(tmp_path / "u" / "User.cs", "using N;\nnamespace U;\npublic class User { }\n")
        nodes = [_class("n.Outer", outer, 2, 5), _class("n.Outer.Inner", outer, 4, 4)]
        index = TypeIndex(nodes, SourceInspector())
        assert index.resolve(_site(user, "Inner", line=3)) is None

    def test_a_static_using_names_a_nested_type_bare(self, tmp_path: Path):
        outer = _write(
            tmp_path / "n" / "Outer.cs", "namespace N;\npublic class Outer\n{\n    public class Inner { }\n}\n"
        )
        user = _write(tmp_path / "u" / "User.cs", "using static N.Outer;\nnamespace U;\npublic class User { }\n")
        nodes = [_class("n.Outer", outer, 2, 5), _class("n.Outer.Inner", outer, 4, 4)]
        index = TypeIndex(nodes, SourceInspector())
        assert _resolved_name(index, _site(user, "Inner", line=3)) == "n.Outer.Inner"

    def test_a_top_level_type_answers_the_bare_name_instead(self, tmp_path: Path):
        outer = _write(
            tmp_path / "n" / "Outer.cs", "namespace N;\npublic class Outer\n{\n    public class Inner { }\n}\n"
        )
        other = _write(tmp_path / "m" / "Inner.cs", "namespace M;\npublic class Inner { }\n")
        user = _write(tmp_path / "u" / "User.cs", "namespace U;\npublic class User { }\n")
        nodes = [_class("n.Outer", outer, 2, 5), _class("n.Outer.Inner", outer, 4, 4), _class("m.Inner", other)]
        index = TypeIndex(nodes, SourceInspector())
        assert _resolved_name(index, _site(user, "Inner")) == "m.Inner"

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


class TestTypeIndexCSharpUsings:
    """A ``using`` decides a name; the reader records what was written and the resolver applies it."""

    def test_an_alias_decides_the_name_over_directory_proximity(self, tmp_path: Path):
        vendor = _write(tmp_path / "vendor" / "Widget.cs", "namespace Vendor;\npublic class Widget { }\n")
        core = _write(tmp_path / "core" / "Widget.cs", "namespace Core;\npublic class Widget { }\n")
        user = _write(
            tmp_path / "core" / "ui" / "User.cs",
            "using Widget = Vendor.Widget;\nnamespace Core.Ui;\npublic class User { }\n",
        )
        index = TypeIndex([_class("vendor.Widget", vendor), _class("core.Widget", core)], SourceInspector())
        # Without the alias the nearer `core/Widget.cs` wins on shared directory prefix.
        assert _resolved_name(index, _site(user, "Widget", line=3)) == "vendor.Widget"

    def test_an_alias_expands_the_leftmost_segment_of_a_qualified_name(self, tmp_path: Path):
        leaf = _write(tmp_path / "a" / "Leaf.cs", "namespace A.Inner;\npublic class Leaf { }\n")
        user = _write(tmp_path / "u" / "User.cs", "using Io = A.Inner;\nnamespace U;\npublic class User { }\n")
        index = TypeIndex([_class("a.Leaf", leaf)], SourceInspector())
        assert _resolved_name(index, _site(user, "Leaf", line=3, qualifier="Io")) == "a.Leaf"

    def test_an_alias_naming_something_outside_the_graph_resolves_to_nothing(self, tmp_path: Path):
        """It must not fall through and guess: C# says the alias decided the name."""
        core = _write(tmp_path / "core" / "Widget.cs", "namespace Core;\npublic class Widget { }\n")
        user = _write(
            tmp_path / "core" / "ui" / "User.cs",
            "using Widget = Nuget.Package.Widget;\nnamespace Core.Ui;\npublic class User { }\n",
        )
        index = TypeIndex([_class("core.Widget", core)], SourceInspector())
        assert index.resolve(_site(user, "Widget", line=3)) is None

    def test_a_using_inside_a_namespace_block_does_not_reach_its_sibling(self, tmp_path: Path):
        a = _write(tmp_path / "a" / "Widget.cs", "namespace A;\npublic class Widget { }\n")
        b = _write(tmp_path / "b" / "Widget.cs", "namespace B;\npublic class Widget { }\n")
        user = _write(
            tmp_path / "u" / "User.cs",
            "namespace N1\n{\n    using A;\n    class C1 { }\n}\n"
            "namespace N2\n{\n    using B;\n    class C2 { }\n}\n",
        )
        index = TypeIndex([_class("a.Widget", a), _class("b.Widget", b)], SourceInspector())
        assert _resolved_name(index, _site(user, "Widget", line=4)) == "a.Widget"
        assert _resolved_name(index, _site(user, "Widget", line=9)) == "b.Widget"

    def test_a_using_inside_a_namespace_names_the_relative_namespace_first(self, tmp_path: Path):
        """Inside ``namespace Root``, ``using Models;`` means ``Root.Models`` when that exists."""
        nested = _write(tmp_path / "r" / "Foo.cs", "namespace Root.Models;\npublic class Foo { }\n")
        top = _write(tmp_path / "m" / "Foo.cs", "namespace Models;\npublic class Foo { }\n")
        user = _write(tmp_path / "u" / "User.cs", "namespace Root\n{\n    using Models;\n    class User { }\n}\n")
        index = TypeIndex([_class("r.Foo", nested), _class("m.Foo", top)], SourceInspector())
        for declaring in (nested, top):
            index.scope_of(str(declaring))
        assert _resolved_name(index, _site(user, "Foo", line=4)) == "r.Foo"

    def test_a_using_written_in_the_namespace_beats_a_global_using_that_brings_a_second_type(self, tmp_path: Path):
        """C# looks a name up scope by scope: the namespace's own usings decide before the compilation unit's."""
        domain = _write(tmp_path / "lib" / "Order.cs", "namespace Shop.Domain;\npublic class Order { }\n")
        _write(tmp_path / "api" / "Api.csproj", "<Project />\n")
        view = _write(
            tmp_path / "api" / "Queries" / "OrderView.cs", "namespace Shop.Api.Queries;\npublic record Order { }\n"
        )
        globals_file = _write(
            tmp_path / "api" / "GlobalUsings.cs", "global using Shop.Api.Queries;\nglobal using Shop.Domain;\n"
        )
        handler = _write(
            tmp_path / "api" / "Commands" / "Handler.cs",
            "namespace Shop.Api.Commands;\n\nusing Shop.Domain;\n\npublic class Handler { }\n",
        )
        index = TypeIndex([_class("lib.Order", domain), _class("api.Queries.OrderView.Order", view)], SourceInspector())
        index.scope_of(str(globals_file))
        # Directory proximity alone would pick the record beside the handler.
        assert _resolved_name(index, _site(handler, "Order", line=5)) == "lib.Order"

    def test_a_static_using_does_not_make_the_types_beside_its_target_nameable(self, tmp_path: Path):
        """``using static A.B.Check`` brings in Check's members, not the types alongside Check."""
        held = _write(tmp_path / "a" / "X.cs", "namespace A.B;\npublic class X { }\npublic class Check { }\n")
        own = _write(tmp_path / "c" / "X.cs", "namespace C;\npublic class X { }\n")
        user = _write(tmp_path / "u" / "User.cs", "using static A.B.Check;\nnamespace C;\npublic class User { }\n")
        nodes = [_class("a.X", held, 2, 2), _class("a.Check", held, 3, 3), _class("c.X", own)]
        index = TypeIndex(nodes, SourceInspector())
        assert _resolved_name(index, _site(user, "X", line=3)) == "c.X"

    def test_a_static_using_makes_the_types_its_target_holds_nameable(self, tmp_path: Path):
        held = _write(
            tmp_path / "a" / "Check.cs", "namespace A.B;\npublic class Check\n{\n    public class Inner { }\n}\n"
        )
        other = _write(tmp_path / "d" / "Inner.cs", "namespace D;\npublic class Inner { }\n")
        user = _write(tmp_path / "u" / "User.cs", "using static A.B.Check;\nnamespace C;\npublic class User { }\n")
        nodes = [_class("a.Check", held, 2, 5), _class("a.Check.Inner", held, 4, 4), _class("d.Inner", other)]
        index = TypeIndex(nodes, SourceInspector())
        assert _resolved_name(index, _site(user, "Inner", line=3)) == "a.Check.Inner"

    def test_a_global_using_governs_every_file_of_its_compilation(self, tmp_path: Path):
        a = _write(tmp_path / "lib" / "a" / "Foo.cs", "namespace A;\npublic class Foo { }\n")
        b = _write(tmp_path / "lib" / "b" / "Foo.cs", "namespace B;\npublic class Foo { }\n")
        _write(tmp_path / "app" / "App.csproj", "<Project />\n")
        globals_file = _write(tmp_path / "app" / "GlobalUsings.cs", "global using A;\n")
        user = _write(tmp_path / "app" / "src" / "User.cs", "namespace C;\npublic class User { }\n")
        _write(tmp_path / "other" / "Other.csproj", "<Project />\n")
        outsider = _write(tmp_path / "other" / "User.cs", "namespace C;\npublic class User { }\n")
        index = TypeIndex([_class("a.Foo", a), _class("b.Foo", b)], SourceInspector())
        index.scope_of(str(globals_file))
        assert _resolved_name(index, _site(user, "Foo")) == "a.Foo"
        # The other project never sees the directive, and its two candidates are equidistant.
        assert index.resolve(_site(outsider, "Foo")) is None


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

    def test_a_renamed_default_import_resolves_to_the_modules_default_export(self, tmp_path: Path):
        svc = _write(tmp_path / "svc.ts", "export default class Service { }\n")
        user = _write(tmp_path / "user.ts", "import Client from './svc';\n")
        index = TypeIndex([_class("svc.Service", svc)], SourceInspector())
        index.scope_of(str(svc))
        assert _resolved_name(index, _site(user, "Client")) == "svc.Service"

    def test_a_package_import_is_external_even_when_a_repo_class_shares_the_name(self, tmp_path: Path):
        svc = _write(tmp_path / "svc.ts", "export class Svc { }\n")
        user = _write(tmp_path / "user.ts", "import { Svc } from '@abp/ng.core';\n")
        index = TypeIndex([_class("svc.Svc", svc)], SourceInspector())
        assert index.resolve(_site(user, "Svc")) is None

    def test_a_same_file_class_needs_no_import(self, tmp_path: Path):
        user = _write(tmp_path / "user.ts", "class Local { }\n")
        index = TypeIndex([_class("user.Local", user)], SourceInspector())
        assert _resolved_name(index, _site(user, "Local")) == "user.Local"

    def test_an_explicit_js_extension_resolves_to_the_typescript_module(self, tmp_path: Path):
        """What NodeNext forces you to write: the specifier says .js, the module is .ts."""
        svc = _write(tmp_path / "src" / "svc.ts", "export class Svc { }\n")
        user = _write(tmp_path / "src" / "u.ts", "import { Svc } from './svc.js';\n")
        index = TypeIndex([_class("src.svc.Svc", svc)], SourceInspector())
        assert _resolved_name(index, _site(user, "Svc")) == "src.svc.Svc"

    def test_an_explicit_extension_resolves_when_the_module_really_is_javascript(self, tmp_path: Path):
        svc = _write(tmp_path / "src" / "svc.js", "export class Svc { }\n")
        user = _write(tmp_path / "src" / "u.js", "import { Svc } from './svc.js';\n")
        index = TypeIndex([_class("src.svc.Svc", svc)], SourceInspector())
        assert _resolved_name(index, _site(user, "Svc")) == "src.svc.Svc"

    def test_a_directory_import_through_index_resolves(self, tmp_path: Path):
        svc = _write(tmp_path / "src" / "lib" / "svc.ts", "export class Svc { }\n")
        user = _write(tmp_path / "src" / "u.ts", "import { Svc } from './lib/index.js';\n")
        index = TypeIndex([_class("src.lib.svc.Svc", svc)], SourceInspector())
        assert _resolved_name(index, _site(user, "Svc")) == "src.lib.svc.Svc"

    def test_an_aliased_import_with_an_extension_resolves_to_the_exported_name(self, tmp_path: Path):
        svc = _write(tmp_path / "src" / "svc.ts", "export class Svc { }\n")
        user = _write(tmp_path / "src" / "u.ts", "import { Svc as S } from './svc.js';\n")
        index = TypeIndex([_class("src.svc.Svc", svc)], SourceInspector())
        assert _resolved_name(index, _site(user, "S")) == "src.svc.Svc"

    def test_a_sibling_module_of_the_same_name_is_not_matched(self, tmp_path: Path):
        """The precision fence: dropping the extension must not degrade into basename matching."""
        elsewhere = _write(tmp_path / "a" / "svc.ts", "export class Svc { }\n")
        user = _write(tmp_path / "src" / "u.ts", "import { Svc } from './svc.js';\n")
        index = TypeIndex([_class("a.svc.Svc", elsewhere)], SourceInspector())
        assert index.resolve(_site(user, "Svc")) is None

    def test_an_unimported_name_resolves_only_when_unique(self, tmp_path: Path):
        first = _write(tmp_path / "a" / "svc.ts", "export class Svc { }\n")
        second = _write(tmp_path / "b" / "svc.ts", "export class Svc { }\n")
        user = _write(tmp_path / "user.ts", "const x = 1;\n")
        assert TypeIndex([_class("a.svc.Svc", first)], SourceInspector()).resolve(_site(user, "Svc")) is not None
        both = TypeIndex([_class("a.svc.Svc", first), _class("b.svc.Svc", second)], SourceInspector())
        assert both.resolve(_site(user, "Svc")) is None


class TestContainers:
    def test_innermost_declaration_wins(self, tmp_path: Path):
        path = tmp_path / "A.cs"
        method = _method("A.M()", path, 5, 10)
        index = TypeIndex([_class("A", path, 1, 20), method], SourceInspector())
        assert index.container_at(str(path), 7) is method
        outer = index.container_at(str(path), 3)
        assert outer is not None and outer.fully_qualified_name == "A"
        assert index.container_at(str(path), 30) is None
        assert index.container_at("elsewhere.cs", 7) is None

    def test_a_container_is_the_namespace_and_enclosing_types_or_the_module(self, tmp_path: Path):
        outer = _write(
            tmp_path / "n" / "Outer.cs", "namespace N.M;\npublic class Outer\n{\n    public class Inner { }\n}\n"
        )
        script = _write(tmp_path / "lib" / "svc.ts", "export class Svc { }\n")
        nodes = [_class("n.Outer", outer, 2, 5), _class("n.Outer.Inner", outer, 4, 4), _class("lib.svc.Svc", script)]
        index = TypeIndex(nodes, SourceInspector())
        assert [index.container_of(node) for node in nodes] == ["N.M", "N.M.Outer", str(tmp_path / "lib" / "svc")]


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
        edges = build_type_references(inspector, TypeIndex(nodes, inspector), [module, core], stats)
        assert _pairs(edges) == {
            ("app.AppModule", "volo.Core.CoreModule"),
            ("app.AppModule", "volo.Core.AbpModule"),
            ("app.AppModule.Configure(AbpOptions options)", "volo.Core.AbpOptions"),
        }
        assert (stats.sites, stats.resolved, stats.unresolved, stats.ambiguous) == (3, 3, 0, 0)

    def test_an_edge_carries_the_sites_that_made_it(self, tmp_path: Path):
        module, core, nodes = self._fixture(tmp_path)
        inspector = SourceInspector()
        edges = build_type_references(inspector, TypeIndex(nodes, inspector), [module, core], TypeReferenceStats())
        by_pair = {(edge.src, edge.dst): edge for edge in edges}
        assert by_pair[("app.AppModule", "volo.Core.CoreModule")].sites == ({"line": 3, "column": 19},)
        assert by_pair[("app.AppModule", "volo.Core.AbpModule")].sites == ({"line": 4, "column": 26},)
        assert all(edge.kind is EdgeKind.TYPEREF for edge in edges)

    def test_a_declaration_naming_its_own_type_is_not_an_edge(self, tmp_path: Path):
        path = _write(tmp_path / "A.cs", "public class A\n{\n    public A Clone(A other) { return other; }\n}\n")
        nodes = [_class("A", path, 1, 4), _method("A.Clone(A other)", path, 3, 3)]
        inspector = SourceInspector()
        stats = TypeReferenceStats()
        assert build_type_references(inspector, TypeIndex(nodes, inspector), [path], stats) == []
        assert stats.resolved == 2

    def test_script_type_references_survive_an_explicit_extension(self, tmp_path: Path):
        svc = _write(tmp_path / "src" / "svc.ts", "export class Svc { }\n")
        user = _write(
            tmp_path / "src" / "u.ts",
            "import { Svc } from './svc.js';\nexport class U { run(s: Svc) { } }\n",
        )
        nodes = [
            _class("src.svc.Svc", svc, 1, 1),
            _class("src.u.U", user, 2, 2),
            _method("src.u.U.run(s)", user, 2, 2),
        ]
        inspector = SourceInspector()
        stats = TypeReferenceStats()
        edges = build_type_references(inspector, TypeIndex(nodes, inspector), [svc, user], stats)
        assert ("src.u.U.run(s)", "src.svc.Svc") in _pairs(edges)
        assert (stats.resolved, stats.ambiguous) == (1, 0)

    def test_every_file_is_read_before_any_site_is_resolved(self, tmp_path: Path):
        """A ``global using`` declared in the last file governs a site in the first."""
        a = _write(tmp_path / "lib" / "a" / "Foo.cs", "namespace A;\npublic class Foo { }\n")
        b = _write(tmp_path / "lib" / "b" / "Foo.cs", "namespace B;\npublic class Foo { }\n")
        _write(tmp_path / "app" / "App.csproj", "<Project />\n")
        user = _write(tmp_path / "app" / "User.cs", "namespace C;\npublic class User { Foo Make() { return null; } }\n")
        globals_file = _write(tmp_path / "app" / "Usings.cs", "global using A;\n")
        nodes = [_class("a.Foo", a), _class("b.Foo", b), _class("app.User", user, 2, 2)]
        inspector = SourceInspector()
        edges = build_type_references(
            inspector, TypeIndex(nodes, inspector), [user, globals_file], TypeReferenceStats()
        )
        assert _pairs(edges) == {("app.User", "a.Foo")}

    def test_an_unreadable_file_is_reported(self, tmp_path: Path):
        module, core, nodes = self._fixture(tmp_path)
        missing = tmp_path / "gone" / "Gone.cs"
        inspector = SourceInspector()
        stats = TypeReferenceStats()
        edges = build_type_references(inspector, TypeIndex(nodes, inspector), [module, missing, core], stats)
        assert len(edges) == 3
        assert stats.unreadable_files == [str(missing)]
        assert "1 unreadable file(s)" in stats.summary()

    def test_complete_type_references_replaces_stale_edges_and_keeps_other_kinds(self, tmp_path: Path):
        module, core, nodes = self._fixture(tmp_path)
        graph = CallGraph(language="csharp")
        for node in nodes:
            graph.add_node(node)
        graph.add_reference_edge(ReferenceEdge("app.AppModule", "volo.Core.AbpOptions", EdgeKind.TYPEREF))

        stats = complete_type_references(graph, [module, core], SourceInspector())
        stats_again = complete_type_references(graph, [module, core], SourceInspector())

        typerefs = [ref for ref in graph.reference_edges if ref.kind is EdgeKind.TYPEREF]
        assert _pairs(typerefs) == {
            ("app.AppModule", "volo.Core.CoreModule"),
            ("app.AppModule", "volo.Core.AbpModule"),
            ("app.AppModule.Configure(AbpOptions options)", "volo.Core.AbpOptions"),
        }
        assert stats.edges == stats_again.edges == len(typerefs) == 3
        assert all(ref.sites for ref in typerefs)
        assert "3 sites -> 3 edges" in stats.summary()

    def test_a_base_already_recorded_as_inheritance_is_not_doubled_as_a_type_reference(self, tmp_path: Path):
        module, core, nodes = self._fixture(tmp_path)
        graph = CallGraph(language="csharp")
        for node in nodes:
            graph.add_node(node)
        inherits = ReferenceEdge("app.AppModule", "volo.Core.AbpModule", EdgeKind.INHERITS)
        graph.add_reference_edge(inherits)

        stats = complete_type_references(graph, [module, core], SourceInspector())

        typerefs = [ref for ref in graph.reference_edges if ref.kind is EdgeKind.TYPEREF]
        assert _pairs(typerefs) == {
            ("app.AppModule", "volo.Core.CoreModule"),
            ("app.AppModule.Configure(AbpOptions options)", "volo.Core.AbpOptions"),
        }
        assert inherits in graph.reference_edges
        assert stats.edges == 2
