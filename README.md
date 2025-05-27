# CodeBoarding

CodeBoarding

In order to setup and run the project you need the following environment variables:

Setup the environment:

```bash
uv venv
uv pip sync
```

### Compile the project for vscode extension:

```bash
pyinstaller --onefile vscode_runnable.py
```

Then the executable can be found in the `dist` folder.