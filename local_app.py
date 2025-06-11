import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from agents.agent_responses import AnalysisInsights
from diagram_generator import DiagramGenerator
from generate_markdown import generate_docs_remote, clone_repository
from utils import RepoDontExistError, RepoIsNone, CFGGenerationError, create_temp_repo_folder, remove_temp_repo_folder, \
    generate_markdown_content

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


def generate_documents(repo_path, temp_repo_folder, repo_name):
    generator = DiagramGenerator(repo_location=repo_path, temp_folder=temp_repo_folder, repo_name=repo_name,
                                 output_dir=temp_repo_folder)
    analysis_files = generator.generate_analysis()
    return analysis_files


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
async def generate_docs_content(url: str = Query(..., description="The HTTPS URL of the GitHub repository")):
    """
    Generate onboarding documentation and return the content directly.

    Example:
        GET /generate_docs?url=https://github.com/your/repo

    Returns:
        JSON object with file names as keys and their content as values
    """
    logger.info("Received request to generate docs content for %s", url)

    # Ensure the URL starts with the correct prefix
    if not url.startswith("https://github.com/"):
        url = "https://github.com/" + url

    # clone the repo:
    repo_name = clone_repository(url, Path(os.getenv("REPO_ROOT")))
    # Setup a dedicated temp folder for this run
    temp_repo_folder = create_temp_repo_folder()
    try:
        # generate the docs
        analysis_files = await run_in_threadpool(
            generate_documents,
            repo_path=Path(os.getenv("REPO_ROOT")) / repo_name,
            temp_repo_folder=temp_repo_folder,
            repo_name=repo_name,
        )

        # Now for each foc create the markdown and send it back:
        docs_content = {}
        for file in analysis_files:
            with open(file, 'r') as f:
                analysis = AnalysisInsights.model_validate_json(f.read())
                logging.info(f"Generated analysis file: {file}")
                markdown_response = generate_markdown_content(analysis, repo_name, link_files=("analysis.json" in file),
                                                      repo_url=url)
                fname = Path(file).name.split(".json")[0]
                fname = "on_boarding" if fname.endswith("analysis") else fname
                docs_content[f"{fname}.md"] = markdown_response.strip()

        if not docs_content:
            logger.warning("No documentation files generated for: %s", url)
            raise HTTPException(404, detail="No documentation files were generated")

        logger.info("Successfully generated %d doc files for %s", len(docs_content), url)
        resp = JSONResponse(content={
            "files": docs_content
        })
        print(resp)
        return resp

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
