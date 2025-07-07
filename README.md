# CodeBoarding

CodeBoarding

In order to setup and run the project you need the following environment variables:

Setup the environment:

```bash
uv venv --python 3.11
uv pip sync
```

List of env variables you need:

```bash
CACHING_DOCUMENTATION= # if we should cache the documentation
REPO_ROOT= # The root directory of where repositories are downloaded
ROOT_RESULT= # Root of the directory for our demo uploads
GITHUB_TOKEN= # Github token for accessing private repositories
PROJECT_ROOT= # The source project root => Has to end with /CodeBoarding
DIAGRAM_DEPTH_LEVEL= # max level of depth for the generations
JOB_DB=.duckdb/duckdb # The database for the jobs
GOOGLE_API_KEY=# # Google API key for the Google Cloud Platform
````

### Compile the project for vscode extension:

```bash
pyinstaller --onefile vscode_runnable.py
```

Then the executable can be found in the `dist` folder.