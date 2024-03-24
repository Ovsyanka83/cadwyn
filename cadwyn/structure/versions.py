import email.message
import functools
import inspect
import json
import warnings
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from contextlib import AsyncExitStack
from contextvars import ContextVar
from enum import Enum
from pathlib import Path
from types import ModuleType
from typing import Any, ClassVar, ParamSpec, TypeAlias, TypeVar, cast, overload

from fastapi import HTTPException, params
from fastapi import Request as FastapiRequest
from fastapi import Response as FastapiResponse
from fastapi._compat import _normalize_errors
from fastapi.concurrency import run_in_threadpool
from fastapi.dependencies.models import Dependant
from fastapi.dependencies.utils import solve_dependencies
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.routing import APIRoute, _prepare_response_content
from pydantic import BaseModel
from starlette._utils import is_async_callable
from typing_extensions import assert_never, deprecated

from cadwyn._compat import PYDANTIC_V2, ModelField, PydanticUndefined, model_dump
from cadwyn._package_utils import (
    IdentifierPythonPath,
    get_cls_pythonpath,
    get_package_path_from_module,
    get_version_dir_path,
)
from cadwyn._utils import classproperty, get_another_version_of_cls
from cadwyn.exceptions import CadwynError, CadwynHeadRequestValidationError, CadwynStructureError

from .._utils import Sentinel
from .common import Endpoint, VersionDate, VersionedModel
from .data import (
    AlterRequestByPathInstruction,
    AlterRequestBySchemaInstruction,
    AlterResponseByPathInstruction,
    AlterResponseBySchemaInstruction,
    RequestInfo,
    ResponseInfo,
    _BaseAlterResponseInstruction,
)
from .endpoints import AlterEndpointSubInstruction
from .enums import AlterEnumSubInstruction
from .modules import AlterModuleInstruction
from .schemas import AlterSchemaSubInstruction, SchemaHadInstruction

_CADWYN_REQUEST_PARAM_NAME = "cadwyn_request_param"
_CADWYN_RESPONSE_PARAM_NAME = "cadwyn_response_param"
_P = ParamSpec("_P")
_R = TypeVar("_R")
PossibleInstructions: TypeAlias = (
    AlterSchemaSubInstruction
    | AlterEndpointSubInstruction
    | AlterEnumSubInstruction
    | SchemaHadInstruction
    | AlterModuleInstruction
    | staticmethod
)
APIVersionVarType: TypeAlias = ContextVar[VersionDate | None] | ContextVar[VersionDate]


class VersionChange:
    description: ClassVar[str] = Sentinel
    instructions_to_migrate_to_previous_version: ClassVar[Sequence[PossibleInstructions]] = Sentinel
    alter_schema_instructions: ClassVar[list[AlterSchemaSubInstruction | SchemaHadInstruction]] = Sentinel
    alter_enum_instructions: ClassVar[list[AlterEnumSubInstruction]] = Sentinel
    alter_module_instructions: ClassVar[list[AlterModuleInstruction]] = Sentinel
    alter_endpoint_instructions: ClassVar[list[AlterEndpointSubInstruction]] = Sentinel
    alter_request_by_schema_instructions: ClassVar[dict[type[BaseModel], list[AlterRequestBySchemaInstruction]]] = (
        Sentinel
    )
    alter_request_by_path_instructions: ClassVar[dict[str, list[AlterRequestByPathInstruction]]] = Sentinel
    alter_response_by_schema_instructions: ClassVar[dict[type, list[AlterResponseBySchemaInstruction]]] = Sentinel
    alter_response_by_path_instructions: ClassVar[dict[str, list[AlterResponseByPathInstruction]]] = Sentinel
    _bound_version_bundle: "VersionBundle | None"

    def __init_subclass__(cls, _abstract: bool = False) -> None:
        super().__init_subclass__()

        if _abstract:
            return
        cls._validate_subclass()
        cls._extract_list_instructions_into_correct_containers()
        cls._extract_body_instructions_into_correct_containers()
        cls._check_no_subclassing()
        cls._bound_version_bundle = None

    @classmethod
    def _extract_body_instructions_into_correct_containers(cls):
        for instruction in cls.__dict__.values():
            if isinstance(instruction, AlterRequestBySchemaInstruction):
                for schema in instruction.schemas:
                    cls.alter_request_by_schema_instructions[schema].append(instruction)
            elif isinstance(instruction, AlterRequestByPathInstruction):
                cls.alter_request_by_path_instructions[instruction.path].append(instruction)
            elif isinstance(instruction, AlterResponseBySchemaInstruction):
                for schema in instruction.schemas:
                    cls.alter_response_by_schema_instructions[schema].append(instruction)
            elif isinstance(instruction, AlterResponseByPathInstruction):
                cls.alter_response_by_path_instructions[instruction.path].append(instruction)

    @classmethod
    def _extract_list_instructions_into_correct_containers(cls):
        cls.alter_schema_instructions = []
        cls.alter_enum_instructions = []
        cls.alter_module_instructions = []
        cls.alter_endpoint_instructions = []
        cls.alter_request_by_schema_instructions = defaultdict(list)
        cls.alter_request_by_path_instructions = defaultdict(list)
        cls.alter_response_by_schema_instructions = defaultdict(list)
        cls.alter_response_by_path_instructions = defaultdict(list)
        for alter_instruction in cls.instructions_to_migrate_to_previous_version:
            if isinstance(alter_instruction, SchemaHadInstruction | AlterSchemaSubInstruction):
                cls.alter_schema_instructions.append(alter_instruction)
            elif isinstance(alter_instruction, AlterEnumSubInstruction):
                cls.alter_enum_instructions.append(alter_instruction)
            elif isinstance(alter_instruction, AlterModuleInstruction):
                cls.alter_module_instructions.append(alter_instruction)
            elif isinstance(alter_instruction, AlterEndpointSubInstruction):
                cls.alter_endpoint_instructions.append(alter_instruction)
            elif isinstance(alter_instruction, staticmethod):  # pragma: no cover
                raise NotImplementedError(f'"{alter_instruction}" is an unacceptable version change instruction')
            else:
                assert_never(alter_instruction)

    @classmethod
    def _validate_subclass(cls):
        if cls.description is Sentinel:
            raise CadwynStructureError(
                f"Version change description is not set on '{cls.__name__}' but is required.",
            )
        if cls.instructions_to_migrate_to_previous_version is Sentinel:
            raise CadwynStructureError(
                f"Attribute 'instructions_to_migrate_to_previous_version' is not set on '{cls.__name__}'"
                " but is required.",
            )
        if not isinstance(cls.instructions_to_migrate_to_previous_version, Sequence):
            raise CadwynStructureError(
                f"Attribute 'instructions_to_migrate_to_previous_version' must be a sequence in '{cls.__name__}'.",
            )
        for instruction in cls.instructions_to_migrate_to_previous_version:
            if not isinstance(instruction, PossibleInstructions):
                raise CadwynStructureError(
                    f"Instruction '{instruction}' is not allowed. Please, use the correct instruction types",
                )
        for attr_name, attr_value in cls.__dict__.items():
            if not isinstance(
                attr_value,
                AlterRequestBySchemaInstruction
                | AlterRequestByPathInstruction
                | AlterResponseBySchemaInstruction
                | AlterResponseByPathInstruction,
            ) and attr_name not in {
                "description",
                "side_effects",
                "instructions_to_migrate_to_previous_version",
                "__module__",
                "__doc__",
            }:
                raise CadwynStructureError(
                    f"Found: '{attr_name}' attribute of type '{type(attr_value)}' in '{cls.__name__}'."
                    " Only migration instructions and schema properties are allowed in version change class body.",
                )

    @classmethod
    def _check_no_subclassing(cls):
        if cls.mro() != [cls, VersionChange, object]:
            raise TypeError(
                f"Can't subclass {cls.__name__} as it was never meant to be subclassed.",
            )

    def __init__(self) -> None:  # pyright: ignore[reportMissingSuperCall]
        raise TypeError(
            f"Can't instantiate {self.__class__.__name__} as it was never meant to be instantiated.",
        )


class VersionChangeWithSideEffects(VersionChange, _abstract=True):
    @classmethod
    def _check_no_subclassing(cls):
        if cls.mro() != [cls, VersionChangeWithSideEffects, VersionChange, object]:
            raise TypeError(
                f"Can't subclass {cls.__name__} as it was never meant to be subclassed.",
            )

    @classproperty
    def is_applied(cls: type["VersionChangeWithSideEffects"]) -> bool:  # pyright: ignore[reportGeneralTypeIssues]
        if (
            cls._bound_version_bundle is None
            or cls not in cls._bound_version_bundle._version_changes_to_version_mapping
        ):
            raise CadwynError(
                f"You tried to check whether '{cls.__name__}' is active but it was never bound to any version.",
            )
        api_version = cls._bound_version_bundle.api_version_var.get()
        if api_version is None:
            return True
        return cls._bound_version_bundle._version_changes_to_version_mapping[cls] <= api_version


class Version:
    def __init__(self, value: VersionDate, *version_changes: type[VersionChange]) -> None:
        super().__init__()

        self.value = value
        self.version_changes = version_changes

    def __repr__(self) -> str:
        return f"Version('{self.value}')"


class HeadVersion:
    def __init__(self, *version_changes: type[VersionChange]) -> None:
        super().__init__()
        self.version_changes = version_changes

        for version_change in version_changes:
            if any(
                [
                    version_change.alter_request_by_path_instructions,
                    version_change.alter_request_by_schema_instructions,
                    version_change.alter_response_by_path_instructions,
                    version_change.alter_response_by_schema_instructions,
                ]
            ):
                raise NotImplementedError(
                    f"HeadVersion does not support request or response migrations but {version_change} contained one."
                )


class VersionBundle:
    @overload
    def __init__(
        self,
        latest_version_or_head_version: Version | HeadVersion,
        /,
        *other_versions: Version,
        api_version_var: APIVersionVarType | None = None,
        head_schemas_package: ModuleType | None = None,
    ) -> None: ...

    @overload
    @deprecated("Pass head_version_package instead of latest_schemas_package.")
    def __init__(
        self,
        latest_version_or_head_version: Version | HeadVersion,
        /,
        *other_versions: Version,
        api_version_var: APIVersionVarType | None = None,
        latest_schemas_package: ModuleType | None = None,
    ) -> None: ...

    def __init__(
        self,
        latest_version_or_head_version: Version | HeadVersion,
        /,
        *other_versions: Version,
        api_version_var: APIVersionVarType | None = None,
        head_schemas_package: ModuleType | None = None,
        latest_schemas_package: ModuleType | None = None,
    ) -> None:
        super().__init__()

        if isinstance(latest_version_or_head_version, HeadVersion):
            self.head_version = latest_version_or_head_version
            self.versions = other_versions
        else:
            self.head_version = HeadVersion()
            self.versions = (latest_version_or_head_version, *other_versions)

        self.head_schemas_package = head_schemas_package or latest_schemas_package
        self.version_dates = tuple(version.value for version in self.versions)
        if api_version_var is None:
            api_version_var = ContextVar("cadwyn_api_version")
        self.api_version_var = api_version_var
        if sorted(self.versions, key=lambda v: v.value, reverse=True) != list(self.versions):
            raise CadwynStructureError(
                "Versions are not sorted correctly. Please sort them in descending order.",
            )
        if self.versions[-1].version_changes:
            raise CadwynStructureError(
                f'The first version "{self.versions[-1].value}" cannot have any version changes. '
                "Version changes are defined to migrate to/from a previous version so you "
                "cannot define one for the very first version.",
            )
        version_values = set()
        for version in self.versions:
            if version.value not in version_values:
                version_values.add(version.value)
            else:
                raise CadwynStructureError(
                    f"You tried to define two versions with the same value in the same "
                    f"{VersionBundle.__name__}: '{version.value}'.",
                )
            for version_change in version.version_changes:
                if version_change._bound_version_bundle is not None:
                    raise CadwynStructureError(
                        f"You tried to bind version change '{version_change.__name__}' to two different versions. "
                        "It is prohibited.",
                    )
                version_change._bound_version_bundle = self

    @property  # pragma: no cover
    @deprecated("Use head_version_package instead.")
    def latest_schemas_package(self):
        return self.head_schemas_package

    def __iter__(self) -> Iterator[Version]:
        yield from self.versions

    def _validate_head_schemas_package_structure(self):
        # This entire function won't be necessary once we start raising an exception
        # upon receiving `latest`.

        head_schemas_package = cast(ModuleType, self.head_schemas_package)
        if not hasattr(head_schemas_package, "__path__"):
            raise CadwynStructureError(
                f'The head schemas package must be a package. "{head_schemas_package.__name__}" is not a package.',
            )
        elif head_schemas_package.__name__.endswith(".head"):
            return "head"
        elif head_schemas_package.__name__.endswith(".latest"):
            warnings.warn(
                'The name of the head schemas module must be "head". '
                f'Received "{head_schemas_package.__name__}" instead.',
                DeprecationWarning,
                stacklevel=4,
            )
            return "latest"
        else:
            raise CadwynStructureError(
                'The name of the head schemas module must be "head". '
                f'Received "{head_schemas_package.__name__}" instead.',
            )

    @functools.cached_property
    def _all_versions(self):
        return (self.head_version, *self.versions)

    @functools.cached_property
    def versioned_schemas(self) -> dict[IdentifierPythonPath, type[VersionedModel]]:
        altered_schemas = {
            get_cls_pythonpath(instruction.schema): instruction.schema
            for version in self._all_versions
            for version_change in version.version_changes
            for instruction in list(version_change.alter_schema_instructions)
        }

        migrated_schemas = {
            get_cls_pythonpath(schema): schema
            for version in self._all_versions
            for version_change in version.version_changes
            for schema in list(version_change.alter_request_by_schema_instructions.keys())
        }

        return altered_schemas | migrated_schemas

    @functools.cached_property
    def versioned_enums(self) -> dict[IdentifierPythonPath, type[Enum]]:
        return {
            get_cls_pythonpath(instruction.enum): instruction.enum
            for version in self._all_versions
            for version_change in version.version_changes
            for instruction in version_change.alter_enum_instructions
        }

    @functools.cached_property
    def versioned_modules(self) -> dict[IdentifierPythonPath, ModuleType]:
        return {
            # We do this because when users import their modules, they might import
            # the __init__.py file directly instead of the package itself
            # which results in this extra `.__init__` suffix in the name
            instruction.module.__name__.removesuffix(".__init__"): instruction.module
            for version in self._all_versions
            for version_change in version.version_changes
            for instruction in version_change.alter_module_instructions
        }

    @functools.cached_property
    def versioned_directories(self) -> tuple[Path, ...]:
        if self.head_schemas_package is None:
            raise CadwynError(
                f"You cannot call 'VersionBundle.{self.migrate_response_body.__name__}' because it has no access to "
                "'head_schemas_package'. It likely means that it was not attached "
                "to any Cadwyn application which attaches 'head_schemas_package' during initialization."
            )
        return tuple(
            [get_package_path_from_module(self.head_schemas_package)]
            + [get_version_dir_path(self.head_schemas_package, version.value) for version in self]
        )

    def migrate_response_body(self, latest_response_model: type[BaseModel], *, latest_body: Any, version: VersionDate):
        """Convert the data to a specific version by applying all version changes from latest until that version
        in reverse order and wrapping the result in the correct version of latest_response_model.
        """
        response = ResponseInfo(FastapiResponse(status_code=200), body=latest_body)
        migrated_response = self._migrate_response(
            response,
            current_version=version,
            head_response_model=latest_response_model,
            path="\0\0\0",
            method="GET",
        )

        version = self._get_closest_lesser_version(version)
        # + 1 comes from latest also being in the versioned_directories list
        version_dir = self.versioned_directories[self.version_dates.index(version) + 1]

        versioned_response_model: type[BaseModel] = get_another_version_of_cls(
            latest_response_model, version_dir, self.versioned_directories
        )
        return versioned_response_model.parse_obj(migrated_response.body)

    def _get_closest_lesser_version(self, version: VersionDate):
        for defined_version in self.version_dates:
            if defined_version <= version:
                return defined_version
        raise CadwynError("You tried to migrate to version that is earlier than the first version which is prohibited.")

    @functools.cached_property
    def _version_changes_to_version_mapping(
        self,
    ) -> dict[type[VersionChange] | type[VersionChangeWithSideEffects], VersionDate]:
        return {
            version_change: version.value for version in self.versions for version_change in version.version_changes
        }

    async def _migrate_request(
        self,
        body_type: type[BaseModel] | None,
        head_dependant: Dependant,
        path: str,
        request: FastapiRequest,
        response: FastapiResponse,
        request_info: RequestInfo,
        current_version: VersionDate,
        head_route: APIRoute,
        exit_stack: AsyncExitStack,
    ) -> dict[str, Any]:
        method = request.method
        for v in reversed(self.versions):
            if v.value <= current_version:
                continue
            for version_change in v.version_changes:
                if body_type is not None and body_type in version_change.alter_request_by_schema_instructions:
                    for instruction in version_change.alter_request_by_schema_instructions[body_type]:
                        instruction(request_info)
                if path in version_change.alter_request_by_path_instructions:
                    for instruction in version_change.alter_request_by_path_instructions[path]:
                        if method in instruction.methods:
                            instruction(request_info)
        request.scope["headers"] = tuple((key.encode(), value.encode()) for key, value in request_info.headers.items())
        del request._headers
        # Remember this: if len(body_params) == 1, then route.body_schema == route.dependant.body_params[0]

        dependencies, errors, _, _, _ = await solve_dependencies(
            request=request,
            response=response,
            dependant=head_dependant,
            body=request_info.body,
            dependency_overrides_provider=head_route.dependency_overrides_provider,
            async_exit_stack=exit_stack,
        )
        if errors:
            raise CadwynHeadRequestValidationError(
                _normalize_errors(errors), body=request_info.body, version=current_version
            )
        return dependencies

    def _migrate_response(
        self,
        response_info: ResponseInfo,
        current_version: VersionDate,
        head_response_model: type[BaseModel],
        path: str,
        method: str,
    ) -> ResponseInfo:
        """Convert the data to a specific version by applying all version changes in reverse order.

        Args:
            endpoint: the function which usually returns this data. Data migrations marked with this endpoint will
            be applied to the passed data
            payload: data to be migrated. Will be mutated during the call
            version: the version to which the data should be converted

        Returns:
            Modified data
        """
        for v in self.versions:
            if v.value <= current_version:
                break
            for version_change in v.version_changes:
                migrations_to_apply: list[_BaseAlterResponseInstruction] = []

                if head_response_model and head_response_model in version_change.alter_response_by_schema_instructions:
                    migrations_to_apply.extend(
                        version_change.alter_response_by_schema_instructions[head_response_model]
                    )

                if path in version_change.alter_response_by_path_instructions:
                    for instruction in version_change.alter_response_by_path_instructions[path]:
                        if method in instruction.methods:
                            migrations_to_apply.append(instruction)

                for migration in migrations_to_apply:
                    if response_info.status_code < 300 or migration.migrate_http_errors:
                        migration(response_info)
        return response_info

    # TODO (https://github.com/zmievsa/cadwyn/issues/113): Refactor this function and all functions it calls.
    def _versioned(
        self,
        head_body_field: type[BaseModel] | None,
        module_body_field_name: str | None,
        route: APIRoute,
        head_route: APIRoute,
        dependant_for_request_migrations: Dependant,
        *,
        request_param_name: str,
        response_param_name: str,
    ) -> Callable[[Endpoint[_P, _R]], Endpoint[_P, _R]]:
        def wrapper(endpoint: Endpoint[_P, _R]) -> Endpoint[_P, _R]:
            @functools.wraps(endpoint)
            async def decorator(*args: Any, **kwargs: Any) -> _R:
                request_param: FastapiRequest = kwargs[request_param_name]
                response_param: FastapiResponse = kwargs[response_param_name]
                method = request_param.method
                response = Sentinel
                async with AsyncExitStack() as exit_stack:
                    kwargs = await self._convert_endpoint_kwargs_to_version(
                        head_body_field,
                        module_body_field_name,
                        # Dependant must be from the version of the finally migrated request,
                        # not the version of endpoint
                        dependant_for_request_migrations,
                        request_param_name,
                        kwargs,
                        response_param,
                        route,
                        head_route,
                        exit_stack,
                    )

                    response = await self._convert_endpoint_response_to_version(
                        endpoint,
                        head_route,
                        route,
                        method,
                        response_param_name,
                        kwargs,
                        response_param,
                    )
                if response is Sentinel:  # pragma: no cover
                    raise CadwynError(
                        "No response object was returned. There's a high chance that the "
                        "application code is raising an exception and a dependency with yield "
                        "has a block with a bare except, or a block with except Exception, "
                        "and is not raising the exception again. Read more about it in the "
                        "docs: https://fastapi.tiangolo.com/tutorial/dependencies/dependencies-with-yield/#dependencies-with-yield-and-except"
                    )
                return response

            if request_param_name == _CADWYN_REQUEST_PARAM_NAME:
                _add_keyword_only_parameter(decorator, _CADWYN_REQUEST_PARAM_NAME, FastapiRequest)
            if response_param_name == _CADWYN_RESPONSE_PARAM_NAME:
                _add_keyword_only_parameter(decorator, _CADWYN_RESPONSE_PARAM_NAME, FastapiResponse)

            return decorator  # pyright: ignore[reportReturnType]

        return wrapper

    # TODO: Simplify it
    async def _convert_endpoint_response_to_version(  # noqa: C901
        self,
        func_to_get_response_from: Endpoint,
        head_route: APIRoute,
        route: APIRoute,
        method: str,
        response_param_name: str,
        kwargs: dict[str, Any],
        fastapi_response_dependency: FastapiResponse,
    ) -> Any:
        raised_exception = None
        if response_param_name == _CADWYN_RESPONSE_PARAM_NAME:
            kwargs.pop(response_param_name)
        try:
            if is_async_callable(func_to_get_response_from):
                response_or_response_body: FastapiResponse | object = await func_to_get_response_from(**kwargs)
            else:
                response_or_response_body: FastapiResponse | object = await run_in_threadpool(
                    func_to_get_response_from,
                    **kwargs,
                )
        except HTTPException as exc:
            raised_exception = exc
            response_or_response_body = FastapiResponse(
                content=json.dumps({"detail": raised_exception.detail}),
                status_code=raised_exception.status_code,
                headers=raised_exception.headers,
            )
        api_version = self.api_version_var.get()
        if api_version is None:
            return response_or_response_body

        if isinstance(response_or_response_body, FastapiResponse):
            # TODO (https://github.com/zmievsa/cadwyn/issues/125): Add support for migrating `StreamingResponse`
            # TODO (https://github.com/zmievsa/cadwyn/issues/126): Add support for migrating `FileResponse`
            # Starlette breaks Liskov Substitution principle and
            # doesn't define `body` for `StreamingResponse` and `FileResponse`
            if isinstance(response_or_response_body, StreamingResponse | FileResponse):
                body = None
            elif response_or_response_body.body:
                if isinstance(response_or_response_body, JSONResponse) or raised_exception is not None:
                    body = json.loads(response_or_response_body.body)
                else:
                    body = response_or_response_body.body.decode(response_or_response_body.charset)
            else:
                body = None
                # TODO (https://github.com/zmievsa/cadwyn/issues/51): Only do this if there are migrations

            response_info = ResponseInfo(response_or_response_body, body)
        else:
            if fastapi_response_dependency.status_code is not None:  # pyright: ignore[reportUnnecessaryComparison]
                status_code = fastapi_response_dependency.status_code
            elif route.status_code is not None:
                status_code = route.status_code
            elif raised_exception is not None:
                raise NotImplementedError
            else:
                status_code = 200
            fastapi_response_dependency.status_code = status_code
            response_info = ResponseInfo(
                fastapi_response_dependency,
                _prepare_response_content(
                    response_or_response_body,
                    exclude_unset=head_route.response_model_exclude_unset,
                    exclude_defaults=head_route.response_model_exclude_defaults,
                    exclude_none=head_route.response_model_exclude_none,
                ),
            )

        response_info = self._migrate_response(
            response_info,
            api_version,
            head_route.response_model,
            route.path,
            method,
        )
        if isinstance(response_or_response_body, FastapiResponse):
            # a webserver (uvicorn for instance) calculates the body at the endpoint level.
            # if an endpoint returns no "body", its content-length will be set to 0
            # json.dumps(None) results into "null", and content-length should be 4,
            # but it was already calculated to 0 which causes
            # `RuntimeError: Response content longer than Content-Length` or
            # `Too much data for declared Content-Length`, based on the protocol
            # which is why we skip the None case.

            # We skip cases without "body" attribute because of StreamingResponse and FileResponse
            # that do not have it. We don't support it too.
            if response_info.body is not None and hasattr(response_info._response, "body"):
                # TODO (https://github.com/zmievsa/cadwyn/issues/51): Only do this if there are migrations
                if isinstance(response_info.body, str):
                    response_info._response.body = response_info.body.encode(response_info._response.charset)
                else:
                    response_info._response.body = json.dumps(
                        response_info.body,
                        ensure_ascii=False,
                        allow_nan=False,
                        indent=None,
                        separators=(",", ":"),
                    ).encode("utf-8")
                # It makes sense to re-calculate content length because the previously calculated one
                # might slightly differ. If it differs -- uvicorn will break.
                response_info.headers["content-length"] = str(len(response_info._response.body))

            if raised_exception is not None and response_info.status_code >= 400:
                if isinstance(response_info.body, dict) and "detail" in response_info.body:
                    detail = response_info.body["detail"]
                else:
                    detail = response_info.body

                raise HTTPException(
                    status_code=response_info.status_code,
                    detail=detail,
                    headers=dict(response_info.headers),
                )
            return response_info._response
        return response_info.body

    async def _convert_endpoint_kwargs_to_version(
        self,
        head_body_field: type[BaseModel] | None,
        body_field_alias: str | None,
        head_dependant: Dependant,
        request_param_name: str,
        kwargs: dict[str, Any],
        response: FastapiResponse,
        route: APIRoute,
        head_route: APIRoute,
        exit_stack: AsyncExitStack,
    ) -> dict[str, Any]:
        request: FastapiRequest = kwargs[request_param_name]
        if request_param_name == _CADWYN_REQUEST_PARAM_NAME:
            kwargs.pop(request_param_name)

        api_version = self.api_version_var.get()
        if api_version is None:
            return kwargs

        # This is a kind of body param you get when you define a single pydantic schema in your route's body
        if (
            len(route.dependant.body_params) == 1
            and head_body_field is not None
            and body_field_alias is not None
            and body_field_alias in kwargs
        ):
            raw_body: BaseModel | None = kwargs.get(body_field_alias)
            if raw_body is None:
                body = None
            # It means we have a dict or a list instead of a full model.
            # This covers the following use case in the endpoint definition: "payload: dict = Body(None)"
            elif not isinstance(raw_body, BaseModel):
                body = raw_body
            else:
                body = model_dump(raw_body, by_alias=True, exclude_unset=True)
                if not PYDANTIC_V2 and raw_body.__custom_root_type__:  # pyright: ignore[reportAttributeAccessIssue]
                    body = body["__root__"]
        else:
            # This is for requests without body or with complex body such as form or file
            body = await _get_body(request, route.body_field, exit_stack)

        request_info = RequestInfo(request, body)
        new_kwargs = await self._migrate_request(
            head_body_field,
            head_dependant,
            route.path,
            request,
            response,
            request_info,
            api_version,
            head_route,
            exit_stack=exit_stack,
        )
        # Because we re-added it into our kwargs when we did solve_dependencies
        if _CADWYN_REQUEST_PARAM_NAME in new_kwargs:
            new_kwargs.pop(_CADWYN_REQUEST_PARAM_NAME)

        return new_kwargs


# We use this instead of `.body()` to automatically guess body type and load the correct body, even if it's a form
async def _get_body(
    request: FastapiRequest, body_field: ModelField | None, exit_stack: AsyncExitStack
):  # pragma: no cover # This is from fastapi
    is_body_form = body_field and isinstance(body_field.field_info, params.Form)
    try:
        body: Any = None
        if body_field:
            if is_body_form:
                body = await request.form()
                exit_stack.push_async_callback(body.close)
            else:
                body_bytes = await request.body()
                if body_bytes:
                    json_body: Any = PydanticUndefined
                    content_type_value = request.headers.get("content-type")
                    if not content_type_value:
                        json_body = await request.json()
                    else:
                        message = email.message.Message()
                        message["content-type"] = content_type_value
                        if message.get_content_maintype() == "application":
                            subtype = message.get_content_subtype()
                            if subtype == "json" or subtype.endswith("+json"):
                                json_body = await request.json()
                    if json_body != PydanticUndefined:
                        body = json_body
                    else:
                        body = body_bytes
    except json.JSONDecodeError as e:
        raise RequestValidationError(
            [
                {
                    "type": "json_invalid",
                    "loc": ("body", e.pos),
                    "msg": "JSON decode error",
                    "input": {},
                    "ctx": {"error": e.msg},
                },
            ],
            body=e.doc,
        ) from e
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="There was an error parsing the body") from e
    return body


def _add_keyword_only_parameter(
    func: Callable,
    param_name: str,
    param_annotation: type,
):
    signature = inspect.signature(func)
    func.__signature__ = signature.replace(
        parameters=(
            [
                *list(signature.parameters.values()),
                inspect.Parameter(param_name, kind=inspect._ParameterKind.KEYWORD_ONLY, annotation=param_annotation),
            ]
        ),
    )
