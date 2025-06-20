from pydantic import BaseModel


class Version(BaseModel):
    commit_hash: str
    branch: str
