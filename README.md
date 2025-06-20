# CodeBoarding

CodeBoarding

In order to setup and run the project you need the following environment variables:

Setup the environment:

```bash
uv venv
uv pip sync

npm install @mermaid-js/mermaid-cli
```

List of env variables you need:

```bash
CACHING_DOCUMENTATION= # if we should cache the documentation
REPO_ROOT= # The root directory of where repositories are downloaded
ROOT_RESULT= # Root of the directory for our demo uploads
GITHUB_TOKEN= # Github token for accessing private repositories
PROJECT_ROOT= # The source project root => Has to end with /CodeBoarding
DIAGRAM_DEPTH_LEVEL= # max leve of depth for the genrations
````

### Compile the project for vscode extension:

```bash
pyinstaller --onefile vscode_runnable.py
```

Then the executable can be found in the `dist` folder.