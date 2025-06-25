import logging
import os
import uuid
import asyncio
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime
from enum import Enum

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from demo import generate_docs_remote
from github_action import generate_analysis
from repo_utils import RepoDontExistError, clone_repository
from utils import CFGGenerationError, create_temp_repo_folder, remove_temp_repo_folder

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Onboarding Diagram Generator",
    description="Generate docs/diagrams for a GitHub repo via `generate_docs_remote`",
    version="1.0.0",
)
load_dotenv()

# ---- CORS setup ----
origins = [
    "*"  # Allow all origins for public API
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "ngrok-skip-browser-warning", "User-Agent"],
    allow_credentials=False,
)


@app.options("/generate_markdown")
async def preflight():
    # FastAPI + CORSMiddleware handles this automatically,
    # but you can still explicitly return 204 if you like:
    return PlainTextResponse(status_code=204)


@app.get(
    "/generate_markdown",
    response_class=PlainTextResponse,
    summary="Generate onboarding docs for a GitHub repo",
    responses={
        200: {"description": "Returns the GitHub URL of the generated markdown"},
        404: {"description": "Repo not found or diagram generation failed"},
        500: {"description": "Internal server error"},
    },
)
async def generate_markdown(url: str = Query(..., description="The HTTPS URL of the GitHub repository")):
    """
    Example:
        GET /myroute?url=https://github.com/your/repo
    """
    logger.info("Received request to generate docs for %s", url)

    # Setup a dedicated temp folder for this run
    temp_repo_folder = create_temp_repo_folder()
    try:
        # generate the docs
        repo_name = await run_in_threadpool(
            generate_docs_remote,
            repo_url=url,
            temp_repo_folder=temp_repo_folder,
            local_dev=True,
        )

        result_url = (
            f"https://github.com/CodeBoarding/GeneratedOnBoardings"
            f"/blob/main/{repo_name}/on_boarding.md"
        )
        logger.info("Successfully generated docs: %s", result_url)
        return result_url

    except RepoDontExistError:
        logger.warning("Repo not found or clone failed: %s", url)
        raise HTTPException(404, detail=f"Repository not found or failed to clone: {url}")

    except CFGGenerationError:
        logger.warning("CFG generation error for: %s", url)
        raise HTTPException(404, detail="Failed to generate diagram. We will look into it 🙂")

    except Exception as e:
        logger.exception("Unexpected error processing repo %s", url)
        raise HTTPException(500, detail="Internal server error")

    finally:
        # cleanup temp folder for this run
        remove_temp_repo_folder(temp_repo_folder)


@app.options("/generate_docs")
async def preflight_docs():
    return PlainTextResponse(status_code=204)


class DocsGenerationRequest(BaseModel):
    url: str
    source_branch: str = "main"
    target_branch: str = "main"
    extension: str = ".md"


@app.post(
    "/github_action/jobs",
    response_class=JSONResponse,
    summary="Start a job to generate onboarding docs for a GitHub repo",
    responses={
        202: {"description": "Job created successfully, returns job ID"},
        400: {"description": "Invalid request parameters"},
    },
)
async def start_docs_generation_job(
        background_tasks: BackgroundTasks,
        docs_request: DocsGenerationRequest
):
    """
    Start a background job to generate onboarding documentation.

    Example:
        POST /github_action/jobs?url=https://github.com/your/repo

    Returns:
        JSON object with job_id that can be used to check status
    """
    logger.info("Received request to start docs generation job for %s", docs_request.url)

    if docs_request.extension not in [".md", ".rst"]:
        logger.warning("Unsupported extension provided: %s. Defaulting to markdown", docs_request.extension)
        docs_request.extension = ".md"  # Default to markdown if unsupported extension is provided

    # Create job entry
    job_data = {
        "url": docs_request.url,
        "source_branch": docs_request.source_branch,
        "target_branch": docs_request.target_branch,
        "extension": docs_request.extension
    }

    job_id = job_store.create_job(job_data)

    # Start background task
    background_tasks.add_task(
        process_docs_generation_job,
        job_id,
        docs_request.url,
        docs_request.source_branch,
        docs_request.target_branch,
        docs_request.extension
    )

    logger.info("Created job %s for %s", job_id, docs_request.url)
    return JSONResponse(
        status_code=202,
        content={
            "job_id": job_id,
            "message": "Job created successfully. Use the job_id to check status."
        }
    )


@app.get(
    "/github_action/jobs/{job_id}",
    response_class=JSONResponse,
    summary="Check the status of a documentation generation job",
    responses={
        200: {"description": "Returns job status and result if completed"},
        404: {"description": "Job not found"},
    },
)
async def get_job_status(job_id: str):
    """
    Check the status of a documentation generation job.

    Example:
        GET /github_action/jobs/{job_id}

    Returns:
        JSON object with job status, and result if completed
    """
    job = job_store.get_job(job_id)

    if not job:
        logger.warning("Job not found: %s", job_id)
        raise HTTPException(404, detail="Job not found")
    response_data = {
        "job_id": job["id"],
        "status": job["status"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "url": job["url"],
        "source_branch": job["source_branch"],
        "target_branch": job["target_branch"],
        "extension": job["extension"]
    }

    if job["status"] == JobStatus.COMPLETED:
        if job.get("result"):
            response_data["result"] = job["result"]
        else:
            response_data["result"] = {"message": "Job completed but no result available"}
    elif job["status"] == JobStatus.FAILED:
        if job.get("error"):
            response_data["error"] = job["error"]
        else:
            response_data["error"] = "Job failed with unknown error"

    return JSONResponse(content=response_data)


@app.get(
    "/github_action/jobs",
    response_class=JSONResponse,
    summary="List all jobs",
    responses={
        200: {"description": "Returns list of all jobs"},
    },
)
async def list_jobs():
    """
    List all documentation generation jobs.

    Returns:
        JSON object with list of all jobs
    """
    jobs_list = []
    for job in job_store.jobs.values():
        job_summary = {
            "job_id": job["id"],
            "status": job["status"],
            "created_at": job["created_at"],
            "updated_at": job["updated_at"],
            "url": job["url"],
            "source_branch": job["source_branch"],
            "target_branch": job["target_branch"],
            "extension": job["extension"]
        }
        jobs_list.append(job_summary)

    return JSONResponse(content={"jobs": jobs_list})


# Job Management System
class JobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class JobStore:
    def __init__(self):
        self.jobs: Dict[str, Dict[str, Any]] = {}

    def create_job(self, job_data: Dict[str, Any]) -> str:
        job_id = str(uuid.uuid4())
        self.jobs[job_id] = {
            "id": job_id,
            "status": JobStatus.PENDING,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "result": None,
            "error": None,
            **job_data
        }
        return job_id

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        return self.jobs.get(job_id)

    def update_job_status(self, job_id: str, status: JobStatus, result: Any = None, error: str = None):
        if job_id in self.jobs:
            self.jobs[job_id]["status"] = status
            self.jobs[job_id]["updated_at"] = datetime.now().isoformat()
            if result is not None:
                self.jobs[job_id]["result"] = result
            if error is not None:
                self.jobs[job_id]["error"] = error


# Global job store instance
job_store = JobStore()


async def process_docs_generation_job(job_id: str, url: str, source_branch: str, target_branch: str, extension: str):
    """Background task to process documentation generation"""
    job_store.update_job_status(job_id, JobStatus.RUNNING)

    temp_repo_folder = create_temp_repo_folder()
    try:
        # Ensure the URL starts with the correct prefix
        if not url.startswith("https://github.com/"):
            url = "https://github.com/" + url

        # clone the repo:
        repo_name = clone_repository(url, Path(os.getenv("REPO_ROOT")))

        # generate the docs
        files_dir = await run_in_threadpool(
            generate_analysis,
            repo_url=url,
            source_branch=source_branch,
            target_branch=target_branch,
            extension=extension,
        )

        # Process the generated files
        docs_content = {}
        analysis_files_json = list(Path(files_dir).glob("*.json"))
        analysis_files_extension = list(Path(files_dir).glob(f"*{extension}"))

        for file in analysis_files_json:
            with open(file, 'r') as f:
                fname = file.stem
                docs_content[f"{fname}.json"] = f.read().strip()

        for file in analysis_files_extension:
            with open(file, 'r') as f:
                fname = file.stem
                docs_content[f"{fname}{extension}"] = f.read().strip()

        if not docs_content:
            logger.warning("No documentation files generated for: %s", url)
            job_store.update_job_status(job_id, JobStatus.FAILED, error="No documentation files were generated")
            return

        result = {"files": docs_content}
        job_store.update_job_status(job_id, JobStatus.COMPLETED, result=result)
        logger.info("Successfully generated %d doc files for %s (job: %s)", len(docs_content), url, job_id)

    except RepoDontExistError as e:
        logger.warning("Repo not found or clone failed: %s (job: %s)", url, job_id)
        job_store.update_job_status(job_id, JobStatus.FAILED, error=f"Repository not found or failed to clone: {url}")

    except CFGGenerationError as e:
        logger.warning("CFG generation error for: %s (job: %s)", url, job_id)
        job_store.update_job_status(job_id, JobStatus.FAILED,
                                    error="Failed to generate diagram. We will look into it 🙂")

    except Exception as e:
        logger.exception("Unexpected error processing repo %s (job: %s)", url, job_id)
        job_store.update_job_status(job_id, JobStatus.FAILED, error="Internal server error")

    finally:
        # cleanup temp folder for this run
        remove_temp_repo_folder(temp_repo_folder)


@app.options("/github_action/jobs")
async def preflight_jobs():
    return PlainTextResponse(status_code=204)


@app.options("/github_action/jobs/{job_id}")
async def preflight_job_status():
    return PlainTextResponse(status_code=204)
