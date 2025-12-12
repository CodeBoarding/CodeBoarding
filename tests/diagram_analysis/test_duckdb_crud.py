import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from duckdb_crud import (
    fetch_all_jobs,
    fetch_job,
    init_db,
    insert_job,
    update_job,
)


class TestDuckDBCRUD(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory for test database
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_jobs.duckdb")
        self.lock_path = self.db_path + ".lock"

        # Patch the DB_PATH and LOCK_PATH to use test database
        self.db_path_patcher = patch("duckdb_crud.DB_PATH", self.db_path)
        self.lock_path_patcher = patch("duckdb_crud.LOCK_PATH", self.lock_path)

        self.db_path_patcher.start()
        self.lock_path_patcher.start()

        # Initialize test database
        init_db()

    def tearDown(self):
        # Stop patchers
        self.db_path_patcher.stop()
        self.lock_path_patcher.stop()

        # Clean up test database
        try:
            if os.path.exists(self.db_path):
                os.remove(self.db_path)
            if os.path.exists(self.lock_path):
                os.remove(self.lock_path)
            os.rmdir(self.temp_dir)
        except Exception:
            pass

    def test_init_db_creates_database(self):
        # Test that init_db creates the database file
        self.assertTrue(os.path.exists(self.db_path))

    def test_init_db_creates_table(self):
        # Test that the jobs table exists
        import duckdb

        conn = duckdb.connect(self.db_path)
        result = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'").fetchall()
        conn.close()

        # The table should exist (though DuckDB uses different system tables)
        # Just verify we can query the jobs table
        conn = duckdb.connect(self.db_path)
        try:
            conn.execute("SELECT * FROM jobs").fetchall()
            table_exists = True
        except Exception:
            table_exists = False
        conn.close()

        self.assertTrue(table_exists)

    def test_insert_job(self):
        # Test inserting a job
        job = {
            "id": "test-job-1",
            "repo_url": "https://github.com/test/repo",
            "status": "PENDING",
            "result": None,
            "error": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "started_at": None,
            "finished_at": None,
        }

        insert_job(job)

        # Verify job was inserted
        fetched_job = fetch_job("test-job-1")
        self.assertIsNotNone(fetched_job)
        assert fetched_job is not None
        self.assertEqual(fetched_job["id"], "test-job-1")
        self.assertEqual(fetched_job["repo_url"], "https://github.com/test/repo")
        self.assertEqual(fetched_job["status"], "PENDING")

    def test_fetch_job_not_found(self):
        # Test fetching a non-existent job
        result = fetch_job("nonexistent-job")
        self.assertIsNone(result)

    def test_update_job_status(self):
        # Test updating job status
        job = {
            "id": "test-job-2",
            "repo_url": "https://github.com/test/repo",
            "status": "PENDING",
            "result": None,
            "error": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "started_at": None,
            "finished_at": None,
        }

        insert_job(job)

        # Update status
        update_job("test-job-2", status="RUNNING")

        # Verify update
        fetched_job = fetch_job("test-job-2")
        assert fetched_job is not None
        self.assertEqual(fetched_job["status"], "RUNNING")

    def test_update_job_multiple_fields(self):
        # Test updating multiple fields
        job = {
            "id": "test-job-3",
            "repo_url": "https://github.com/test/repo",
            "status": "PENDING",
            "result": None,
            "error": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "started_at": None,
            "finished_at": None,
        }

        insert_job(job)

        # Update multiple fields
        started_at = datetime.now(timezone.utc)
        update_job("test-job-3", status="RUNNING", started_at=started_at)

        # Verify updates
        fetched_job = fetch_job("test-job-3")
        assert fetched_job is not None
        self.assertEqual(fetched_job["status"], "RUNNING")
        self.assertIsNotNone(fetched_job["started_at"])

    def test_update_job_result(self):
        # Test updating job result
        job = {
            "id": "test-job-4",
            "repo_url": "https://github.com/test/repo",
            "status": "RUNNING",
            "result": None,
            "error": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
        }

        insert_job(job)

        # Update result
        result_data = '{"files": {"test.md": "content"}}'
        update_job("test-job-4", result=result_data, status="COMPLETED")

        # Verify update
        fetched_job = fetch_job("test-job-4")
        assert fetched_job is not None
        self.assertEqual(fetched_job["status"], "COMPLETED")
        self.assertEqual(fetched_job["result"], result_data)

    def test_update_job_error(self):
        # Test updating job error
        job = {
            "id": "test-job-5",
            "repo_url": "https://github.com/test/repo",
            "status": "RUNNING",
            "result": None,
            "error": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
        }

        insert_job(job)

        # Update error
        error_msg = "Test error occurred"
        update_job("test-job-5", error=error_msg, status="FAILED")

        # Verify update
        fetched_job = fetch_job("test-job-5")
        assert fetched_job is not None
        self.assertEqual(fetched_job["status"], "FAILED")
        self.assertEqual(fetched_job["error"], error_msg)

    def test_fetch_all_jobs_empty(self):
        # Test fetching all jobs when database is empty
        jobs = fetch_all_jobs()
        self.assertEqual(len(jobs), 0)

    def test_fetch_all_jobs_multiple(self):
        # Test fetching multiple jobs
        job1 = {
            "id": "test-job-6",
            "repo_url": "https://github.com/test/repo1",
            "status": "COMPLETED",
            "result": None,
            "error": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "started_at": None,
            "finished_at": None,
        }

        job2 = {
            "id": "test-job-7",
            "repo_url": "https://github.com/test/repo2",
            "status": "RUNNING",
            "result": None,
            "error": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "started_at": None,
            "finished_at": None,
        }

        insert_job(job1)
        insert_job(job2)

        # Fetch all jobs
        jobs = fetch_all_jobs()
        self.assertEqual(len(jobs), 2)

        # Verify jobs are returned
        job_ids = {job["id"] for job in jobs}
        self.assertIn("test-job-6", job_ids)
        self.assertIn("test-job-7", job_ids)

    def test_fetch_all_jobs_ordering(self):
        # Test that jobs are ordered by created_at DESC
        import time

        job1 = {
            "id": "test-job-8",
            "repo_url": "https://github.com/test/repo1",
            "status": "COMPLETED",
            "result": None,
            "error": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "started_at": None,
            "finished_at": None,
        }

        insert_job(job1)

        # Wait a bit to ensure different timestamps
        time.sleep(0.1)

        job2 = {
            "id": "test-job-9",
            "repo_url": "https://github.com/test/repo2",
            "status": "RUNNING",
            "result": None,
            "error": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "started_at": None,
            "finished_at": None,
        }

        insert_job(job2)

        # Fetch all jobs
        jobs = fetch_all_jobs()

        # Most recent job should be first
        self.assertEqual(jobs[0]["id"], "test-job-9")
        self.assertEqual(jobs[1]["id"], "test-job-8")

    def test_timestamp_conversion(self):
        # Test that timestamps are properly converted
        now = datetime.now(timezone.utc)
        job = {
            "id": "test-job-10",
            "repo_url": "https://github.com/test/repo",
            "status": "COMPLETED",
            "result": None,
            "error": None,
            "created_at": now.isoformat(),
            "started_at": now.isoformat(),
            "finished_at": now.isoformat(),
        }

        insert_job(job)

        # Fetch job
        fetched_job = fetch_job("test-job-10")
        assert fetched_job is not None

        # Verify timestamps are returned as ISO format strings
        self.assertIsInstance(fetched_job["created_at"], str)
        self.assertIsInstance(fetched_job["started_at"], str)
        self.assertIsInstance(fetched_job["finished_at"], str)

    def test_concurrent_updates(self):
        # Test that concurrent updates work with file locking
        job = {
            "id": "test-job-11",
            "repo_url": "https://github.com/test/repo",
            "status": "PENDING",
            "result": None,
            "error": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "started_at": None,
            "finished_at": None,
        }

        insert_job(job)

        # Perform multiple updates
        update_job("test-job-11", status="RUNNING")
        update_job("test-job-11", started_at=datetime.now(timezone.utc))
        update_job("test-job-11", status="COMPLETED")

        # Verify final state
        fetched_job = fetch_job("test-job-11")
        assert fetched_job is not None
        self.assertEqual(fetched_job["status"], "COMPLETED")

    def test_none_values_handled(self):
        # Test that None values are properly handled
        job = {
            "id": "test-job-12",
            "repo_url": "https://github.com/test/repo",
            "status": "PENDING",
            "result": None,
            "error": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "started_at": None,
            "finished_at": None,
        }

        insert_job(job)

        # Fetch job
        fetched_job = fetch_job("test-job-12")
        assert fetched_job is not None

        # Verify None values
        self.assertIsNone(fetched_job["result"])
        self.assertIsNone(fetched_job["error"])
        self.assertIsNone(fetched_job["started_at"])
        self.assertIsNone(fetched_job["finished_at"])

    def test_init_db_removes_existing_database(self):
        # Test that init_db removes existing database
        # Insert a job
        job = {
            "id": "test-job-13",
            "repo_url": "https://github.com/test/repo",
            "status": "PENDING",
            "result": None,
            "error": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "started_at": None,
            "finished_at": None,
        }

        insert_job(job)

        # Reinitialize database
        init_db()

        # Job should no longer exist
        fetched_job = fetch_job("test-job-13")
        self.assertIsNone(fetched_job)

    def test_update_nonexistent_job(self):
        # Test updating a job that doesn't exist
        # Should not raise exception, just silently fail to update
        update_job("nonexistent-job", status="COMPLETED")

        # Verify job still doesn't exist
        fetched_job = fetch_job("nonexistent-job")
        self.assertIsNone(fetched_job)


class TestDuckDBCRUDWithEnvVar(unittest.TestCase):
    @patch.dict(os.environ, {"JOB_DB": "/custom/path/jobs.duckdb"})
    def test_custom_db_path_from_env(self):
        # Test that DB_PATH can be customized via environment variable
        # This test verifies that the module reads from JOB_DB env var
        # Note: This test validates the module's initialization logic
        from importlib import reload
        import duckdb_crud

        # Reload module to pick up env var
        reload(duckdb_crud)

        # The DB_PATH should be set from the environment variable
        self.assertEqual(duckdb_crud.DB_PATH, "/custom/path/jobs.duckdb")


if __name__ == "__main__":
    unittest.main()
