import shutil
import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import Mock, patch

from langchain_core.language_models import BaseChatModel

from cache.meta_cache import (
    DOCS_MANIFEST_SCHEMA_VERSION,
    META_CACHE_TTL_SECONDS,
    MetaAgentCache,
    MetaCacheIdentity,
)
from utils import sha256_hexdigest


class TestMetaAgentCache(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.repo_dir = Path(self.temp_dir) / "repo"
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        self.cache = MetaAgentCache.from_repo_dir(self.repo_dir)

        self.result_json = (
            '{"project_type":"library","domain":"software",'
            '"architectural_patterns":["modular"],"expected_components":["core"],'
            '"technology_stack":["python"],"architectural_bias":"Prefer module boundaries"}'
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _build_docs_manifest(self, files: dict[str, str]) -> dict[str, object]:
        file_hashes: dict[str, str] = {}
        priority_files: list[str] = []

        for rel_path, text in sorted(files.items()):
            normalized_path = rel_path.replace("\\", "/")
            file_hashes[normalized_path] = sha256_hexdigest(text)

            rel_lower = normalized_path.lower()
            path = Path(rel_lower)
            parts = path.parts
            in_root = len(parts) == 1
            in_docs_dir = len(parts) >= 2 and parts[0] == "docs"
            is_priority_name = path.name.startswith(("readme", "contributing", "architecture"))
            is_docs_index = in_docs_dir and path.name.startswith("index.")
            is_priority = (is_priority_name and (in_root or in_docs_dir)) or is_docs_index
            if is_priority:
                priority_files.append(normalized_path)

        return {
            "v": DOCS_MANIFEST_SCHEMA_VERSION,
            "files": file_hashes,
            "priority_files": sorted(priority_files),
            "doc_extensions": [".html", ".md", ".rst"],
        }

    def _snapshot(
        self,
        docs_manifest: dict[str, object],
        deps_hash: str = "deps_v1",
        tree_hash: str = "tree_v1",
    ) -> MetaCacheIdentity:
        return MetaCacheIdentity(
            scope=str(self.repo_dir.resolve()),
            deps_hash=deps_hash,
            tree_hash=tree_hash,
            model_id="model_v1",
            prompt_version="prompt_v1",
            docs_manifest=docs_manifest,
        )

    def test_load_if_valid_invalidates_on_non_priority_docs_change_with_signature(self):
        base_docs = " ".join(f"word{i}" for i in range(1000))
        typo_docs = base_docs.replace("word500", "word50O")
        base_signature = self._build_docs_manifest({"docs/guide.md": base_docs})
        typo_signature = self._build_docs_manifest({"docs/guide.md": typo_docs})

        first = self._snapshot(base_signature)
        self.cache.save(first, self.result_json)

        second = self._snapshot(typo_signature)
        loaded = self.cache.load_if_valid(second)

        self.assertIsNone(loaded)

    def test_load_if_valid_invalidates_on_deps_change(self):
        signature = self._build_docs_manifest({"README.md": "README"})
        first = self._snapshot(signature)
        self.cache.save(first, self.result_json)

        changed_deps = self._snapshot(signature, deps_hash="deps_v2")
        loaded = self.cache.load_if_valid(changed_deps)
        self.assertIsNone(loaded)

    def test_snapshot_from_repo_uses_injected_ignore_manager(self):
        (self.repo_dir / "README.md").write_text("Sample project", encoding="utf-8")
        (self.repo_dir / "requirements.txt").write_text("pydantic==2.0.0", encoding="utf-8")

        ignore_manager = Mock()
        ignore_manager.should_ignore.return_value = False
        llm = cast(BaseChatModel, object())

        with patch("cache.meta_cache.RepoIgnoreManager") as repo_ignore_manager_ctor:
            snapshot = MetaCacheIdentity.from_repo(
                self.repo_dir, llm, ignore_manager=ignore_manager, prompt_version="test_prompt_v1"
            )

        repo_ignore_manager_ctor.assert_not_called()
        self.assertEqual(snapshot.scope, str(self.repo_dir.resolve()))
        self.assertEqual(snapshot.model_id, "object")
        self.assertEqual(snapshot.prompt_version, "test_prompt_v1")
        self.assertEqual(snapshot.docs_manifest.get("v"), DOCS_MANIFEST_SCHEMA_VERSION)
        files = snapshot.docs_manifest.get("files")
        self.assertIsInstance(files, dict)
        if isinstance(files, dict):
            self.assertEqual(len(files), 1)
            self.assertIn("README.md", files)
        priority_files = snapshot.docs_manifest.get("priority_files")
        self.assertIsInstance(priority_files, list)
        if isinstance(priority_files, list):
            self.assertIn("README.md", priority_files)

    def test_snapshot_compatibility_and_meta_payload(self):
        docs_manifest = self._build_docs_manifest({"README.md": "README"})
        snapshot = self._snapshot(docs_manifest)

        payload = snapshot.to_cache_metadata()
        self.assertTrue(snapshot.matches_cached_metadata(payload))
        self.assertEqual(payload.get("docs_manifest_schema_version"), DOCS_MANIFEST_SCHEMA_VERSION)
        self.assertIn("docs_manifest", payload)

        changed_meta = dict(payload)
        changed_meta["deps_hash"] = "deps_v2"
        self.assertFalse(snapshot.matches_cached_metadata(changed_meta))

    def test_docs_digest_ignores_file_order(self):
        first_signature = self._build_docs_manifest(
            {
                "README.md": "alpha beta gamma",
                "docs/guide.md": "overview content",
            }
        )
        second_signature = self._build_docs_manifest(
            {
                "docs/guide.md": "overview content",
                "README.md": "alpha beta gamma",
            }
        )

        self.cache.save(self._snapshot(first_signature), self.result_json)
        loaded = self.cache.load_if_valid(self._snapshot(second_signature))
        self.assertIsNotNone(loaded)

    def test_docs_digest_file_addition_invalidates_cache(self):
        base_docs = " ".join(f"word{i}" for i in range(1000))
        base_signature = self._build_docs_manifest({"docs/guide.md": base_docs})
        with_added_file = self._build_docs_manifest(
            {
                "docs/guide.md": base_docs,
                "docs/extra.md": "tiny",
            }
        )

        self.cache.save(self._snapshot(base_signature), self.result_json)
        loaded = self.cache.load_if_valid(self._snapshot(with_added_file))
        self.assertIsNone(loaded)

    def test_docs_digest_file_removal_invalidates_cache(self):
        first_signature = self._build_docs_manifest(
            {
                "docs/guide.md": "guide content",
                "docs/extra.md": "extra content",
            }
        )
        second_signature = self._build_docs_manifest({"docs/guide.md": "guide content"})

        self.cache.save(self._snapshot(first_signature), self.result_json)
        loaded = self.cache.load_if_valid(self._snapshot(second_signature))
        self.assertIsNone(loaded)

    def test_priority_doc_change_invalidates_cache(self):
        first_signature = self._build_docs_manifest(
            {
                "README.md": "project intro",
                "docs/guide.md": "guide text",
            }
        )
        second_signature = self._build_docs_manifest(
            {
                "README.md": "project intro changed",
                "docs/guide.md": "guide text",
            }
        )

        self.cache.save(self._snapshot(first_signature), self.result_json)
        loaded = self.cache.load_if_valid(self._snapshot(second_signature))
        self.assertIsNone(loaded)

    def test_legacy_signature_version_is_invalidated(self):
        legacy_signature = {
            "v": DOCS_MANIFEST_SCHEMA_VERSION - 1,
            "files": {"docs/guide.md": sha256_hexdigest("legacy guide")},
        }
        current_signature = self._build_docs_manifest({"docs/guide.md": "legacy guide"})

        self.cache.save(self._snapshot(legacy_signature), self.result_json)
        loaded = self.cache.load_if_valid(self._snapshot(current_signature))
        self.assertIsNone(loaded)

    def test_cache_entry_expires_after_ttl_without_access(self):
        signature = self._build_docs_manifest({"docs/guide.md": "guide"})
        snapshot = self._snapshot(signature)
        created_at = 1_700_000_000

        with patch("cache._sqlite_store.time.time", return_value=created_at):
            self.cache.save(snapshot, self.result_json)

        expired_at = created_at + META_CACHE_TTL_SECONDS + 1
        with patch("cache._sqlite_store.time.time", return_value=expired_at):
            loaded = self.cache.load_if_valid(snapshot)
        self.assertIsNone(loaded)

    def test_cache_hit_refreshes_last_access_for_ttl(self):
        signature = self._build_docs_manifest({"docs/guide.md": "guide"})
        snapshot = self._snapshot(signature)
        created_at = 1_700_000_000

        with patch("cache._sqlite_store.time.time", return_value=created_at):
            self.cache.save(snapshot, self.result_json)

        first_access = created_at + META_CACHE_TTL_SECONDS - 100
        with patch("cache._sqlite_store.time.time", return_value=first_access):
            first_loaded = self.cache.load_if_valid(snapshot)
        self.assertIsNotNone(first_loaded)

        after_original_expiry = created_at + META_CACHE_TTL_SECONDS + 100
        with patch("cache._sqlite_store.time.time", return_value=after_original_expiry):
            second_loaded = self.cache.load_if_valid(snapshot)
        self.assertIsNotNone(second_loaded)


if __name__ == "__main__":
    unittest.main()
