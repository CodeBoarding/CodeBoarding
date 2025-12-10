from evals.types import ProjectSpec

PROJECTS_STATIC_ANALYSIS = [
    ProjectSpec(name="markitdown", url="https://github.com/microsoft/markitdown", expected_language="Python"),
    ProjectSpec(name="tsoa", url="https://github.com/lukeautry/tsoa", expected_language="TypeScript"),
    ProjectSpec(name="cobra", url="https://github.com/spf13/cobra", expected_language="Go"),
]

PROJECTS_E2E = [
    ProjectSpec(name="webscraping", url="https://github.com/brovatten/webscraping", expected_language="Python"),
]

PROJECTS_SCALING = [
    ProjectSpec(name="markitdown", url="https://github.com/microsoft/markitdown", expected_language="Python"),
]
