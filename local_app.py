import dotenv

from main import generate_docs_remote

import logging
import os
import uuid
import json
import asyncio
from pathlib import Path
from datetime import datetime, timezone
from enum import Enum

from urllib.parse import urlparse
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from constants import AppConfig
from duckdb_crud import fetch_job, init_db, insert_job, update_job, fetch_all_jobs
from github_action import generate_analysis
from repo_utils import RepoDontExistError
from utils import CFGGenerationError, create_temp_repo_folder, remove_temp_repo_folder


dotenv.load_dotenv()


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# Environment variables
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
job_semaphore = asyncio.Semaphore(AppConfig.MAX_CONCURRENT_JOBS)

app.add_event_handler("startup", init_db)


def extract_repo_name(repo_url: str) -> str:
    parsed = urlparse(repo_url)
    parts = parsed.path.strip("/").split("/")
    if len(parts) >= 2:
        name = parts[-1]
        return name[:-4] if name.endswith(".git") else name
    raise ValueError(f"Invalid GitHub URL: {repo_url}")


# -- Job Creation & Processing --
def make_job(repo_url: str) -> dict:
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
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
    update_job(job_id, status=JobStatus.RUNNING, started_at=datetime.now(timezone.utc))
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
                await run_in_threadpool(
                    generate_docs_remote,
                    repo_url=job["repo_url"],
                    temp_repo_folder=temp_repo_folder,
                )

                # Process the generated files from temp_repo_folder
                docs_content = {}
                # Files are generated in the temp_repo_folder, not the returned repo_name
                analysis_files_json = list(temp_repo_folder.glob("*.json"))
                analysis_files_md = list(temp_repo_folder.glob("*.md"))

                logger.info(
                    f"Found {len(analysis_files_json)} JSON files and {len(analysis_files_md)} MD files in {temp_repo_folder}"
                )

                for file in analysis_files_json:
                    try:
                        with open(file, "r", encoding="utf-8") as f:
                            content = f.read().strip()
                            if content:  # Only add non-empty files
                                docs_content[file.name] = content
                                logger.info(f"Added JSON file: {file.name}")
                    except Exception as e:
                        logger.warning(f"Failed to read JSON file {file}: {e}")

                for file in analysis_files_md:
                    try:
                        with open(file, "r", encoding="utf-8") as f:
                            content = f.read().strip()
                            if content:  # Only add non-empty files
                                docs_content[file.name] = content
                                logger.info(f"Added MD file: {file.name}")
                    except Exception as e:
                        logger.warning(f"Failed to read MD file {file}: {e}")

                if not docs_content:
                    logger.warning("No documentation files generated for: %s", job["repo_url"])
                    update_job(
                        job_id,
                        status=JobStatus.FAILED,
                        error="No documentation files were generated",
                        finished_at=datetime.now(timezone.utc),
                    )
                    return

                # Store result as JSON string in the result field
                result = json.dumps({"files": docs_content})
                update_job(job_id, result=result, status=JobStatus.COMPLETED, finished_at=datetime.now(timezone.utc))
                logger.info(
                    "Successfully generated %d doc files for %s (job: %s)", len(docs_content), job["repo_url"], job_id
                )

            except RepoDontExistError:
                url = job.get("repo_url", "unknown") if job else "unknown"
                update_job(
                    job_id,
                    error=f"Repository not found: {url}",
                    status=JobStatus.FAILED,
                    finished_at=datetime.now(timezone.utc),
                )
            except CFGGenerationError:
                update_job(
                    job_id,
                    error="Failed to generate diagram.",
                    status=JobStatus.FAILED,
                    finished_at=datetime.now(timezone.utc),
                )
            except Exception as e:
                update_job(
                    job_id, error=f"Server error: {e}", status=JobStatus.FAILED, finished_at=datetime.now(timezone.utc)
                )
            finally:
                remove_temp_repo_folder(str(temp_repo_folder))
    except Exception as e:
        logger.exception(f"Unexpected error in generate_onboarding: {e}")
        update_job(
            job_id, error=f"Unexpected error: {e}", status=JobStatus.FAILED, finished_at=datetime.now(timezone.utc)
        )


# -- API Endpoints --
@app.post("/generation", response_class=JSONResponse, summary="Create a new onboarding job")
async def start_generation_job(
    background_tasks: BackgroundTasks, repo_url: str = Query(..., description="GitHub repo URL")
):
    if not repo_url:
        raise HTTPException(400, detail="repo_url is required")
    job = make_job(repo_url)
    insert_job(job)
    background_tasks.add_task(generate_onboarding, job["id"])
    return {"job_id": job["id"], "status": job["status"]}


@app.get("/heart_beat", response_class=JSONResponse, summary="Get heart beat")
async def get_heart_beat():
    return JSONResponse({"status": "ok"}, status_code=200)


@app.get("/generation/{job_id}", response_class=JSONResponse, summary="Get job status/result")
async def get_job(job_id: str):
    job = fetch_job(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")

    response_data = {
        "job_id": job["id"],
        "status": job["status"],
        "created_at": job["created_at"],
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
        "repo_url": job["repo_url"],
    }

    if job["status"] == JobStatus.COMPLETED:
        if job.get("result"):
            # Check if result is a JSON string containing files
            try:
                result_data = json.loads(job["result"])
                if "files" in result_data:
                    response_data["files"] = result_data["files"]
                else:
                    response_data["result"] = job["result"]
            except (json.JSONDecodeError, TypeError):
                response_data["result"] = job["result"]
        else:
            response_data["result"] = {"message": "Job completed but no result available"}
    elif job["status"] == JobStatus.FAILED:
        if job.get("error"):
            response_data["error"] = job["error"]
        else:
            response_data["error"] = "Job failed with unknown error"

    return JSONResponse(content=response_data)


class DocsGenerationRequest(BaseModel):
    url: str
    source_branch: str = "main"
    target_branch: str = "main"
    extension: str = ".md"
    output_directory: str = ".codeboarding"


@app.post(
    "/github_action/jobs",
    response_class=JSONResponse,
    summary="Start a job to generate onboarding docs for a GitHub repo",
    responses={
        202: {"description": "Job created successfully, returns job ID"},
        400: {"description": "Invalid request parameters"},
    },
)
async def start_docs_generation_job(background_tasks: BackgroundTasks, docs_request: DocsGenerationRequest):
    """
    Start a background job to generate onboarding documentation.

    Example:
        POST /github_action/jobs?url=https://github.com/your/repo

    Returns:
        JSON object with job_id that can be used to check status
    """
    logger.info("Received request to start docs generation job for %s", docs_request.url)

    if docs_request.extension not in [".md", ".rst", ".html", ".mdx"]:
        logger.warning("Unsupported extension provided: %s. Defaulting to markdown", docs_request.extension)
        docs_request.extension = ".md"  # Default to markdown if unsupported extension is provided

    # Create job entry using duckdb
    job = make_job(docs_request.url)
    insert_job(job)

    # Start background task
    background_tasks.add_task(
        process_docs_generation_job,
        job["id"],
        docs_request.url,
        docs_request.source_branch,
        docs_request.target_branch,
        docs_request.output_directory,
        docs_request.extension,
    )

    logger.info("Created job %s for %s", job["id"], docs_request.url)
    return JSONResponse(
        status_code=202,
        content={"job_id": job["id"], "message": "Job created successfully. Use the job_id to check status."},
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
async def get_github_action_status(job_id: str):
    """
    Check the status of a documentation generation job.

    Example:
        GET /github_action/jobs/{job_id}

    Returns:
        JSON object with job status, and result if completed
    """
    job = fetch_job(job_id)

    if not job:
        logger.warning("Job not found: %s", job_id)
        raise HTTPException(404, detail="Job not found")

    response_data = {
        "job_id": job["id"],
        "status": job["status"],
        "created_at": job["created_at"],
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
        "repo_url": job["repo_url"],
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
    all_jobs = fetch_all_jobs()

    for job in all_jobs:
        job_summary = {
            "job_id": job["id"],
            "status": job["status"],
            "created_at": job["created_at"],
            "started_at": job["started_at"],
            "finished_at": job["finished_at"],
            "repo_url": job["repo_url"],
        }
        jobs_list.append(job_summary)

    return JSONResponse(content={"jobs": jobs_list})


async def process_docs_generation_job(
    job_id: str, url: str, source_branch: str, target_branch: str, output_dir: str, extension: str
):
    """Background task to process documentation generation"""
    update_job(job_id, status=JobStatus.RUNNING, started_at=datetime.now(timezone.utc))

    temp_repo_folder = create_temp_repo_folder()
    try:
        # Ensure the URL starts with the correct prefix
        if not url.startswith("https://github.com/"):
            url = "https://github.com/" + url

        # generate the docs
        files_dir = await run_in_threadpool(
            generate_analysis,
            repo_url=url,
            source_branch=source_branch,
            target_branch=target_branch,
            extension=extension,
            output_dir=output_dir,
        )

        # Process the generated files
        docs_content = {}
        analysis_files_json = list(Path(files_dir).glob("*.json"))
        analysis_files_extension = list(Path(files_dir).glob(f"*{extension}"))

        for file in analysis_files_json:
            with open(file, "r") as f:
                fname = file.stem
                docs_content[f"{fname}.json"] = f.read().strip()

        for file in analysis_files_extension:
            with open(file, "r") as f:
                fname = file.stem
                docs_content[f"{fname}{extension}"] = f.read().strip()

        if not docs_content:
            logger.warning("No documentation files generated for: %s", url)
            update_job(
                job_id,
                status=JobStatus.FAILED,
                error="No documentation files were generated",
                finished_at=datetime.now(timezone.utc),
            )
            return

        # Store result as JSON string in the result field
        result = json.dumps({"files": docs_content})
        update_job(job_id, status=JobStatus.COMPLETED, result=result, finished_at=datetime.now(timezone.utc))
        logger.info("Successfully generated %d doc files for %s (job: %s)", len(docs_content), url, job_id)

    except RepoDontExistError:
        logger.warning("Repo not found or clone failed: %s (job: %s)", url, job_id)
        update_job(
            job_id,
            status=JobStatus.FAILED,
            error=f"Repository not found or failed to clone: {url}",
            finished_at=datetime.now(timezone.utc),
        )

    except CFGGenerationError:
        logger.warning("CFG generation error for: %s (job: %s)", url, job_id)
        update_job(
            job_id,
            status=JobStatus.FAILED,
            error="Failed to generate diagram. We will look into it 🙂",
            finished_at=datetime.now(timezone.utc),
        )

    except Exception:
        logger.exception("Unexpected error processing repo %s (job: %s)", url, job_id)
        update_job(
            job_id, status=JobStatus.FAILED, error="Internal server error", finished_at=datetime.now(timezone.utc)
        )

    finally:
        # cleanup temp folder for this run
        remove_temp_repo_folder(str(temp_repo_folder))
