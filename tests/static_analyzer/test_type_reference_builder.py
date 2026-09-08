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
    return TypeReferenceSite(file=str(path), line=line, column=1, name=name, qualifier=qualifier)


def _resolved_name(index: TypeIndex, site: TypeReferenceSite) -> str | None:
    node = index.resolve(site)
    return node.fully_qualified_name if node is not None else None


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

    def test_a_static_using_does_not_make_its_namespace_nameable(self, tmp_path: Path):
        """``using static A.B`` brings in B's members, not the types alongside B."""
        held = _write(tmp_path / "a" / "X.cs", "namespace A.B;\npublic class X { }\n")
        own = _write(tmp_path / "c" / "X.cs", "namespace C;\npublic class X { }\n")
        user = _write(tmp_path / "u" / "User.cs", "using static A.B;\nnamespace C;\npublic class User { }\n")
        index = TypeIndex([_class("a.X", held), _class("c.X", own)], SourceInspector())
        assert _resolved_name(index, _site(user, "X", line=3)) == "c.X"


class TestTypeIndexJava:
    """Java resolves a bare name the way C# does, so it shares the resolver and only the reader differs."""

    def _repo(self, tmp_path: Path) -> tuple[Path, list]:
        core = _write(
            tmp_path / "com" / "example" / "core" / "Widget.java",
            "package com.example.core;\npublic class Widget { }\n",
        )
        util = _write(
            tmp_path / "com" / "example" / "util" / "Widget.java",
            "package com.example.util;\npublic class Widget { }\n",
        )
        return tmp_path, [_class("com.example.core.Widget", core), _class("com.example.util.Widget", util)]

    def test_a_single_type_import_decides_the_name(self, tmp_path: Path):
        root, nodes = self._repo(tmp_path)
        user = _write(
            root / "com" / "example" / "util" / "Service.java",
            "package com.example.util;\nimport com.example.core.Widget;\npublic class Service { Widget w; }\n",
        )
        index = TypeIndex(nodes, SourceInspector())
        # Without the import the file's own package would win.
        assert _resolved_name(index, _site(user, "Widget", line=3)) == "com.example.core.Widget"

    def test_the_files_own_package_resolves_without_an_import(self, tmp_path: Path):
        root, nodes = self._repo(tmp_path)
        user = _write(
            root / "com" / "example" / "util" / "Service.java",
            "package com.example.util;\npublic class Service { Widget w; }\n",
        )
        index = TypeIndex(nodes, SourceInspector())
        assert _resolved_name(index, _site(user, "Widget", line=2)) == "com.example.util.Widget"

    def test_a_wildcard_import_makes_a_package_nameable(self, tmp_path: Path):
        root, nodes = self._repo(tmp_path)
        user = _write(
            root / "com" / "app" / "Service.java",
            "package com.app;\nimport com.example.core.*;\npublic class Service { Widget w; }\n",
        )
        index = TypeIndex(nodes, SourceInspector())
        assert _resolved_name(index, _site(user, "Widget", line=3)) == "com.example.core.Widget"

    def test_a_static_import_does_not_make_the_type_nameable(self, tmp_path: Path):
        root, nodes = self._repo(tmp_path)
        user = _write(
            root / "com" / "example" / "util" / "Service.java",
            "package com.example.util;\nimport static com.example.core.Widget.check;\n"
            "public class Service { Widget w; }\n",
        )
        index = TypeIndex(nodes, SourceInspector())
        # The static import brings in members, so the file's own package still decides.
        assert _resolved_name(index, _site(user, "Widget", line=3)) == "com.example.util.Widget"


class TestTypeIndexGo:
    def test_an_unqualified_name_is_this_files_package(self, tmp_path: Path):
        here = _write(tmp_path / "app" / "widget.go", "package app\ntype Widget struct { }\n")
        other = _write(tmp_path / "other" / "widget.go", "package other\ntype Widget struct { }\n")
        user = _write(tmp_path / "app" / "s.go", "package app\ntype S struct { w Widget }\n")
        index = TypeIndex([_class("app.widget.Widget", here), _class("other.widget.Widget", other)], SourceInspector())
        assert _resolved_name(index, _site(user, "Widget", line=2)) == "app.widget.Widget"

    def test_a_qualified_name_resolves_through_its_import(self, tmp_path: Path):
        core = _write(tmp_path / "x" / "core" / "widget.go", "package core\ntype Widget struct { }\n")
        other = _write(tmp_path / "x" / "util" / "widget.go", "package util\ntype Widget struct { }\n")
        user = _write(
            tmp_path / "app" / "s.go", 'package app\nimport "example.com/x/core"\ntype S struct { w core.Widget }\n'
        )
        index = TypeIndex(
            [_class("x.core.widget.Widget", core), _class("x.util.widget.Widget", other)], SourceInspector()
        )
        assert _resolved_name(index, _site(user, "Widget", line=3, qualifier="core")) == "x.core.widget.Widget"

    def test_an_aliased_import_resolves_through_its_local_name(self, tmp_path: Path):
        core = _write(tmp_path / "x" / "core" / "widget.go", "package core\ntype Widget struct { }\n")
        user = _write(
            tmp_path / "app" / "s.go", 'package app\nimport c "example.com/x/core"\ntype S struct { w c.Widget }\n'
        )
        index = TypeIndex([_class("x.core.widget.Widget", core)], SourceInspector())
        assert _resolved_name(index, _site(user, "Widget", line=3, qualifier="c")) == "x.core.widget.Widget"

    def test_an_unimported_qualifier_resolves_to_nothing(self, tmp_path: Path):
        core = _write(tmp_path / "x" / "core" / "widget.go", "package core\ntype Widget struct { }\n")
        user = _write(tmp_path / "app" / "s.go", "package app\ntype S struct { }\n")
        index = TypeIndex([_class("x.core.widget.Widget", core)], SourceInspector())
        assert index.resolve(_site(user, "Widget", line=2, qualifier="core")) is None


class TestTypeIndexPhp:
    def _repo(self, tmp_path: Path):
        core = _write(tmp_path / "core" / "Widget.php", "<?php\nnamespace App\\Core;\nclass Widget { }\n")
        models = _write(tmp_path / "models" / "Widget.php", "<?php\nnamespace App\\Models;\nclass Widget { }\n")
        return [_class("core.Widget.Widget", core), _class("models.Widget.Widget", models)]

    def test_a_use_clause_decides_the_name(self, tmp_path: Path):
        nodes = self._repo(tmp_path)
        user = _write(
            tmp_path / "models" / "Service.php",
            "<?php\nnamespace App\\Models;\nuse App\\Core\\Widget;\nclass Service { private Widget $w; }\n",
        )
        index = TypeIndex(nodes, SourceInspector())
        # Without the use clause the file's own namespace would win.
        assert _resolved_name(index, _site(user, "Widget", line=4)) == "core.Widget.Widget"

    def test_the_files_own_namespace_resolves_without_a_use(self, tmp_path: Path):
        nodes = self._repo(tmp_path)
        user = _write(
            tmp_path / "models" / "Service.php",
            "<?php\nnamespace App\\Models;\nclass Service { private Widget $w; }\n",
        )
        index = TypeIndex(nodes, SourceInspector())
        assert _resolved_name(index, _site(user, "Widget", line=3)) == "models.Widget.Widget"

    def test_an_aliased_use_resolves_to_its_target(self, tmp_path: Path):
        nodes = self._repo(tmp_path)
        user = _write(
            tmp_path / "models" / "Service.php",
            "<?php\nnamespace App\\Models;\nuse App\\Core\\Widget as W;\nclass Service { private W $w; }\n",
        )
        index = TypeIndex(nodes, SourceInspector())
        assert _resolved_name(index, _site(user, "W", line=4)) == "core.Widget.Widget"


class TestTypeIndexPython:
    def test_an_absolute_import_resolves_to_its_module(self, tmp_path: Path):
        widget = _write(tmp_path / "a" / "b.py", "class Widget: ...\n")
        other = _write(tmp_path / "z" / "b.py", "class Widget: ...\n")
        user = _write(tmp_path / "u.py", "from a.b import Widget\n")
        index = TypeIndex([_class("a.b.Widget", widget), _class("z.b.Widget", other)], SourceInspector())
        assert _resolved_name(index, _site(user, "Widget")) == "a.b.Widget"

    def test_a_relative_import_resolves_against_the_importing_file(self, tmp_path: Path):
        near = _write(tmp_path / "pkg" / "mod.py", "class Widget: ...\n")
        far = _write(tmp_path / "other" / "mod.py", "class Widget: ...\n")
        user = _write(tmp_path / "pkg" / "u.py", "from .mod import Widget\n")
        index = TypeIndex([_class("pkg.mod.Widget", near), _class("other.mod.Widget", far)], SourceInspector())
        assert _resolved_name(index, _site(user, "Widget")) == "pkg.mod.Widget"

    def test_a_package_resolves_through_its_init(self, tmp_path: Path):
        widget = _write(tmp_path / "a" / "b" / "__init__.py", "class Widget: ...\n")
        user = _write(tmp_path / "u.py", "from a.b import Widget\n")
        index = TypeIndex([_class("a.b.Widget", widget)], SourceInspector())
        assert _resolved_name(index, _site(user, "Widget")) == "a.b.Widget"

    def test_an_alias_resolves_to_the_exported_name(self, tmp_path: Path):
        widget = _write(tmp_path / "a" / "b.py", "class Widget: ...\n")
        user = _write(tmp_path / "u.py", "from a.b import Widget as W\n")
        index = TypeIndex([_class("a.b.Widget", widget)], SourceInspector())
        assert _resolved_name(index, _site(user, "W")) == "a.b.Widget"

    def test_a_module_import_resolves_through_its_qualifier(self, tmp_path: Path):
        widget = _write(tmp_path / "pkg" / "sub.py", "class Thing: ...\n")
        user = _write(tmp_path / "u.py", "import pkg.sub\n")
        index = TypeIndex([_class("pkg.sub.Thing", widget)], SourceInspector())
        assert _resolved_name(index, _site(user, "Thing", qualifier="pkg.sub")) == "pkg.sub.Thing"

    def test_a_third_party_import_is_external(self, tmp_path: Path):
        """A name the repository also declares must not be claimed for an installed package."""
        widget = _write(tmp_path / "mine" / "b.py", "class Widget: ...\n")
        user = _write(tmp_path / "u.py", "from django.db import Widget\n")
        index = TypeIndex([_class("mine.b.Widget", widget)], SourceInspector())
        assert index.resolve(_site(user, "Widget")) is None

    def test_a_name_with_no_import_falls_back_to_this_file(self, tmp_path: Path):
        here = _write(tmp_path / "u.py", "class Widget: ...\n")
        elsewhere = _write(tmp_path / "far" / "b.py", "class Widget: ...\n")
        index = TypeIndex([_class("u.Widget", here), _class("far.b.Widget", elsewhere)], SourceInspector())
        assert _resolved_name(index, _site(here, "Widget")) == "u.Widget"


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
        pairs = build_type_references(inspector, TypeIndex(nodes, inspector), [svc, user], stats)
        assert ("src.u.U.run(s)", "src.svc.Svc") in pairs
        assert (stats.resolved, stats.ambiguous) == (1, 0)

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
