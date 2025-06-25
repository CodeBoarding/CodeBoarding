import asyncio
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import duckdb
from filelock import FileLock
import dotenv
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from diagram_generator import DiagramGenerator
from utils import (
    CFGGenerationError,
    RepoDontExistError,
    RepoIsNone,
    create_temp_repo_folder,
    remove_temp_repo_folder,
)

dotenv.load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables
DB_PATH = os.getenv("JOB_DB", "jobs.duckdb")
LOCK_PATH = DB_PATH + ".lock"
REPO_ROOT = os.getenv("REPO_ROOT")
if not REPO_ROOT:
    logger.error("REPO_ROOT environment variable not set")

# FastAPI app
app = FastAPI(
    title="Onboarding Diagram Generator",
    description="Generate docs/diagrams for a GitHub repo",
    version="1.0.0",
)

# CORS setup
origins = ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "ngrok-skip-browser-warning", "User-Agent"],
    allow_credentials=False,
)

# Job concurrency limit
MAX_CONCURRENT_JOBS = 5
job_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)

class JobStatus:
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"

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

app.add_event_handler("startup", init_db)

# -- CRUD operations --
def insert_job(job: dict):
    with FileLock(LOCK_PATH):
        conn = _connect()
        conn.execute(
            "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                job["id"], job["repo_url"], job["status"], job["result"],
                job["error"], job["created_at"], job["started_at"], job["finished_at"]
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
        "SELECT id, repo_url, status, result, error, created_at, started_at, finished_at"
        " FROM jobs WHERE id = ?",
        [job_id]
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

# -- Utility Functions --
def extract_repo_name(repo_url: str) -> str:
    parsed = urlparse(repo_url)
    parts = parsed.path.strip('/').split('/')
    if len(parts) >= 2:
        name = parts[-1]
        return name[:-4] if name.endswith('.git') else name
    raise ValueError(f"Invalid GitHub URL: {repo_url}")


def generate_documents(repo_path, temp_repo_folder, repo_name):
    generator = DiagramGenerator(
        repo_location=repo_path,
        temp_folder=temp_repo_folder,
        repo_name=repo_name,
        output_dir=temp_repo_folder
    )
    return generator.generate_analysis()

# -- Job Creation & Processing --
def make_job(repo_url: str) -> dict:
    job_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    return {
        "id": job_id,
        "repo_url": repo_url,
        "status": JobStatus.PENDING,
        "result": None,
        "error": None,
        "created_at": now,
        "started_at": None,
        "finished_at": None,
    }

async def generate_onboarding(job_id: str):
    update_job(job_id, status=JobStatus.RUNNING, started_at=datetime.utcnow())
    try:
        async with job_semaphore:
            temp_repo_folder = create_temp_repo_folder()
            try:
                job = fetch_job(job_id)
                if not job:
                    raise ValueError(f"Job {job_id} not found")
                if not REPO_ROOT:
                    raise ValueError("REPO_ROOT environment variable not set")

                # run generation
                repo_name = extract_repo_name(job["repo_url"])
                repo_path = Path(REPO_ROOT) / repo_name
                await run_in_threadpool(
                    generate_documents,
                    repo_path=repo_path,
                    temp_repo_folder=temp_repo_folder,
                    repo_name=repo_name,
                )

                # format result URL
                result_url = (
                    f"https://github.com/CodeBoarding/"
                    f"GeneratedOnBoardings/blob/main/{repo_name}/on_boarding.md"
                )
                update_job(job_id, result=result_url, status=JobStatus.SUCCESS)

            except (RepoDontExistError, RepoIsNone):
                url = job.get("repo_url", "unknown") if job else "unknown"
                update_job(job_id, error=f"Repository not found: {url}", status=JobStatus.FAILED)
            except CFGGenerationError:
                update_job(job_id, error="Failed to generate diagram.", status=JobStatus.FAILED)
            except Exception as e:
                update_job(job_id, error=f"Server error: {e}", status=JobStatus.FAILED)
            finally:
                remove_temp_repo_folder(str(temp_repo_folder))
    finally:
        update_job(job_id, finished_at=datetime.utcnow())

# -- API Endpoints --
@app.post(
    "/generation",
    response_class=JSONResponse,
    summary="Create a new onboarding job"
)
async def start_generation_job(
    repo_url: str = Query(..., description="GitHub repo URL"),
    background_tasks: BackgroundTasks = None
):
    if not repo_url:
        raise HTTPException(400, detail="repo_url is required")
    job = make_job(repo_url)
    insert_job(job)
    if background_tasks:
        background_tasks.add_task(generate_onboarding, job["id"])
    else:
        asyncio.create_task(generate_onboarding(job["id"]))
    return {"job_id": job["id"], "status": job["status"]}

@app.get(
    "/generation/{job_id}",
    response_class=JSONResponse,
    summary="Get job status/result"
)
async def get_job(job_id: str):
    job = fetch_job(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    return job

@app.get(
    "/github_action",
    response_class=JSONResponse,
    summary="Generate onboarding docs for a GitHub repo and return content"
)
async def github_action(job_id: str):
    return await generate_onboarding(job_id)
