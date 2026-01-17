"""
Configuration for static analysis evaluation.
"""

from evals.schemas import ProjectSpec

# Projects to evaluate for static analysis performance
PROJECTS = [
    ProjectSpec(name="markitdown", url="https://github.com/microsoft/markitdown", expected_language="Python"),
    ProjectSpec(name="bertopic", url="https://github.com/MaartenGr/BERTopic", expected_language="Python"),
    ProjectSpec(name="browser-use", url="https://github.com/browser-use/browser-use", expected_language="Python"),
    ProjectSpec(name="fastapi", url="https://github.com/fastapi/fastapi", expected_language="Python"),
    ProjectSpec(name="django", url="https://github.com/django/django", expected_language="Python"),
    # ProjectSpec(name="tsoa", url="https://github.com/lukeautry/tsoa", expected_language="TypeScript"),
    # ProjectSpec(name="cobra", url="https://github.com/spf13/cobra", expected_language="Go"),
]
