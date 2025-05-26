import logging

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
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
    "*"  # or e.g. "https://your-frontend.com"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["Content-Type", "ngrok-skip-browser-warning"],
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
