from __future__ import annotations

from enum import Enum, auto
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, conint


class AlmostEmptyEnum(Enum):
    foo = auto()


class MyEnum(Enum):
    foo = auto()
    baz = auto()


class A(BaseModel):
    daz: int


def my_default_factory():
    raise NotImplementedError


class ModelWithWeirdFields(A):
    """My docstring"""

    foo: dict = Field(default={"a": "b"})
    bar: list[int] = Field(default_factory=my_default_factory)
    baz: Literal[MyEnum.foo]
    saz: Annotated[str, StringConstraints(to_upper=True)]
    laz: conint(gt=12)
    taz: int | str | None = Field(default_factory=lambda: 83)  # pragma: no branch
    naz: list[int] = Field(default=[1, 2, 3])
