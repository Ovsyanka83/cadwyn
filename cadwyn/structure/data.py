from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import ClassVar, cast

from fastapi import Request, Response
from starlette.datastructures import MutableHeaders
from typing_extensions import Any, ParamSpec, overload

from cadwyn._utils import same_definition_as_in
from cadwyn.structure.endpoints import _validate_that_strings_are_valid_http_methods

_P = ParamSpec("_P")


# TODO (https://github.com/zmievsa/cadwyn/issues/49): Add form handling
class RequestInfo:
    __slots__ = ("_cookies", "_query_params", "_request", "body", "headers")

    def __init__(self, request: Request, body: Any):
        super().__init__()
        self.body = body
        self.headers = request.headers.mutablecopy()
        self._cookies = request.cookies
        self._query_params = request.query_params._dict
        self._request = request

    @property
    def cookies(self) -> dict[str, str]:
        return self._cookies

    @property
    def query_params(self) -> dict[str, str]:
        return self._query_params


# TODO (https://github.com/zmievsa/cadwyn/issues/111): handle _response.media_type and _response.background
class ResponseInfo:
    __slots__ = ("_response", "body")

    def __init__(self, response: Response, body: Any):
        super().__init__()
        self.body = body
        self._response = response

    @property
    def status_code(self) -> int:
        return self._response.status_code

    @status_code.setter
    def status_code(self, value: int):
        self._response.status_code = value

    @property
    def headers(self) -> MutableHeaders:
        return self._response.headers

    @same_definition_as_in(Response.set_cookie)
    def set_cookie(self, *args: Any, **kwargs: Any):
        return self._response.set_cookie(*args, **kwargs)

    @same_definition_as_in(Response.delete_cookie)
    def delete_cookie(self, *args: Any, **kwargs: Any):
        return self._response.delete_cookie(*args, **kwargs)


@dataclass
class _AlterDataInstruction:
    transformer: Callable[[Any], None]
    owner: type = field(init=False)
    _payload_arg_name: ClassVar[str]

    def __post_init__(self):
        signature = inspect.signature(self.transformer)
        if list(signature.parameters) != [self._payload_arg_name]:
            raise ValueError(
                f"Method '{self.transformer.__name__}' must have only 1 parameter: {self._payload_arg_name}",
            )

        functools.update_wrapper(self, self.transformer)

    def __set_name__(self, owner: type, name: str):
        self.owner = owner

    def __call__(self, __request_or_response: RequestInfo | ResponseInfo, /) -> None:
        return self.transformer(__request_or_response)


@dataclass
class _BaseAlterBySchemaInstruction:
    schemas: tuple[Any, ...]
    check_usage: bool = True


##########
# Requests
##########


@dataclass
class _BaseAlterRequestInstruction(_AlterDataInstruction):
    _payload_arg_name = "request"


@dataclass
class _AlterRequestBySchemaInstruction(_BaseAlterBySchemaInstruction, _BaseAlterRequestInstruction): ...


@dataclass
class _AlterRequestByPathInstruction(_BaseAlterRequestInstruction):
    path: str
    methods: set[str]
    repr_name = "Request by path converter"


@overload
def convert_request_to_next_version_for(
    first_schema: type,
    /,
    *additional_schemas: type,
    check_usage: bool = True,
) -> type[staticmethod[_P, None]]: ...


@overload
def convert_request_to_next_version_for(path: str, methods: list[str], /) -> type[staticmethod[_P, None]]: ...


def convert_request_to_next_version_for(
    schema_or_path: type | str,
    methods_or_second_schema: list[str] | None | type = None,
    /,
    *additional_schemas: type,
    check_usage: bool = True,
) -> type[staticmethod[_P, None]]:
    _validate_decorator_args(schema_or_path, methods_or_second_schema, additional_schemas)

    def decorator(transformer: Callable[[RequestInfo], None]) -> Any:
        if isinstance(schema_or_path, str):
            return _AlterRequestByPathInstruction(
                path=schema_or_path,
                methods=set(cast(list, methods_or_second_schema)),
                transformer=transformer,
            )
        else:
            if methods_or_second_schema is None:
                schemas = (schema_or_path,)
            else:
                schemas = (schema_or_path, methods_or_second_schema, *additional_schemas)
            return _AlterRequestBySchemaInstruction(
                schemas=schemas,
                transformer=transformer,
                check_usage=check_usage,
            )

    return decorator  # pyright: ignore[reportReturnType]


###########
# Responses
###########


@dataclass
class _BaseAlterResponseInstruction(_AlterDataInstruction):
    _payload_arg_name = "response"
    migrate_http_errors: bool


@dataclass
class _AlterResponseBySchemaInstruction(_BaseAlterBySchemaInstruction, _BaseAlterResponseInstruction): ...


@dataclass
class _AlterResponseByPathInstruction(_BaseAlterResponseInstruction):
    path: str
    methods: set[str]
    repr_name = "Response by path converter"


@overload
def convert_response_to_previous_version_for(
    first_schema: type,
    /,
    *schemas: type,
    migrate_http_errors: bool = False,
    check_usage: bool = True,
) -> type[staticmethod[_P, None]]: ...


@overload
def convert_response_to_previous_version_for(
    path: str,
    methods: list[str],
    /,
    *,
    migrate_http_errors: bool = False,
) -> type[staticmethod[_P, None]]: ...


def convert_response_to_previous_version_for(
    schema_or_path: type | str,
    methods_or_second_schema: list[str] | type | None = None,
    /,
    *additional_schemas: type,
    migrate_http_errors: bool = False,
    check_usage: bool = True,
) -> type[staticmethod[_P, None]]:
    _validate_decorator_args(schema_or_path, methods_or_second_schema, additional_schemas)

    def decorator(transformer: Callable[[ResponseInfo], None]) -> Any:
        if isinstance(schema_or_path, str):
            # The validation above checks that methods is not None
            return _AlterResponseByPathInstruction(
                path=schema_or_path,
                methods=set(cast(list, methods_or_second_schema)),
                transformer=transformer,
                migrate_http_errors=migrate_http_errors,
            )
        else:
            if methods_or_second_schema is None:
                schemas = (schema_or_path,)
            else:
                schemas = (schema_or_path, methods_or_second_schema, *additional_schemas)
            return _AlterResponseBySchemaInstruction(
                schemas=schemas,
                transformer=transformer,
                migrate_http_errors=migrate_http_errors,
                check_usage=check_usage,
            )

    return decorator  # pyright: ignore[reportReturnType]


def _validate_decorator_args(
    schema_or_path: type | str, methods_or_second_schema: list[str] | type | None, additional_schemas: tuple[type, ...]
) -> None:
    if isinstance(schema_or_path, str):
        if not isinstance(methods_or_second_schema, list):
            raise TypeError("If path was provided as a first argument, methods must be provided as a second argument")
        _validate_that_strings_are_valid_http_methods(methods_or_second_schema)
        if additional_schemas:
            raise TypeError("If path was provided as a first argument, then additional schemas cannot be added")

    elif methods_or_second_schema is not None and not isinstance(methods_or_second_schema, type):
        raise TypeError("If schema was provided as a first argument, all other arguments must also be schemas")
