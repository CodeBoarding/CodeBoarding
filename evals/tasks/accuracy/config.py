from evals.schemas import ProjectSpec

PROJECTS = [
    ProjectSpec(
        name="markitdown",
        url="https://github.com/microsoft/markitdown",
        expected_language="Python",
        code_size="medium",
    ),
    ProjectSpec(
        name="pytorch_geometric",
        url="https://github.com/pytorch/pytorch_geometric",
        expected_language="Python",
        code_size="large",
    ),
]

DEPTH_LEVELS = [1, 2]
