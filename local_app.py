import asyncio
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict
from urllib.parse import urlparse

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

app = FastAPI(
    title="Onboarding Diagram Generator",
    description="Generate docs/diagrams for a GitHub repo",
    version="1.0.0",
)

origins = ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "ngrok-skip-browser-warning", "User-Agent"],
    allow_credentials=False,
)

MAX_CONCURRENT_JOBS = 5
job_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
jobs: Dict[str, dict] = {}

class JobStatus:
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"

def extract_repo_name(repo_url: str) -> str:
    """Extract repository name from GitHub URL."""
    parsed = urlparse(repo_url)
    path_parts = parsed.path.strip('/').split('/')
    if len(path_parts) >= 2:
        repo_name = path_parts[-1]
        if repo_name.endswith('.git'):
            repo_name = repo_name[:-4]
        return repo_name
    raise ValueError(f"Invalid GitHub URL format: {repo_url}")

def make_job(repo_url: str) -> dict:
    job_id = str(uuid.uuid4())
    return {
        "id": job_id,
        "repo_url": repo_url,
        "status": JobStatus.PENDING,
        "result": None,
        "error": None,
        "created_at": datetime.utcnow().isoformat(),
        "started_at": None,
        "finished_at": None,
    }

async def generate_onboarding(job_id: str):
    job = jobs[job_id]
    job["status"] = JobStatus.RUNNING
    job["started_at"] = datetime.utcnow().isoformat()
    
    try:
        async with job_semaphore:
            temp_repo_folder = create_temp_repo_folder()
            try:
                repo_root = os.getenv("REPO_ROOT")
                if not repo_root:
                    raise ValueError("REPO_ROOT environment variable not set")
                
                repo_name = extract_repo_name(job["repo_url"])
                repo_path = Path(repo_root) / repo_name
                
                result = await run_in_threadpool(
                    generate_documents,
                    repo_path=repo_path,
                    temp_repo_folder=temp_repo_folder,
                    repo_name=repo_name,
                )
                
                output_dir = Path("generated_docs")
                output_dir.mkdir(exist_ok=True)
                
                job["result"] = f"{result}/onboarding.md"
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

@app.get(
    "/github_action", 
    response_class=JSONResponse, 
    summary="Generate onboarding docs for a GitHub repo and return content",
    responses={
        200: {"description": "Returns the generated markdown files as JSON"},
        404: {"description": "Repo not found or diagram generation failed"},
        500: {"description": "Internal server error"},
    },
)
async def github_action(job_id: str):
    return await generate_onboarding(job_id)

@app.post(
    "/generation", 
    response_class=JSONResponse, 
    summary="Create a new onboarding job", 
    responses={
        200: {"description": "Job created", "content": {"application/json": {}}},
        400: {"description": "Missing repo_url"},
    }
)
async def start_generation_job(
    repo_url: str = Query(..., description="GitHub repo URL"), 
    background_tasks: BackgroundTasks = None
):
    if not repo_url:
        raise HTTPException(400, detail="repo_url is required")
    
    job = make_job(repo_url)
    jobs[job["id"]] = job
    
    if background_tasks is not None:
        background_tasks.add_task(generate_onboarding, job["id"])
    else:
        asyncio.create_task(generate_onboarding(job["id"]))
    
    return {"job_id": job["id"], "status": job["status"]}

@app.get(
    "/generation/{job_id}", 
    response_class=JSONResponse, 
    summary="Get job status/result", 
    responses={
        200: {"description": "Job status/result"},
        404: {"description": "Job not found"},
    }
)
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

def generate_documents(repo_path, temp_repo_folder, repo_name):
    generator = DiagramGenerator(
        repo_location=repo_path, 
        temp_folder=temp_repo_folder, 
        repo_name=repo_name,
        output_dir=temp_repo_folder
    )
    return generator.generate_analysis()