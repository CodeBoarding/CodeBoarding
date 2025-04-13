from fastapi import FastAPI

from main import analyze

app = FastAPI()

@app.post("/generate_repository")
async def analyze_repo():
    mermaid_str = analyze()
    with open("/home/ivan/StartUp/CodeBoarding/repos/markitdown/README.md", "r") as f:
        existing_content = f.read()
    with open("/home/ivan/StartUp/CodeBoarding/repos/markitdown/README.md", "w") as f:
        f.write("# High Level Diagram\n")
        f.write(mermaid_str)
        f.write("\n")
        f.write(existing_content)

    # Now git add, commit and push
    import subprocess
    subprocess.run(["git", "-C", "/home/ivan/StartUp/CodeBoarding/repos/markitdown", "add", "/home/ivan/StartUp/CodeBoarding/repos/markitdown/README.md"])
    subprocess.run(["git", "-C", "/home/ivan/StartUp/CodeBoarding/repos/markitdown", "commit", "-m", "Added high level diagram"])
    subprocess.run(["git", "-C", "/home/ivan/StartUp/CodeBoarding/repos/markitdown", "push"])