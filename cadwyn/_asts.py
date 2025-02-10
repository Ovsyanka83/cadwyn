from __future__ import annotations

import ast
import inspect
import sys
from collections.abc import Callable
from enum import Enum, auto
from types import GenericAlias, LambdaType
from typing import (  # noqa: UP035
    Any,
    List,
    Union,
    cast,
    get_args,
    get_origin,
)

import annotated_types

from cadwyn._utils import PlainRepr, UnionType
from cadwyn.exceptions import InvalidGenerationInstructionError

if sys.version_info >= (3, 10):
    from types import NoneType
else:
    NoneType = type(None)


_LambdaFunctionName = (lambda: None).__name__  # pragma: no branch


# A parent type of typing._GenericAlias
_BaseGenericAlias = cast(type, type(List[int])).mro()[1]  # noqa: UP006

# type(list[int]) and type(List[int]) are different which is why we have to do this.
# Please note that this problem is much wider than just lists which is why we use typing._BaseGenericAlias
# instead of typing._GenericAlias.
GenericAliasUnion = Union[GenericAlias, _BaseGenericAlias]


def get_fancy_repr(value: Any) -> Any:
    if isinstance(value, annotated_types.GroupedMetadata) and hasattr(type(value), "__dataclass_fields__"):
        return transform_grouped_metadata(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        return transform_collection(value)
    if isinstance(value, dict):
        return transform_dict(value)
    if isinstance(value, GenericAliasUnion):
        return transform_generic_alias(value)
    if value is None or value is NoneType:
        return transform_none(value)
    if isinstance(value, type):
        return transform_type(value)
    if isinstance(value, Enum):
        return transform_enum(value)
    if isinstance(value, auto):  # pragma: no cover # it works but we no longer use auto
        return transform_auto(value)
    if isinstance(value, UnionType):
        return transform_union(value)
    if isinstance(value, LambdaType) and _LambdaFunctionName == value.__name__:
        return transform_lambda(value)
    if inspect.isfunction(value):
        return transform_function(value)
    else:
        return transform_other(value)


def transform_grouped_metadata(value: annotated_types.GroupedMetadata):
    empty_obj = type(value)

    modified_fields = [
        (key, getattr(value, key))
        for key in value.__dataclass_fields__  # pyright: ignore[reportAttributeAccessIssue]
        if getattr(value, key) != getattr(empty_obj, key)
    ]

    return PlainRepr(
        value.__class__.__name__
        + "("
        + ", ".join(f"{PlainRepr(key)}={get_fancy_repr(v)}" for key, v in modified_fields)
        + ")",
    )


def transform_collection(value: list | tuple | set | frozenset) -> Any:
    return PlainRepr(value.__class__(map(get_fancy_repr, value)))


def transform_dict(value: dict) -> Any:
    return PlainRepr(
        value.__class__((get_fancy_repr(k), get_fancy_repr(v)) for k, v in value.items()),
    )


def transform_generic_alias(value: GenericAliasUnion) -> Any:
    return f"{get_fancy_repr(get_origin(value))}[{', '.join(get_fancy_repr(a) for a in get_args(value))}]"


def transform_none(_: type[None]) -> Any:
    return "None"


def transform_type(value: type) -> Any:
    return value.__name__


def transform_enum(value: Enum) -> Any:
    return PlainRepr(f"{value.__class__.__name__}.{value.name}")


def transform_auto(_: auto) -> Any:  # pragma: no cover # it works but we no longer use auto
    return PlainRepr("auto()")


def transform_union(value: UnionType) -> Any:
    return "typing.Union[" + (", ".join(get_fancy_repr(a) for a in get_args(value))) + "]"


def transform_lambda(value: LambdaType) -> Any:
    # We clean source because getsource() can return only a part of the expression which
    # on its own is not a valid expression such as: "\n  .had(default_factory=lambda: 91)"
    return _get_lambda_source_from_default_factory(inspect.getsource(value).strip(" \n\t."))


def transform_function(value: Callable) -> Any:
    return PlainRepr(value.__name__)


def transform_other(value: Any) -> Any:
    return PlainRepr(repr(value))


def _get_lambda_source_from_default_factory(source: str) -> str:
    found_lambdas: list[ast.Lambda] = [
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.keyword) and node.arg == "default_factory" and isinstance(node.value, ast.Lambda)
    ]

    if len(found_lambdas) == 1:
        return ast.unparse(found_lambdas[0])
    # These two errors are really hard to cover. Not sure if even possible, honestly :)
    elif len(found_lambdas) == 0:  # pragma: no cover
        raise InvalidGenerationInstructionError(
            f"No lambda found in default_factory even though one was passed: {source}",
        )
    else:  # pragma: no cover
        raise InvalidGenerationInstructionError(
            "More than one lambda found in default_factory. This is not supported.",
        )


def pop_docstring_from_cls_body(cls_body: list[ast.stmt]) -> list[ast.stmt]:
    if (
        len(cls_body) > 0
        and isinstance(cls_body[0], ast.Expr)
        and isinstance(cls_body[0].value, ast.Constant)
        and isinstance(cls_body[0].value.value, str)
    ):
        return [cls_body.pop(0)]
    else:
        return []
