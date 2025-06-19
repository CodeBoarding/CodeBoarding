import logging
import os
from pathlib import Path
import uuid
import asyncio
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from typing import Dict
from datetime import datetime

from generate_markdown import generate_docs_remote
from utils import RepoDontExistError, RepoIsNone, CFGGenerationError, create_temp_repo_folder, remove_temp_repo_folder

import dotenv
dotenv.load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Onboarding Diagram Generator",
    description="Generate docs/diagrams for a GitHub repo",
    version="1.0.0",
)

# ---- CORS setup ----
origins = ["*"]  # Allow all origins for public API
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "ngrok-skip-browser-warning", "User-Agent"],
    allow_credentials=False,
)

# --- In-memory job management ---
MAX_CONCURRENT_JOBS = 5
job_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
jobs: Dict[str, dict] = {}  # job_id -> job dict

class JobStatus:
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"

def make_job(repo_url: str) -> dict:
    job_id = str(uuid.uuid4())
    job = {
        "id": job_id,
        "repo_url": repo_url,
        "status": JobStatus.PENDING,
        "result": None,
        "error": None,
        "created_at": datetime.utcnow().isoformat(),
        "started_at": None,
        "finished_at": None,
    }
    return job

async def generate_onboarding(job_id: str):
    job = jobs[job_id]
    job["status"] = JobStatus.RUNNING
    job["started_at"] = datetime.utcnow().isoformat()
    try:
        async with job_semaphore:
            temp_repo_folder = create_temp_repo_folder()
            try:
                repo_name = await run_in_threadpool(
                    generate_docs_remote,
                    repo_url=job["repo_url"],
                    temp_repo_folder=temp_repo_folder,
                    local_dev=True,
                )
                
                # Create a local output directory if it doesn't exist
                output_dir = Path("generated_docs")
                output_dir.mkdir(exist_ok=True)
                
                job["result"] = f"{repo_name}/onboarding.md"
                job["status"] = JobStatus.SUCCESS
            except (RepoDontExistError, RepoIsNone):
                job["error"] = f"Repository not found or failed to clone: {job['repo_url']}"
                job["status"] = JobStatus.FAILED
            except CFGGenerationError:
                job["error"] = "Failed to generate diagram. We will look into it 🙂"
                job["status"] = JobStatus.FAILED
            except Exception as e:
                job["error"] = f"Internal server error: {e}"
                job["status"] = JobStatus.FAILED
            finally:
                remove_temp_repo_folder(str(temp_repo_folder))
    finally:
        job["finished_at"] = datetime.utcnow().isoformat()

@app.post("/generation", response_class=JSONResponse, summary="Create a new onboarding job", responses={
    200: {"description": "Job created", "content": {"application/json": {}}},
    400: {"description": "Missing repo_url"},
})
async def start_generation_job(repo_url: str = Query(..., description="GitHub repo URL"), background_tasks: BackgroundTasks = None):
    if not repo_url:
        raise HTTPException(400, detail="repo_url is required")
    # Create new job
    job = make_job(repo_url)
    jobs[job["id"]] = job
    # Start background task
    if background_tasks is not None:
        background_tasks.add_task(generate_onboarding, job["id"])
    else:
        asyncio.create_task(generate_onboarding(job["id"]))
    return {"job_id": job["id"], "status": job["status"]}

@app.get("/generation/{job_id}", response_class=JSONResponse, summary="Get job status/result", responses={
    200: {"description": "Job status/result"},
    404: {"description": "Job not found"},
})
async def get_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    return {
        "id": job["id"],
        "repo_url": job["repo_url"],
        "status": job["status"],
        "result": job["result"],
        "error": job["error"],
        "created_at": job["created_at"],
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
    }
