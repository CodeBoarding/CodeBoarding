from evals.schemas import ProjectSpec

PROJECTS_STATIC_ANALYSIS = [
    ProjectSpec(name="markitdown", url="https://github.com/microsoft/markitdown", expected_language="Python"),
    ProjectSpec(name="bertopic", url="https://github.com/MaartenGr/BERTopic", expected_language="Python"),
    ProjectSpec(name="browser-use", url="https://github.com/browser-use/browser-use", expected_language="Python"),
    ProjectSpec(name="fastapi", url="https://github.com/fastapi/fastapi", expected_language="Python"),
    ProjectSpec(name="django", url="https://github.com/django/django", expected_language="Python"),
    # ProjectSpec(name="tsoa", url="https://github.com/lukeautry/tsoa", expected_language="TypeScript"),
    # ProjectSpec(name="cobra", url="https://github.com/spf13/cobra", expected_language="Go"),
]

PROJECTS_E2E = [
    ProjectSpec(name="markitdown", url="https://github.com/microsoft/markitdown", expected_language="Python"),
    ProjectSpec(name="codeboarding", url="https://github.com/CodeBoarding/CodeBoarding", expected_language="Python"),
    ProjectSpec(name="django", url="https://github.com/django/django", expected_language="Python"),
]

PROJECTS_SCALING = [
    ProjectSpec(
        name="markitdown-depth-1",
        url="https://github.com/microsoft/markitdown",
        expected_language="Python",
        env_vars={"DIAGRAM_DEPTH_LEVEL": "1"},
    ),
    ProjectSpec(
        name="bertopic-depth-1",
        url="https://github.com/MaartenGr/BERTopic",
        expected_language="Python",
        env_vars={"DIAGRAM_DEPTH_LEVEL": "1"},
    ),
    # ProjectSpec(
    #     name="markitdown-depth-2",
    #     url="https://github.com/microsoft/markitdown",
    #     expected_language="Python",
    #     env_vars={"DIAGRAM_DEPTH_LEVEL": "2"},
    # ),
]
