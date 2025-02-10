from __future__ import annotations

import dataclasses
import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from copy import copy, deepcopy
from dataclasses import dataclass, is_dataclass
from typing import (
    TYPE_CHECKING,
    Any,
    Generic,
    cast,
)

import fastapi.params
import fastapi.routing
import fastapi.security.base
import fastapi.utils
from fastapi import APIRouter
from fastapi.routing import APIRoute
from issubclass import issubclass as lenient_issubclass
from pydantic import BaseModel
from starlette.routing import BaseRoute
from typing_extensions import TypeVar, assert_never

from cadwyn._utils import Sentinel
from cadwyn.exceptions import (
    CadwynError,
    RouteAlreadyExistsError,
    RouteByPathConverterDoesNotApplyToAnythingError,
    RouteRequestBySchemaConverterDoesNotApplyToAnythingError,
    RouteResponseBySchemaConverterDoesNotApplyToAnythingError,
    RouterGenerationError,
    RouterPathParamsModifiedError,
)
from cadwyn.schema_generation import (
    _add_request_and_response_params,
    generate_versioned_models,
)
from cadwyn.structure import Version, VersionBundle
from cadwyn.structure.common import Endpoint, VersionDate
from cadwyn.structure.endpoints import (
    EndpointDidntExistInstruction,
    EndpointExistedInstruction,
    EndpointHadInstruction,
)

if TYPE_CHECKING:
    from fastapi.dependencies.models import Dependant

_Call = TypeVar("_Call", bound=Callable[..., Any])
_R = TypeVar("_R", bound=APIRouter)
_WR = TypeVar("_WR", bound=APIRouter, default=APIRouter)
_RouteT = TypeVar("_RouteT", bound=BaseRoute)
# This is a hack we do because we can't guarantee how the user will use the router.
_DELETED_ROUTE_TAG = "_CADWYN_DELETED_ROUTE"


@dataclass(frozen=True, eq=True)
class _EndpointInfo:
    endpoint_path: str
    endpoint_methods: frozenset[str]


@dataclass(frozen=True)
class GeneratedRouters(Generic[_R, _WR]):
    endpoints: dict[VersionDate, _R]
    webhooks: dict[VersionDate, _WR]


def generate_versioned_routers(
    router: _R,
    versions: VersionBundle,
    *,
    webhooks: _WR | None = None,
) -> GeneratedRouters[_R, _WR]:
    if webhooks is None:
        webhooks = cast(_WR, APIRouter())
    return _EndpointTransformer(router, versions, webhooks).transform()


class VersionedAPIRouter(fastapi.routing.APIRouter):
    def only_exists_in_older_versions(self, endpoint: _Call) -> _Call:
        route = _get_route_from_func(self.routes, endpoint)
        if route is None:
            raise LookupError(
                f'Route not found on endpoint: "{endpoint.__name__}". '
                "Are you sure it's a route and decorators are in the correct order?",
            )
        if _DELETED_ROUTE_TAG in route.tags:
            raise CadwynError(f'The route "{endpoint.__name__}" was already deleted. You can\'t delete it again.')
        route.tags.append(_DELETED_ROUTE_TAG)
        return endpoint


def copy_router(router: _R) -> _R:
    router = copy(router)
    router.routes = [copy_route(r) for r in router.routes]
    return router


def copy_route(route: _RouteT) -> _RouteT:
    if not isinstance(route, APIRoute):
        return copy(route)

    # This is slightly wasteful in terms of resources but it makes it easy for us
    # to make sure that new versions of FastAPI are going to be supported even if
    # APIRoute gets new attributes.
    new_route = deepcopy(route)
    new_route.dependant = copy(route.dependant)
    new_route.dependencies = copy(route.dependencies)
    return new_route


class _EndpointTransformer(Generic[_R, _WR]):
    def __init__(self, parent_router: _R, versions: VersionBundle, webhooks: _WR) -> None:
        super().__init__()
        self.parent_router = parent_router
        self.versions = versions
        self.parent_webhooks_router = webhooks
        self.schema_generators = generate_versioned_models(versions)

        self.routes_that_never_existed = [
            route for route in parent_router.routes if isinstance(route, APIRoute) and _DELETED_ROUTE_TAG in route.tags
        ]

    def transform(self) -> GeneratedRouters[_R, _WR]:
        router = copy_router(self.parent_router)
        webhook_router = copy_router(self.parent_webhooks_router)
        routers: dict[VersionDate, _R] = {}
        webhook_routers: dict[VersionDate, _WR] = {}

        for version in self.versions:
            self.schema_generators[str(version.value)].annotation_transformer.migrate_router_to_version(router)
            self.schema_generators[str(version.value)].annotation_transformer.migrate_router_to_version(webhook_router)

            self._validate_all_data_converters_are_applied(router, version)

            routers[version.value] = router
            webhook_routers[version.value] = webhook_router
            # Applying changes for the next version
            router = copy_router(router)
            webhook_router = copy_router(webhook_router)
            self._apply_endpoint_changes_to_router(router.routes + webhook_router.routes, version)

        if self.routes_that_never_existed:
            raise RouterGenerationError(
                "Every route you mark with "
                f"@VersionedAPIRouter.{VersionedAPIRouter.only_exists_in_older_versions.__name__} "
                "must be restored in one of the older versions. Otherwise you just need to delete it altogether. "
                "The following routes have been marked with that decorator but were never restored: "
                f"{self.routes_that_never_existed}",
            )

        for route_index, head_route in enumerate(self.parent_router.routes):
            if not isinstance(head_route, APIRoute):
                continue
            _add_request_and_response_params(head_route)
            copy_of_dependant = copy(head_route.dependant)

            for older_router in list(routers.values()):
                older_route = older_router.routes[route_index]

                # We know they are APIRoutes because of the check at the very beginning of the top loop.
                # I.e. Because head_route is an APIRoute, both routes are  APIRoutes too
                older_route = cast(APIRoute, older_route)
                # Wait.. Why do we need this code again?
                if older_route.body_field is not None and _route_has_a_simple_body_schema(older_route):
                    if hasattr(older_route.body_field.type_, "__cadwyn_original_model__"):
                        template_older_body_model = older_route.body_field.type_.__cadwyn_original_model__
                    else:
                        template_older_body_model = older_route.body_field.type_
                else:
                    template_older_body_model = None
                _add_data_migrations_to_route(
                    older_route,
                    # NOTE: The fact that we use latest here assumes that the route can never change its response schema
                    head_route,
                    template_older_body_model,
                    older_route.body_field.alias if older_route.body_field is not None else None,
                    copy_of_dependant,
                    self.versions,
                )
        for router in routers.values():
            router.routes = [
                route
                for route in router.routes
                if not (isinstance(route, fastapi.routing.APIRoute) and _DELETED_ROUTE_TAG in route.tags)
            ]
        for webhook_router in webhook_routers.values():
            webhook_router.routes = [
                route
                for route in webhook_router.routes
                if not (isinstance(route, fastapi.routing.APIRoute) and _DELETED_ROUTE_TAG in route.tags)
            ]
        return GeneratedRouters(routers, webhook_routers)

    def _validate_all_data_converters_are_applied(self, router: APIRouter, version: Version):
        path_to_route_methods_mapping, head_response_models, head_request_bodies = self._extract_all_routes_identifiers(
            router
        )

        for version_change in version.changes:
            for by_path_converters in [
                *version_change.alter_response_by_path_instructions.values(),
                *version_change.alter_request_by_path_instructions.values(),
            ]:
                for by_path_converter in by_path_converters:
                    missing_methods = by_path_converter.methods.difference(
                        path_to_route_methods_mapping[by_path_converter.path]
                    )

                    if missing_methods:
                        raise RouteByPathConverterDoesNotApplyToAnythingError(
                            f"{by_path_converter.repr_name} "
                            f'"{version_change.__name__}.{by_path_converter.transformer.__name__}" '
                            f"failed to find routes with the following methods: {list(missing_methods)}. "
                            f"This means that you are trying to apply this converter to non-existing endpoint(s). "
                            "Please, check whether the path and methods are correct. (hint: path must include "
                            "all path variables and have a name that was used in the version that this "
                            "VersionChange resides in)"
                        )

            for by_schema_converters in version_change.alter_request_by_schema_instructions.values():
                for by_schema_converter in by_schema_converters:
                    if not by_schema_converter.check_usage:  # pragma: no cover
                        continue
                    missing_models = set(by_schema_converter.schemas) - head_request_bodies
                    if missing_models:
                        raise RouteRequestBySchemaConverterDoesNotApplyToAnythingError(
                            f"Request by body schema converter "
                            f'"{version_change.__name__}.{by_schema_converter.transformer.__name__}" '
                            f"failed to find routes with the following body schemas: "
                            f"{[m.__name__ for m in missing_models]}. "
                            f"This means that you are trying to apply this converter to non-existing endpoint(s). "
                        )
            for by_schema_converters in version_change.alter_response_by_schema_instructions.values():
                for by_schema_converter in by_schema_converters:
                    if not by_schema_converter.check_usage:  # pragma: no cover
                        continue
                    missing_models = set(by_schema_converter.schemas) - head_response_models
                    if missing_models:
                        raise RouteResponseBySchemaConverterDoesNotApplyToAnythingError(
                            f"Response by response model converter "
                            f'"{version_change.__name__}.{by_schema_converter.transformer.__name__}" '
                            f"failed to find routes with the following response models: "
                            f"{[m.__name__ for m in missing_models]}. "
                            f"This means that you are trying to apply this converter to non-existing endpoint(s). "
                            "If this is intentional and this converter really does not apply to any endpoints, then "
                            "pass check_usage=False argument to "
                            f"{version_change.__name__}.{by_schema_converter.transformer.__name__}"
                        )

    def _extract_all_routes_identifiers(
        self, router: APIRouter
    ) -> tuple[defaultdict[str, set[str]], set[Any], set[Any]]:
        response_models: set[Any] = set()
        request_bodies: set[Any] = set()
        path_to_route_methods_mapping: dict[str, set[str]] = defaultdict(set)

        for route in router.routes:
            if isinstance(route, APIRoute):
                if route.response_model is not None and lenient_issubclass(route.response_model, BaseModel):
                    response_models.add(route.response_model)
                    # Not sure if it can ever be None when it's a simple schema. Eh, I would rather be safe than sorry
                if _route_has_a_simple_body_schema(route) and route.body_field is not None:
                    annotation = route.body_field.field_info.annotation
                    if annotation is not None and lenient_issubclass(annotation, BaseModel):
                        request_bodies.add(annotation)
                path_to_route_methods_mapping[route.path] |= route.methods

        head_response_models = {model.__cadwyn_original_model__ for model in response_models}
        head_request_bodies = {getattr(body, "__cadwyn_original_model__", body) for body in request_bodies}

        return path_to_route_methods_mapping, head_response_models, head_request_bodies

    # TODO (https://github.com/zmievsa/cadwyn/issues/28): Simplify
    def _apply_endpoint_changes_to_router(  # noqa: C901
        self,
        routes: list[BaseRoute] | list[APIRoute],
        version: Version,
    ):
        for version_change in version.changes:
            for instruction in version_change.alter_endpoint_instructions:
                original_routes = _get_routes(
                    routes,
                    instruction.endpoint_path,
                    instruction.endpoint_methods,
                    instruction.endpoint_func_name,
                    is_deleted=False,
                )
                methods_to_which_we_applied_changes = set()
                methods_we_should_have_applied_changes_to = instruction.endpoint_methods.copy()

                if isinstance(instruction, EndpointDidntExistInstruction):
                    deleted_routes = _get_routes(
                        routes,
                        instruction.endpoint_path,
                        instruction.endpoint_methods,
                        instruction.endpoint_func_name,
                        is_deleted=True,
                    )
                    if deleted_routes:
                        method_union = set()
                        for deleted_route in deleted_routes:
                            method_union |= deleted_route.methods
                        raise RouterGenerationError(
                            f'Endpoint "{list(method_union)} {instruction.endpoint_path}" you tried to delete in '
                            f'"{version_change.__name__}" was already deleted in a newer version. If you really have '
                            f'two routes with the same paths and methods, please, use "endpoint(..., func_name=...)" '
                            f"to distinguish between them. Function names of endpoints that were already deleted: "
                            f"{[r.endpoint.__name__ for r in deleted_routes]}",
                        )
                    for original_route in original_routes:
                        methods_to_which_we_applied_changes |= original_route.methods
                        original_route.tags.append(_DELETED_ROUTE_TAG)
                    err = (
                        'Endpoint "{endpoint_methods} {endpoint_path}" you tried to delete in'
                        ' "{version_change_name}" doesn\'t exist in a newer version'
                    )
                elif isinstance(instruction, EndpointExistedInstruction):
                    if original_routes:
                        method_union = set()
                        for original_route in original_routes:
                            method_union |= original_route.methods
                        raise RouterGenerationError(
                            f'Endpoint "{list(method_union)} {instruction.endpoint_path}" you tried to restore in'
                            f' "{version_change.__name__}" already existed in a newer version. If you really have two '
                            f'routes with the same paths and methods, please, use "endpoint(..., func_name=...)" to '
                            f"distinguish between them. Function names of endpoints that already existed: "
                            f"{[r.endpoint.__name__ for r in original_routes]}",
                        )
                    deleted_routes = _get_routes(
                        routes,
                        instruction.endpoint_path,
                        instruction.endpoint_methods,
                        instruction.endpoint_func_name,
                        is_deleted=True,
                    )
                    try:
                        _validate_no_repetitions_in_routes(deleted_routes)
                    except RouteAlreadyExistsError as e:
                        raise RouterGenerationError(
                            f'Endpoint "{list(instruction.endpoint_methods)} {instruction.endpoint_path}" you tried to '
                            f'restore in "{version_change.__name__}" has {len(e.routes)} applicable routes that could '
                            f"be restored. If you really have two routes with the same paths and methods, please, use "
                            f'"endpoint(..., func_name=...)" to distinguish between them. Function names of '
                            f"endpoints that can be restored: {[r.endpoint.__name__ for r in e.routes]}",
                        ) from e
                    for deleted_route in deleted_routes:
                        methods_to_which_we_applied_changes |= deleted_route.methods
                        deleted_route.tags.remove(_DELETED_ROUTE_TAG)

                        routes_that_never_existed = _get_routes(
                            self.routes_that_never_existed,
                            deleted_route.path,
                            deleted_route.methods,
                            deleted_route.endpoint.__name__,
                            is_deleted=True,
                        )
                        if len(routes_that_never_existed) == 1:
                            self.routes_that_never_existed.remove(routes_that_never_existed[0])
                        elif len(routes_that_never_existed) > 1:  # pragma: no cover
                            # I am not sure if it's possible to get to this error but I also don't want
                            # to remove it because I like its clarity very much
                            routes = routes_that_never_existed
                            raise RouterGenerationError(
                                f'Endpoint "{list(deleted_route.methods)} {deleted_route.path}" you tried to restore '
                                f'in "{version_change.__name__}" has {len(routes_that_never_existed)} applicable '
                                f"routes with the same function name and path that could be restored. This can cause "
                                f"problems during version generation. Specifically, Cadwyn won't be able to warn "
                                f"you when you deleted a route and never restored it. Please, make sure that "
                                f"functions for all these routes have different names: "
                                f"{[f'{r.endpoint.__module__}.{r.endpoint.__name__}' for r in routes]}",
                            )
                    err = (
                        'Endpoint "{endpoint_methods} {endpoint_path}" you tried to restore in'
                        ' "{version_change_name}" wasn\'t among the deleted routes'
                    )
                elif isinstance(instruction, EndpointHadInstruction):
                    for original_route in original_routes:
                        methods_to_which_we_applied_changes |= original_route.methods
                        _apply_endpoint_had_instruction(version_change.__name__, instruction, original_route)
                    err = (
                        'Endpoint "{endpoint_methods} {endpoint_path}" you tried to change in'
                        ' "{version_change_name}" doesn\'t exist'
                    )
                else:
                    assert_never(instruction)
                method_diff = methods_we_should_have_applied_changes_to - methods_to_which_we_applied_changes
                if method_diff:
                    raise RouterGenerationError(
                        err.format(
                            endpoint_methods=list(method_diff),
                            endpoint_path=instruction.endpoint_path,
                            version_change_name=version_change.__name__,
                        ),
                    )


def _validate_no_repetitions_in_routes(routes: list[fastapi.routing.APIRoute]):
    route_map = {}

    for route in routes:
        route_info = _EndpointInfo(route.path, frozenset(route.methods))
        if route_info in route_map:
            raise RouteAlreadyExistsError(route, route_map[route_info])
        route_map[route_info] = route


def _add_data_migrations_to_route(
    route: APIRoute,
    head_route: Any,
    template_body_field: type[BaseModel] | None,
    template_body_field_name: str | None,
    dependant_for_request_migrations: Dependant,
    versions: VersionBundle,
):
    if not (route.dependant.request_param_name and route.dependant.response_param_name):  # pragma: no cover
        raise CadwynError(
            f"{route.dependant.request_param_name=}, {route.dependant.response_param_name=} "
            f"for route {list(route.methods)} {route.path} which should not be possible. Please, contact my author.",
        )

    route.endpoint = versions._versioned(
        template_body_field,
        template_body_field_name,
        route,
        head_route,
        dependant_for_request_migrations,
        request_param_name=route.dependant.request_param_name,
        background_tasks_param_name=route.dependant.background_tasks_param_name,
        response_param_name=route.dependant.response_param_name,
    )(route.endpoint)
    route.dependant.call = route.endpoint


def _apply_endpoint_had_instruction(
    version_change_name: str,
    instruction: EndpointHadInstruction,
    original_route: APIRoute,
):
    field_names: Iterable[str]
    if dataclasses.is_dataclass(instruction.attributes):
        field_names = instruction.attributes.__dataclass_fields__
    else:
        import attrs
        field_names = (field.name for field in attrs.fields(type(instruction.attributes)))
    for attr_name in field_names:
        attr = getattr(instruction.attributes, attr_name)
        if attr is not Sentinel:
            if getattr(original_route, attr_name) == attr:
                raise RouterGenerationError(
                    f'Expected attribute "{attr_name}" of endpoint'
                    f' "{list(original_route.methods)} {original_route.path}"'
                    f' to be different in "{version_change_name}", but it was the same.'
                    " It means that your version change has no effect on the attribute"
                    " and can be removed.",
                )
            if attr_name == "path":
                original_path_params = {p.alias for p in original_route.dependant.path_params}
                new_path_params = set(re.findall("{(.*?)}", attr))
                if new_path_params != original_path_params:
                    raise RouterPathParamsModifiedError(
                        f'When altering the path of "{list(original_route.methods)} {original_route.path}" '
                        f'in "{version_change_name}", you have tried to change its path params '
                        f'from "{list(original_path_params)}" to "{list(new_path_params)}". It is not allowed to '
                        "change the path params of a route because the endpoint was created to handle the old path "
                        "params. In fact, there is no need to change them because the change of path params is "
                        "not a breaking change. If you really need to change the path params, you should create a "
                        "new route with the new path params and delete the old one.",
                    )
            setattr(original_route, attr_name, attr)


def _get_routes(
    routes: Sequence[BaseRoute],
    endpoint_path: str,
    endpoint_methods: set[str],
    endpoint_func_name: str | None = None,
    *,
    is_deleted: bool = False,
) -> list[fastapi.routing.APIRoute]:
    endpoint_path = endpoint_path.rstrip("/")
    return [
        route
        for route in routes
        if (
            isinstance(route, fastapi.routing.APIRoute)
            and route.path.rstrip("/") == endpoint_path
            and set(route.methods).issubset(endpoint_methods)
            and (endpoint_func_name is None or route.endpoint.__name__ == endpoint_func_name)
            and (_DELETED_ROUTE_TAG in route.tags) == is_deleted
        )
    ]


def _get_route_from_func(
    routes: Sequence[BaseRoute],
    endpoint: Endpoint,
) -> fastapi.routing.APIRoute | None:
    for route in routes:
        if isinstance(route, fastapi.routing.APIRoute) and (route.endpoint == endpoint):
            return route
    return None


def _route_has_a_simple_body_schema(route: APIRoute) -> bool:
    # Remember this: if len(body_params) == 1, then route.body_schema == route.dependant.body_params[0]
    return len(route.dependant.body_params) == 1
