from pydantic import BaseModel


class Version(BaseModel):
    commit_hash: str
    code_boarding_version: str
