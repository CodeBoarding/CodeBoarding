"""
Configuration for end-to-end evaluation.
"""

from evals.schemas import ProjectSpec

# Projects to evaluate for end-to-end pipeline execution
PROJECTS = [
    ProjectSpec(name="markitdown", url="https://github.com/microsoft/markitdown", expected_language="Python"),
    ProjectSpec(name="codeboarding", url="https://github.com/CodeBoarding/CodeBoarding", expected_language="Python"),
    ProjectSpec(name="django", url="https://github.com/django/django", expected_language="Python"),
]
