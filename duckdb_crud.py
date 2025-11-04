from filelock import FileLock
import duckdb
from typing import Optional
import os

DB_PATH = os.getenv("JOB_DB", "jobs.duckdb")
LOCK_PATH = DB_PATH + ".lock"


# -- DuckDB Connection Helper --
def _connect():
    return duckdb.connect(DB_PATH)


# Initialize DB on startup
def init_db():
    # ensure directory exists
    dir_path = os.path.dirname(DB_PATH)
    if dir_path and not os.path.exists(dir_path):
        os.makedirs(dir_path, exist_ok=True)
    # wipe existing DB and lock files
    if os.path.exists(DB_PATH):
        try:
            os.remove(DB_PATH)
            os.remove(LOCK_PATH)
        except OSError:
            pass
    # create fresh table
    with FileLock(LOCK_PATH):
        conn = _connect()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY,
              repo_url TEXT,
              status TEXT,
              result TEXT,
              error TEXT,
              created_at TIMESTAMP,
              started_at TIMESTAMP,
              finished_at TIMESTAMP
            )
            """
        )
        conn.close()


# -- CRUD operations --
def insert_job(job: dict):
    with FileLock(LOCK_PATH):
        conn = _connect()
        conn.execute(
            "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                job["id"],
                job["repo_url"],
                job["status"],
                job["result"],
                job["error"],
                job["created_at"],
                job["started_at"],
                job["finished_at"],
            ],
        )
        conn.close()


def update_job(job_id: str, **fields):
    cols, vals = zip(*fields.items())
    set_clause = ", ".join(f"{c} = ?" for c in cols)
    with FileLock(LOCK_PATH):
        conn = _connect()
        conn.execute(
            f"UPDATE jobs SET {set_clause} WHERE id = ?",
            list(vals) + [job_id],
        )
        conn.close()


def fetch_job(job_id: str) -> Optional[dict]:
    conn = _connect()
    res = conn.execute(
        "SELECT id, repo_url, status, result, error, created_at, started_at, finished_at" " FROM jobs WHERE id = ?",
        [job_id],
    ).fetchall()
    conn.close()
    if not res:
        return None
    id_, repo_url, status, result, error, created_at, started_at, finished_at = res[0]
    return {
        "id": id_,
        "repo_url": repo_url,
        "status": status,
        "result": result,
        "error": error,
        "created_at": created_at.isoformat() if created_at else None,
        "started_at": started_at.isoformat() if started_at else None,
        "finished_at": finished_at.isoformat() if finished_at else None,
    }


def fetch_all_jobs() -> list[dict]:
    conn = _connect()
    res = conn.execute(
        "SELECT id, repo_url, status, result, error, created_at, started_at, finished_at"
        " FROM jobs ORDER BY created_at DESC"
    ).fetchall()
    conn.close()

    jobs = []
    for row in res:
        id_, repo_url, status, result, error, created_at, started_at, finished_at = row
        jobs.append(
            {
                "id": id_,
                "repo_url": repo_url,
                "status": status,
                "result": result,
                "error": error,
                "created_at": created_at.isoformat() if created_at else None,
                "started_at": started_at.isoformat() if started_at else None,
                "finished_at": finished_at.isoformat() if finished_at else None,
            }
        )
    return jobs
