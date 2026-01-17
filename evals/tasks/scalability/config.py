"""
Configuration for scalability evaluation.
"""

from evals.schemas import ProjectSpec

# Projects to evaluate for scalability analysis
PROJECTS = [
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
