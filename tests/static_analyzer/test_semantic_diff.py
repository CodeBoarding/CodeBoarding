import subprocess
from pathlib import Path

import pytest

from static_analyzer.constants import Language, SOURCE_EXTENSION_TO_LANGUAGE
from static_analyzer.cosmetic_diff import is_file_cosmetic, strip_comments_from_source
from static_analyzer.tree_sitter_parsers import (
    _LANG_CONFIGS,
    _get_parser,
    _to_normalized_tuple,
    _to_structural_tuple,
)


def _init_git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, capture_output=True, check=True)
    return tmp_path


def _commit_file(repo: Path, file_path: str, content: str, message: str = "c") -> str:
    full = repo / file_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    subprocess.run(["git", "add", file_path], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", message], cwd=repo, capture_output=True, check=True)
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _parse(language: Language, ext: str, source: bytes):
    parser = _get_parser(language, ext)
    assert parser is not None
    return parser.parse(source)


@pytest.mark.parametrize(
    "ext,expected",
    [
        (".py", Language.PYTHON),
        (".ts", Language.TYPESCRIPT),
        (".tsx", Language.TYPESCRIPT),
        (".mts", Language.TYPESCRIPT),
        (".js", Language.JAVASCRIPT),
        (".jsx", Language.JAVASCRIPT),
        (".mjs", Language.JAVASCRIPT),
        (".go", Language.GO),
        (".java", Language.JAVA),
        (".php", Language.PHP),
    ],
)
def test_extension_to_language(ext, expected):
    assert SOURCE_EXTENSION_TO_LANGUAGE[ext] == expected


@pytest.mark.parametrize("ext", [".rb", ".swift"])
def test_unsupported_extension(ext):
    assert ext not in SOURCE_EXTENSION_TO_LANGUAGE


class TestParserLoading:
    @pytest.mark.parametrize(
        "lang,ext",
        [
            (Language.PYTHON, ".py"),
            (Language.JAVASCRIPT, ".js"),
            (Language.TYPESCRIPT, ".ts"),
            (Language.TYPESCRIPT, ".tsx"),
            (Language.GO, ".go"),
            (Language.JAVA, ".java"),
            (Language.PHP, ".php"),
        ],
    )
    def test_parser_loads(self, lang, ext):
        assert _get_parser(lang, ext) is not None

    def test_unsupported_language_returns_none(self):
        assert _get_parser(Language.CPP, ".cpp") is None


class TestCommentStripping:
    def test_strip_comments_from_source_python(self):
        source = "def foo():\n    # comment\n    return 1\n"
        stripped = strip_comments_from_source("foo.py", source)
        assert "# comment" not in stripped
        assert "return 1" in stripped

    def test_strip_comments_from_source_python_removes_docstring(self):
        source = 'def foo():\n    """old docstring"""\n    return 1\n'
        stripped = strip_comments_from_source("foo.py", source)
        assert '"""old docstring"""' not in stripped
        assert "return 1" in stripped

    def test_strip_comments_unsupported_extension_returns_original(self):
        source = "fn main() { // comment\n}\n"
        assert strip_comments_from_source("main.rs", source) == source


class TestTier0:
    @pytest.mark.parametrize(
        "lang,ext,old,new",
        [
            (
                Language.PYTHON,
                ".py",
                b"def foo():\n    # old\n    return 1\n",
                b"def foo():\n    # new\n    return 1\n",
            ),
            (
                Language.JAVASCRIPT,
                ".js",
                b"// old\nfunction foo() { return 1; }\n",
                b"// new\nfunction foo() { return 1; }\n",
            ),
            (
                Language.TYPESCRIPT,
                ".ts",
                b"// old\nfunction foo(): number { return 1; }\n",
                b"// new\nfunction foo(): number { return 1; }\n",
            ),
            (
                Language.GO,
                ".go",
                b"package m\n// old\nfunc foo() int { return 1 }\n",
                b"package m\n// new\nfunc foo() int { return 1 }\n",
            ),
            (
                Language.JAVA,
                ".java",
                b"class F { /* old */ int foo() { return 1; } }\n",
                b"class F { /* new */ int foo() { return 1; } }\n",
            ),
            (
                Language.PHP,
                ".php",
                b"<?php\n// old\nfunction foo() { return 1; }\n",
                b"<?php\n// new\nfunction foo() { return 1; }\n",
            ),
        ],
    )
    def test_comment_change_is_cosmetic(self, lang, ext, old, new):
        old_tree = _parse(lang, ext, old)
        new_tree = _parse(lang, ext, new)
        assert _to_structural_tuple(old_tree.root_node) == _to_structural_tuple(new_tree.root_node)

    def test_whitespace_change_python(self):
        old = _parse(Language.PYTHON, ".py", b"def foo():\n    return 1\n")
        new = _parse(Language.PYTHON, ".py", b"def foo():\n    return 1\n\n\n")
        # Trailing whitespace doesn't produce AST nodes, so should be equal
        assert _to_structural_tuple(old.root_node) == _to_structural_tuple(new.root_node)

    def test_variable_rename_is_not_tier0(self):
        old = _parse(Language.PYTHON, ".py", b"x = 1\n")
        new = _parse(Language.PYTHON, ".py", b"y = 1\n")
        assert _to_structural_tuple(old.root_node) != _to_structural_tuple(new.root_node)

    def test_added_statement_is_not_tier0(self):
        old = _parse(Language.PYTHON, ".py", b"x = 1\n")
        new = _parse(Language.PYTHON, ".py", b"x = 1\ny = 2\n")
        assert _to_structural_tuple(old.root_node) != _to_structural_tuple(new.root_node)


class TestTier1:
    @pytest.mark.parametrize(
        "lang,ext,old,new",
        [
            (Language.PYTHON, ".py", b"x = 1\n", b"y = 1\n"),
            (Language.JAVASCRIPT, ".js", b"const x = 1;\n", b"const y = 1;\n"),
            (Language.TYPESCRIPT, ".ts", b"const x: number = 1;\n", b"const y: number = 1;\n"),
            (Language.GO, ".go", b"package m\nvar x = 1\n", b"package m\nvar y = 1\n"),
            (Language.JAVA, ".java", b"class F { int x = 1; }\n", b"class F { int y = 1; }\n"),
            (Language.PHP, ".php", b"<?php\n$x = 1;\n", b"<?php\n$y = 1;\n"),
        ],
    )
    def test_module_level_rename_is_semantic(self, lang, ext, old, new):
        config = _LANG_CONFIGS[lang]
        old_tree = _parse(lang, ext, old)
        new_tree = _parse(lang, ext, new)
        old_norm = _to_normalized_tuple(old_tree.root_node, config, {}, [0])
        new_norm = _to_normalized_tuple(new_tree.root_node, config, {}, [0])
        assert old_norm != new_norm

    @pytest.mark.parametrize(
        "lang,ext,old,new",
        [
            (Language.PYTHON, ".py", b"def f():\n    x = 1\n", b"def f():\n    y = 1\n"),
            (Language.JAVASCRIPT, ".js", b"function f() { const x = 1; }\n", b"function f() { const y = 1; }\n"),
            (
                Language.TYPESCRIPT,
                ".ts",
                b"function f() { const x: number = 1; }\n",
                b"function f() { const y: number = 1; }\n",
            ),
            (
                Language.JAVA,
                ".java",
                b"class F { void f() { int x = 1; } }\n",
                b"class F { void f() { int y = 1; } }\n",
            ),
            (
                Language.PHP,
                ".php",
                b"<?php\nfunction f() { $x = 1; }\n",
                b"<?php\nfunction f() { $y = 1; }\n",
            ),
        ],
    )
    def test_local_variable_rename_is_cosmetic(self, lang, ext, old, new):
        config = _LANG_CONFIGS[lang]
        old_tree = _parse(lang, ext, old)
        new_tree = _parse(lang, ext, new)
        old_norm = _to_normalized_tuple(old_tree.root_node, config, {}, [0])
        new_norm = _to_normalized_tuple(new_tree.root_node, config, {}, [0])
        assert old_norm == new_norm

    @pytest.mark.parametrize(
        "lang,ext,old,new",
        [
            (Language.PYTHON, ".py", b"z = a + b\n", b"z = b + a\n"),
            (Language.JAVASCRIPT, ".js", b"const z = a + b;\n", b"const z = b + a;\n"),
            (Language.GO, ".go", b"package m\nvar z = a + b\n", b"package m\nvar z = b + a\n"),
            (Language.JAVA, ".java", b"class F { int z = a + b; }\n", b"class F { int z = b + a; }\n"),
            (Language.PHP, ".php", b"<?php\n$z = $a + $b;\n", b"<?php\n$z = $b + $a;\n"),
        ],
    )
    def test_addition_reorder_is_semantic(self, lang, ext, old, new):
        config = _LANG_CONFIGS[lang]
        old_tree = _parse(lang, ext, old)
        new_tree = _parse(lang, ext, new)
        old_norm = _to_normalized_tuple(old_tree.root_node, config, {}, [0])
        new_norm = _to_normalized_tuple(new_tree.root_node, config, {}, [0])
        assert old_norm != new_norm

    def test_multiplication_reorder_is_cosmetic(self):
        config = _LANG_CONFIGS[Language.PYTHON]
        old = _parse(Language.PYTHON, ".py", b"z = a * b\n")
        new = _parse(Language.PYTHON, ".py", b"z = b * a\n")
        old_norm = _to_normalized_tuple(old.root_node, config, {}, [0])
        new_norm = _to_normalized_tuple(new.root_node, config, {}, [0])
        assert old_norm == new_norm

    def test_equality_reorder_is_cosmetic(self):
        config = _LANG_CONFIGS[Language.PYTHON]
        old = _parse(Language.PYTHON, ".py", b"z = a == b\n")
        new = _parse(Language.PYTHON, ".py", b"z = b == a\n")
        old_norm = _to_normalized_tuple(old.root_node, config, {}, [0])
        new_norm = _to_normalized_tuple(new.root_node, config, {}, [0])
        assert old_norm == new_norm

    def test_value_change_is_semantic(self):
        config = _LANG_CONFIGS[Language.PYTHON]
        old = _parse(Language.PYTHON, ".py", b"x = 1\n")
        new = _parse(Language.PYTHON, ".py", b"x = 2\n")
        old_norm = _to_normalized_tuple(old.root_node, config, {}, [0])
        new_norm = _to_normalized_tuple(new.root_node, config, {}, [0])
        assert old_norm != new_norm

    def test_added_statement_is_semantic(self):
        config = _LANG_CONFIGS[Language.PYTHON]
        old = _parse(Language.PYTHON, ".py", b"x = 1\n")
        new = _parse(Language.PYTHON, ".py", b"x = 1\ny = 2\n")
        old_norm = _to_normalized_tuple(old.root_node, config, {}, [0])
        new_norm = _to_normalized_tuple(new.root_node, config, {}, [0])
        assert old_norm != new_norm

    def test_non_commutative_reorder_is_semantic(self):
        config = _LANG_CONFIGS[Language.PYTHON]
        old = _parse(Language.PYTHON, ".py", b"z = a - b\n")
        new = _parse(Language.PYTHON, ".py", b"z = b - a\n")
        old_norm = _to_normalized_tuple(old.root_node, config, {}, [0])
        new_norm = _to_normalized_tuple(new.root_node, config, {}, [0])
        assert old_norm != new_norm

    def test_logical_operator_reorder_is_semantic(self):
        config = _LANG_CONFIGS[Language.PYTHON]
        old = _parse(Language.PYTHON, ".py", b"z = x and y\n")
        new = _parse(Language.PYTHON, ".py", b"z = y and x\n")
        old_norm = _to_normalized_tuple(old.root_node, config, {}, [0])
        new_norm = _to_normalized_tuple(new.root_node, config, {}, [0])
        assert old_norm != new_norm


class TestIsFileCosmetic:
    def test_comment_only_change(self, tmp_path):
        repo = _init_git_repo(tmp_path)
        base = _commit_file(repo, "src/foo.py", "def foo():\n    # old\n    return 1\n")
        (repo / "src/foo.py").write_text("def foo():\n    # new\n    return 1\n")
        assert is_file_cosmetic(repo, base, "src/foo.py") is True

    def test_python_docstring_change_is_cosmetic(self, tmp_path):
        repo = _init_git_repo(tmp_path)
        base = _commit_file(repo, "src/foo.py", 'def foo():\n    """old docstring"""\n    return 1\n')
        (repo / "src/foo.py").write_text('def foo():\n    """new docstring"""\n    return 1\n')
        assert is_file_cosmetic(repo, base, "src/foo.py") is True

    def test_semantic_change(self, tmp_path):
        repo = _init_git_repo(tmp_path)
        base = _commit_file(repo, "src/foo.py", "def foo():\n    return 1\n")
        (repo / "src/foo.py").write_text("def foo():\n    return 2\n")
        assert is_file_cosmetic(repo, base, "src/foo.py") is False

    def test_module_level_variable_rename_is_semantic(self, tmp_path):
        repo = _init_git_repo(tmp_path)
        base = _commit_file(repo, "src/foo.py", "x = 1\n")
        (repo / "src/foo.py").write_text("y = 1\n")
        assert is_file_cosmetic(repo, base, "src/foo.py") is False

    def test_local_variable_rename_is_cosmetic(self, tmp_path):
        repo = _init_git_repo(tmp_path)
        base = _commit_file(repo, "src/foo.py", "def f():\n    x = 1\n")
        (repo / "src/foo.py").write_text("def f():\n    y = 1\n")
        assert is_file_cosmetic(repo, base, "src/foo.py") is True

    def test_unsupported_extension(self, tmp_path):
        repo = _init_git_repo(tmp_path)
        base = _commit_file(repo, "src/foo.rs", "fn foo() {}\n")
        (repo / "src/foo.rs").write_text("fn foo() { /* comment */ }\n")
        assert is_file_cosmetic(repo, base, "src/foo.rs") is False

    def test_missing_old_file(self, tmp_path):
        repo = _init_git_repo(tmp_path)
        _commit_file(repo, "dummy.txt", "d")
        (repo / "src").mkdir(parents=True)
        (repo / "src/new.py").write_text("x = 1\n")
        base = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        assert is_file_cosmetic(repo, base, "src/new.py") is False

    def test_identical_content(self, tmp_path):
        repo = _init_git_repo(tmp_path)
        base = _commit_file(repo, "src/foo.py", "x = 1\n")
        # Content unchanged
        assert is_file_cosmetic(repo, base, "src/foo.py") is True

    def test_parse_error_is_conservative(self, tmp_path):
        repo = _init_git_repo(tmp_path)
        base = _commit_file(repo, "src/foo.py", "def foo():\n    return 1\n")
        (repo / "src/foo.py").write_text("def foo(\n")
        assert is_file_cosmetic(repo, base, "src/foo.py") is False

    def test_javascript_comment_change(self, tmp_path):
        repo = _init_git_repo(tmp_path)
        base = _commit_file(repo, "src/app.js", "// old\nfunction foo() { return 1; }\n")
        (repo / "src/app.js").write_text("// new\nfunction foo() { return 1; }\n")
        assert is_file_cosmetic(repo, base, "src/app.js") is True

    def test_go_comment_change(self, tmp_path):
        repo = _init_git_repo(tmp_path)
        base = _commit_file(repo, "main.go", "package main\n// old\nfunc foo() {}\n")
        (repo / "main.go").write_text("package main\n// new\nfunc foo() {}\n")
        assert is_file_cosmetic(repo, base, "main.go") is True

    def test_missing_new_file(self, tmp_path):
        repo = _init_git_repo(tmp_path)
        base = _commit_file(repo, "src/foo.py", "x = 1\n")
        (repo / "src/foo.py").unlink()
        assert is_file_cosmetic(repo, base, "src/foo.py") is False

    def test_non_utf8_old_content_returns_false(self, tmp_path):
        repo = _init_git_repo(tmp_path)
        # Commit a latin-1 encoded file via raw bytes
        src = repo / "src"
        src.mkdir(parents=True, exist_ok=True)
        (src / "foo.py").write_bytes(b"x = '\xe9'\n")  # \xe9 = é in latin-1
        subprocess.run(["git", "add", "src/foo.py"], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "c"], cwd=repo, capture_output=True, check=True)
        base = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        # Modify the file
        (src / "foo.py").write_bytes(b"y = '\xe9'\n")
        assert is_file_cosmetic(repo, base, "src/foo.py") is False
