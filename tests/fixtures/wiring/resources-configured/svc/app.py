import os


def key() -> str:
    return os.environ["OPENAI_API_KEY"]
