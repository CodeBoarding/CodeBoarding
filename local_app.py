import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from generate_markdown import generate_docs_remote
from utils import RepoDontExistError, RepoIsNone, CFGGenerationError, create_temp_repo_folder, remove_temp_repo_folder

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Onboarding Diagram Generator",
    description="Generate docs/diagrams for a GitHub repo via `generate_docs_remote`",
    version="1.0.0",
)

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

    except (RepoDontExistError, RepoIsNone):
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


@app.get(
    "/generate_docs",
    response_class=JSONResponse,
    summary="Generate onboarding docs for a GitHub repo and return content",
    responses={
        200: {"description": "Returns the generated markdown files as JSON"},
        404: {"description": "Repo not found or diagram generation failed"},
        500: {"description": "Internal server error"},
    },
)
async def generate_docs_content(url: str = Query(..., description="The HTTPS URL of the GitHub repository")):
    """
    Generate onboarding documentation and return the content directly.

    Example:
        GET /generate_docs?url=https://github.com/your/repo

    Returns:
        JSON object with file names as keys and their content as values
    """
    logger.info("Received request to generate docs content for %s", url)

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

        # Collect all generated markdown files
        docs_content = {}
        temp_path = Path(temp_repo_folder)

        for md_file in temp_path.glob("*.md"):
            if md_file.name != "README.md":  # Skip README files
                with open(md_file, 'r', encoding='utf-8') as f:
                    docs_content[md_file.name] = f.read()

        if not docs_content:
            logger.warning("No documentation files generated for: %s", url)
            raise HTTPException(404, detail="No documentation files were generated")

        logger.info("Successfully generated %d doc files for %s", len(docs_content), url)
        return JSONResponse(content={
            "repository": repo_name,
            "files": docs_content
        })

    except (RepoDontExistError, RepoIsNone):
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
