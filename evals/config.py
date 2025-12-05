"""
Configuration for evaluations.
"""

PROJECTS_STATIC_ANALYSIS = [
    {"name": "markitdown", "url": "https://github.com/microsoft/markitdown", "expected_language": "Python"},
    {"name": "tsoa", "url": "https://github.com/lukeautry/tsoa", "expected_language": "TypeScript"},
    {"name": "cobra", "url": "https://github.com/spf13/cobra", "expected_language": "Go"},
]

PROJECTS_E2E = [
    {"name": "webscraping", "url": "https://github.com/brovatten/webscraping", "expected_language": "Python"},
]

PROJECTS_SCALING = [
    {"name": "markitdown", "url": "https://github.com/microsoft/markitdown", "expected_language": "Python"},
]
