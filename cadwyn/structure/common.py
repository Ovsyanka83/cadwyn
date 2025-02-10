import datetime
from collections.abc import Callable
from typing_extensions import TypeAlias

import attrs
from pydantic import BaseModel
from typing_extensions import ParamSpec, TypeVar

VersionedModel = BaseModel
VersionDate = datetime.date
_P = ParamSpec("_P")
_R = TypeVar("_R")
Endpoint: TypeAlias = Callable[_P, _R]


@attrs.define(slots=True, kw_only=True)
class _HiddenAttributeMixin:
    is_hidden_from_changelog: bool = False
