from pydantic import BaseModel
from universi import Field


class UserCreateRequest(BaseModel):
    addresses: list[str] = Field(min_items=1)


class UserResource(BaseModel):
    id: int
    addresses: list[str]
