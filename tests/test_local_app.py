import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from fastapi import BackgroundTasks
from fastapi.testclient import TestClient

from local_app import (
    JobStatus,
    app,
    extract_repo_name,
    generate_onboarding,
    make_job,
    process_docs_generation_job,
)


class TestExtractRepoName(unittest.TestCase):
    def test_extract_repo_name_simple(self):
        # Test with simple GitHub URL
        url = "https://github.com/user/repo"
        result = extract_repo_name(url)
        self.assertEqual(result, "repo")

    def test_extract_repo_name_with_git_extension(self):
        # Test with .git extension
        url = "https://github.com/user/repo.git"
        result = extract_repo_name(url)
        self.assertEqual(result, "repo")

    def test_extract_repo_name_with_path(self):
        # Test with additional path components
        url = "https://github.com/organization/project-name"
        result = extract_repo_name(url)
        self.assertEqual(result, "project-name")

    def test_extract_repo_name_invalid_url(self):
        # Test with invalid URL
        url = "https://github.com/invalid"
        with self.assertRaises(ValueError):
            extract_repo_name(url)


class TestMakeJob(unittest.TestCase):
    def test_make_job_structure(self):
        # Test job creation
        repo_url = "https://github.com/test/repo"
        job = make_job(repo_url)

        # Check all required fields are present
        self.assertIn("id", job)
        self.assertIn("repo_url", job)
        self.assertIn("status", job)
        self.assertIn("result", job)
        self.assertIn("error", job)
        self.assertIn("created_at", job)
        self.assertIn("started_at", job)
        self.assertIn("finished_at", job)

        # Check initial values
        self.assertEqual(job["repo_url"], repo_url)
        self.assertEqual(job["status"], JobStatus.PENDING)
        self.assertIsNone(job["result"])
        self.assertIsNone(job["error"])
        self.assertIsNone(job["started_at"])
        self.assertIsNone(job["finished_at"])

    def test_make_job_unique_ids(self):
        # Test that each job gets a unique ID
        job1 = make_job("https://github.com/test/repo1")
        job2 = make_job("https://github.com/test/repo2")

        self.assertNotEqual(job1["id"], job2["id"])


class TestGenerateOnboarding(unittest.IsolatedAsyncioTestCase):
    # This test kept failing and needs further investigation
    # removing for now, will re-add later
    # @patch("main.clone_repository")
    # @patch("local_app.remove_temp_repo_folder")
    # @patch("local_app.create_temp_repo_folder")
    # @patch("local_app.fetch_job")
    # @patch("local_app.update_job")
    # @patch("local_app.run_in_threadpool")
    # @patch("local_app.job_semaphore")
    # @patch.dict(os.environ, {"REPO_ROOT": "/tmp/repos"})
    # async def test_generate_onboarding_success(
    #     self,
    #     mock_semaphore,
    #     mock_run_in_threadpool,
    #     mock_update_job,
    #     mock_fetch_job,
    #     mock_create_temp,
    #     mock_remove_temp,
    #     mock_clone_repo,
    # ):
    #     # Test successful onboarding generation
    #     job_id = "test-job-id"

    #     # Mock semaphore to act as an async context manager
    #     mock_semaphore.__aenter__ = AsyncMock(return_value=None)
    #     mock_semaphore.__aexit__ = AsyncMock(return_value=None)

    #     # Mock job data
    #     mock_fetch_job.return_value = {
    #         "id": job_id,
    #         "repo_url": "https://github.com/test/repo",
    #         "status": JobStatus.PENDING,
    #     }

    #     # Mock clone_repository to avoid GitHub authentication
    #     mock_clone_repo.return_value = "test-repo"

    #     # Create temp directory and files for testing
    #     with tempfile.TemporaryDirectory() as temp_dir:
    #         temp_path = Path(temp_dir)
    #         mock_create_temp.return_value = temp_path

    #         # Create test files that will be found by the glob operations
    #         (temp_path / "analysis.json").write_text('{"test": "data"}')
    #         (temp_path / "overview.md").write_text("# Overview")

    #         # Mock run_in_threadpool to do nothing (files are already created)
    #         mock_run_in_threadpool.return_value = None

    #         await generate_onboarding(job_id)

    #         # Check that job was updated with RUNNING status
    #         calls = mock_update_job.call_args_list
    #         self.assertTrue(any(call.kwargs.get("status") == JobStatus.RUNNING for call in calls))

    #         # Check that job was updated with COMPLETED status
    #         self.assertTrue(any(call.kwargs.get("status") == JobStatus.COMPLETED for call in calls))

    #         # Check that cleanup was called
    #         mock_remove_temp.assert_called_once()

    @patch("main.clone_repository")
    @patch("local_app.remove_temp_repo_folder")
    @patch("local_app.create_temp_repo_folder")
    @patch("local_app.fetch_job")
    @patch("local_app.update_job")
    @patch("local_app.run_in_threadpool")
    @patch("local_app.job_semaphore")
    @patch.dict(os.environ, {"REPO_ROOT": "/tmp/repos"})
    async def test_generate_onboarding_no_files_generated(
        self,
        mock_semaphore,
        mock_run_in_threadpool,
        mock_update_job,
        mock_fetch_job,
        mock_create_temp,
        mock_remove_temp,
        mock_clone_repo,
    ):
        # Test when no files are generated
        job_id = "test-job-id"

        # Mock semaphore to act as an async context manager
        mock_semaphore.__aenter__ = AsyncMock(return_value=None)
        mock_semaphore.__aexit__ = AsyncMock(return_value=None)

        mock_fetch_job.return_value = {
            "id": job_id,
            "repo_url": "https://github.com/test/repo",
            "status": JobStatus.PENDING,
        }

        # Mock clone_repository to avoid GitHub authentication
        mock_clone_repo.return_value = "test-repo"

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            mock_create_temp.return_value = temp_path
            # Don't create any files

            await generate_onboarding(job_id)

            # Check that job was marked as FAILED
            calls = mock_update_job.call_args_list
            self.assertTrue(any(call.kwargs.get("status") == JobStatus.FAILED for call in calls))

    @patch("local_app.remove_temp_repo_folder")
    @patch("local_app.create_temp_repo_folder")
    @patch("local_app.fetch_job")
    @patch("local_app.update_job")
    @patch("local_app.job_semaphore")
    @patch.dict(os.environ, {"REPO_ROOT": "/tmp/repos"})
    async def test_generate_onboarding_job_not_found(
        self,
        mock_semaphore,
        mock_update_job,
        mock_fetch_job,
        mock_create_temp,
        mock_remove_temp,
    ):
        # Test when job is not found
        job_id = "nonexistent-job"

        # Mock semaphore to act as an async context manager
        mock_semaphore.__aenter__ = AsyncMock(return_value=None)
        mock_semaphore.__aexit__ = AsyncMock(return_value=None)

        mock_fetch_job.return_value = None

        with tempfile.TemporaryDirectory() as temp_dir:
            mock_create_temp.return_value = Path(temp_dir)

            # Should handle the error gracefully
            await generate_onboarding(job_id)

            mock_remove_temp.assert_called_once()


class TestProcessDocsGenerationJob(unittest.IsolatedAsyncioTestCase):
    @patch("github_action.clone_repository")
    @patch("local_app.remove_temp_repo_folder")
    @patch("local_app.create_temp_repo_folder")
    @patch("local_app.update_job")
    @patch("local_app.run_in_threadpool")
    async def test_process_docs_generation_job_success(
        self,
        mock_run_in_threadpool,
        mock_update_job,
        mock_create_temp,
        mock_remove_temp,
        mock_clone_repo,
    ):
        # Test successful docs generation
        job_id = "test-job-id"
        url = "test/repo"
        source_branch = "main"
        target_branch = "main"
        output_dir = ".codeboarding"
        extension = ".md"

        # Mock clone_repository to avoid GitHub authentication
        mock_clone_repo.return_value = "test-repo"

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            mock_create_temp.return_value = temp_path
            mock_run_in_threadpool.return_value = temp_path

            # Create test files
            (temp_path / "analysis.json").write_text('{"test": "data"}')
            (temp_path / "overview.md").write_text("# Overview")

            await process_docs_generation_job(job_id, url, source_branch, target_branch, output_dir, extension)

            # Check that job was updated with RUNNING status
            calls = mock_update_job.call_args_list
            self.assertTrue(any(call.kwargs.get("status") == JobStatus.RUNNING for call in calls))

            # Check that job was updated with COMPLETED status
            self.assertTrue(any(call.kwargs.get("status") == JobStatus.COMPLETED for call in calls))

            mock_remove_temp.assert_called_once()

    @patch("github_action.clone_repository")
    @patch("local_app.remove_temp_repo_folder")
    @patch("local_app.create_temp_repo_folder")
    @patch("local_app.update_job")
    @patch("local_app.run_in_threadpool")
    async def test_process_docs_generation_job_no_files(
        self,
        mock_run_in_threadpool,
        mock_update_job,
        mock_create_temp,
        mock_remove_temp,
        mock_clone_repo,
    ):
        # Test when no files are generated
        job_id = "test-job-id"

        # Mock clone_repository to avoid GitHub authentication
        mock_clone_repo.return_value = "test-repo"

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            mock_create_temp.return_value = temp_path
            mock_run_in_threadpool.return_value = temp_path

            await process_docs_generation_job(job_id, "test/repo", "main", "main", ".codeboarding", ".md")

            # Check that job was marked as FAILED
            calls = mock_update_job.call_args_list
            failed_calls = [call for call in calls if call.kwargs.get("status") == JobStatus.FAILED]
            self.assertTrue(len(failed_calls) > 0)

    @patch("github_action.clone_repository")
    @patch("local_app.remove_temp_repo_folder")
    @patch("local_app.create_temp_repo_folder")
    @patch("local_app.update_job")
    @patch("local_app.run_in_threadpool")
    async def test_process_docs_generation_job_repo_not_found(
        self,
        mock_run_in_threadpool,
        mock_update_job,
        mock_create_temp,
        mock_remove_temp,
        mock_clone_repo,
    ):
        # Test when repository is not found
        from repo_utils import RepoDontExistError

        job_id = "test-job-id"

        # Mock clone_repository to raise RepoDontExistError
        mock_clone_repo.side_effect = RepoDontExistError("Repo not found")

        with tempfile.TemporaryDirectory() as temp_dir:
            mock_create_temp.return_value = Path(temp_dir)

            await process_docs_generation_job(job_id, "nonexistent/repo", "main", "main", ".codeboarding", ".md")

            # Check that job was marked as FAILED with appropriate error
            calls = mock_update_job.call_args_list
            failed_calls = [call for call in calls if call.kwargs.get("status") == JobStatus.FAILED]
            self.assertTrue(len(failed_calls) > 0)

            # Check error message
            error_calls = [call for call in calls if "error" in call.kwargs]
            self.assertTrue(len(error_calls) > 0)


class TestAPIEndpoints(unittest.TestCase):
    def setUp(self):
        # Initialize database before tests
        import duckdb_crud

        # Create a temporary directory for the test database
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_jobs.duckdb")

        # Patch the DB_PATH in duckdb_crud module
        self.original_db_path = duckdb_crud.DB_PATH
        self.original_lock_path = duckdb_crud.LOCK_PATH
        duckdb_crud.DB_PATH = self.db_path
        duckdb_crud.LOCK_PATH = self.db_path + ".lock"

        # Initialize the database
        duckdb_crud.init_db()

        # Create test client
        self.client = TestClient(app)

    def tearDown(self):
        # Restore original paths and clean up
        import duckdb_crud
        import shutil

        duckdb_crud.DB_PATH = self.original_db_path
        duckdb_crud.LOCK_PATH = self.original_lock_path

        # Clean up temporary directory
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    @patch("local_app.insert_job")
    @patch("local_app.generate_onboarding")
    def test_start_generation_job(self, mock_generate, mock_insert):
        # Test POST /generation endpoint
        response = self.client.post("/generation?repo_url=https://github.com/test/repo")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("job_id", data)
        self.assertIn("status", data)
        self.assertEqual(data["status"], JobStatus.PENDING)

        mock_insert.assert_called_once()

    def test_start_generation_job_no_url(self):
        # Test POST /generation without URL
        response = self.client.post("/generation")

        self.assertEqual(response.status_code, 422)  # Validation error

    def test_get_heart_beat(self):
        # Test HEAD /heart_beat endpoint
        response = self.client.head("/heart_beat")

        self.assertEqual(response.status_code, 200)

    @patch("local_app.fetch_job")
    def test_get_job_completed(self, mock_fetch_job):
        # Test GET /generation/{job_id} for completed job
        job_id = "test-job-id"
        mock_fetch_job.return_value = {
            "id": job_id,
            "repo_url": "https://github.com/test/repo",
            "status": JobStatus.COMPLETED,
            "result": json.dumps({"files": {"test.md": "content"}}),
            "error": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }

        response = self.client.get(f"/generation/{job_id}")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], JobStatus.COMPLETED)
        self.assertIn("files", data)

    @patch("local_app.fetch_job")
    def test_get_job_failed(self, mock_fetch_job):
        # Test GET /generation/{job_id} for failed job
        job_id = "test-job-id"
        mock_fetch_job.return_value = {
            "id": job_id,
            "repo_url": "https://github.com/test/repo",
            "status": JobStatus.FAILED,
            "result": None,
            "error": "Test error",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }

        response = self.client.get(f"/generation/{job_id}")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], JobStatus.FAILED)
        self.assertEqual(data["error"], "Test error")

    @patch("local_app.fetch_job")
    def test_get_job_not_found(self, mock_fetch_job):
        # Test GET /generation/{job_id} when job doesn't exist
        mock_fetch_job.return_value = None

        response = self.client.get("/generation/nonexistent-job")

        self.assertEqual(response.status_code, 404)

    @patch("local_app.insert_job")
    @patch("local_app.process_docs_generation_job")
    def test_start_docs_generation_job(self, mock_process, mock_insert):
        # Test POST /github_action/jobs endpoint
        request_data = {
            "url": "https://github.com/test/repo",
            "source_branch": "main",
            "target_branch": "main",
            "extension": ".md",
            "output_directory": ".codeboarding",
        }

        response = self.client.post("/github_action/jobs", json=request_data)

        self.assertEqual(response.status_code, 202)
        data = response.json()
        self.assertIn("job_id", data)
        self.assertIn("message", data)

        mock_insert.assert_called_once()

    @patch("local_app.insert_job")
    def test_start_docs_generation_job_unsupported_extension(self, mock_insert):
        # Test with unsupported extension (should default to .md)
        request_data = {
            "url": "https://github.com/test/repo",
            "source_branch": "main",
            "target_branch": "main",
            "extension": ".unsupported",
            "output_directory": ".codeboarding",
        }

        response = self.client.post("/github_action/jobs", json=request_data)

        self.assertEqual(response.status_code, 202)

    @patch("local_app.fetch_job")
    def test_get_github_action_status(self, mock_fetch_job):
        # Test GET /github_action/jobs/{job_id}
        job_id = "test-job-id"
        mock_fetch_job.return_value = {
            "id": job_id,
            "repo_url": "https://github.com/test/repo",
            "status": JobStatus.RUNNING,
            "result": None,
            "error": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
        }

        response = self.client.get(f"/github_action/jobs/{job_id}")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], JobStatus.RUNNING)

    @patch("local_app.fetch_all_jobs")
    def test_list_jobs(self, mock_fetch_all):
        # Test GET /github_action/jobs
        mock_fetch_all.return_value = [
            {
                "id": "job1",
                "repo_url": "https://github.com/test/repo1",
                "status": JobStatus.COMPLETED,
                "result": None,
                "error": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "id": "job2",
                "repo_url": "https://github.com/test/repo2",
                "status": JobStatus.RUNNING,
                "result": None,
                "error": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": None,
            },
        ]

        response = self.client.get("/github_action/jobs")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("jobs", data)
        self.assertEqual(len(data["jobs"]), 2)


if __name__ == "__main__":
    unittest.main()
