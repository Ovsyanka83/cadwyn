from __future__ import annotations

import importlib
import re
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from datetime import date
from enum import Enum, auto
from typing import Annotated, Any, NewType, cast, get_args
from uuid import UUID

import pytest
import svcs
from fastapi import APIRouter, Body, Depends, UploadFile
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.security.http import HTTPBasic
from fastapi.testclient import TestClient
from pydantic import BaseModel
from pytest_fixture_classes import fixture_class
from starlette.responses import FileResponse
from typing_extensions import TypeAlias

from cadwyn import VersionBundle, VersionedAPIRouter
from cadwyn.exceptions import CadwynError, RouterGenerationError, RouterPathParamsModifiedError
from cadwyn.route_generation import generate_versioned_routers
from cadwyn.schema_generation import generate_versioned_models
from cadwyn.structure import Version, convert_request_to_next_version_for, endpoint, schema
from cadwyn.structure.enums import enum
from cadwyn.structure.versions import VersionChange
from tests._data.unversioned_schema_dir import UnversionedSchema2
from tests._data.unversioned_schema_dir.unversioned_schemas import UnversionedSchema1
from tests._data.unversioned_schemas import UnversionedSchema3
from tests.conftest import (
    CreateVersionedApp,
    CreateVersionedClients,
    client,
    version_change,
)

Default = object()
Endpoint: TypeAlias = Callable[..., Awaitable[Any]]

TYPE_ATTR, ANNOTATION_ATTR = "type_", "annotation"


class StrEnum(str, Enum):
    a = auto()


class EmptySchema(BaseModel):
    pass


class SchemaWithOneIntField(BaseModel):
    foo: int


class SchemaWithOnePydanticField(BaseModel):
    foo: SchemaWithOneIntField


def get_wrapped_endpoint(endpoint: Endpoint) -> Endpoint:
    while hasattr(endpoint, "__wrapped__"):
        endpoint = endpoint.__wrapped__
    return endpoint


def endpoints_equal(endpoint1: Endpoint, endpoint2: Endpoint) -> bool:
    endpoint1 = get_wrapped_endpoint(endpoint1)
    endpoint2 = get_wrapped_endpoint(endpoint2)
    return endpoint1 == endpoint2


@pytest.fixture
def test_path() -> str:
    return "/test/{hewwo}"


@pytest.fixture
def test_endpoint(router: VersionedAPIRouter, test_path: str, random_uuid: UUID) -> Endpoint:
    @router.get(test_path)
    async def test(hewwo: int):
        raise NotImplementedError

    return test


@fixture_class(name="create_versioned_api_routes")
class CreateVersionedAPIRoutes:
    create_versioned_app: CreateVersionedApp

    def __call__(
        self,
        *version_changes: type[VersionChange],
        router: VersionedAPIRouter | None = None,
    ) -> tuple[list[APIRoute], list[APIRoute]]:
        app = self.create_versioned_app(*version_changes, router=router)
        return (
            cast(list[APIRoute], app.router.versioned_routers.get(date(2000, 1, 1), APIRouter()).routes),
            cast(list[APIRoute], app.router.versioned_routers.get(date(2001, 1, 1), APIRouter()).routes),
        )


def test__endpoint_didnt_exist(
    test_endpoint: Endpoint,
    test_path: str,
    create_versioned_api_routes: CreateVersionedAPIRoutes,
):
    routes_2000, routes_2001 = create_versioned_api_routes(version_change(endpoint(test_path, ["GET"]).didnt_exist))

    assert len(routes_2000) == 1
    assert routes_2000[0].path == "/openapi.json"
    assert len(routes_2001) == 2
    assert endpoints_equal(routes_2001[1].endpoint, test_endpoint)


def test__endpoint_existed(
    router: VersionedAPIRouter,
    create_versioned_api_routes: CreateVersionedAPIRoutes,
):
    @router.only_exists_in_older_versions
    @router.get("/test")
    async def test_endpoint():
        raise NotImplementedError

    @router.post("/test")
    async def test_endpoint_post():
        raise NotImplementedError

    routes_2000, routes_2001 = create_versioned_api_routes(
        version_change(
            endpoint("/test", ["GET"]).existed,
        ),
    )

    assert len(routes_2000) == 3
    assert endpoints_equal(routes_2000[1].endpoint, test_endpoint)
    assert endpoints_equal(routes_2000[2].endpoint, test_endpoint_post)

    assert len(routes_2001) == 2
    assert endpoints_equal(routes_2001[1].endpoint, test_endpoint_post)


def test__endpoint_existed__endpoint_removed_in_latest_but_never_restored__should_raise_error(
    router: VersionedAPIRouter,
    create_versioned_api_routes: CreateVersionedAPIRoutes,
):
    @router.only_exists_in_older_versions
    @router.get("/test")
    async def test_endpoint():
        raise NotImplementedError

    with pytest.raises(
        RouterGenerationError,
        match=re.escape(
            "Every route you mark with @VersionedAPIRouter.only_exists_in_older_versions must be restored in one "
            "of the older versions. Otherwise you just need to delete it altogether. The following routes have been "
            "marked with that decorator but were never restored: "
            "[APIRoute(path='/test', name='test_endpoint', methods=['GET'])]",
        ),
    ):
        create_versioned_api_routes(version_change())


def test__endpoint_existed__deleting_restoring_deleting_restoring_an_endpoint(
    router: VersionedAPIRouter,
    api_version_var: ContextVar[date | None],
):
    @router.only_exists_in_older_versions
    @router.get("/test")
    async def test_endpoint():
        raise NotImplementedError

    class MyVersionChange3(VersionChange):
        description = "..."
        instructions_to_migrate_to_previous_version = [endpoint("/test", ["GET"]).existed]

    class MyVersionChange2(VersionChange):
        description = "..."
        instructions_to_migrate_to_previous_version = [endpoint("/test", ["GET"]).didnt_exist]

    class MyVersionChange1(VersionChange):
        description = "..."
        instructions_to_migrate_to_previous_version = [endpoint("/test", ["GET"]).existed]

    versions = VersionBundle(
        Version(date(2003, 1, 1), MyVersionChange3),
        Version(date(2002, 1, 1), MyVersionChange2),
        Version(date(2001, 1, 1), MyVersionChange1),
        Version(date(2000, 1, 1)),
        api_version_var=api_version_var,
    )
    routers = generate_versioned_routers(router, versions=versions)

    assert len(routers.endpoints[date(2003, 1, 1)].routes) == 0
    assert len(routers.endpoints[date(2002, 1, 1)].routes) == 1
    assert len(routers.endpoints[date(2001, 1, 1)].routes) == 0
    assert len(routers.endpoints[date(2000, 1, 1)].routes) == 1


@pytest.mark.parametrize(
    ("attr", "attr_value"),
    [
        ("path", "/wow/{hewwo}"),
        ("status_code", 204),
        ("tags", ["foo", "bar"]),
        ("summary", "my summary"),
        ("description", "my description"),
        ("response_description", "my response description"),
        ("deprecated", True),
        ("include_in_schema", False),
        ("name", "my name"),
        ("openapi_extra", {"my_openapi_extra": "openapi_extra"}),
        ("responses", {405: {"description": "hewwo"}, 500: {"description": "hewwo1"}}),
        ("methods", {"GET", "POST"}),
        ("operation_id", "my_operation_id"),
        ("response_class", FileResponse),
        (
            "generate_unique_id_function",
            lambda api_route: api_route.endpoint.__name__,
        ),  # pragma: no cover
    ],
)
def test__endpoint_had(
    attr: str,
    attr_value: Any,
    test_endpoint: Endpoint,
    test_path: str,
    create_versioned_api_routes: CreateVersionedAPIRoutes,
):
    routes_2000, routes_2001 = create_versioned_api_routes(
        version_change(
            endpoint(test_path, ["GET"]).had(**{attr: attr_value}),
        ),
    )

    assert len(routes_2000) == len(routes_2001) == 2
    assert getattr(routes_2000[1], attr) == attr_value
    assert getattr(routes_2001[1], attr) != attr_value


def test__endpoint_had_another_path_variable(
    test_endpoint: Endpoint,
    test_path: str,
    create_versioned_api_routes: CreateVersionedAPIRoutes,
):
    with pytest.raises(RouterPathParamsModifiedError):
        create_versioned_api_routes(
            version_change(
                endpoint(test_path, ["GET"]).had(path="/test/{world}"),
            ),
        )


def test__endpoint_had_dependencies(
    test_endpoint: Endpoint,
    test_path: str,
    create_versioned_api_routes: CreateVersionedAPIRoutes,
):
    routes_2000, routes_2001 = create_versioned_api_routes(
        version_change(
            endpoint(test_path, ["GET"]).had(dependencies=[Depends(lambda: "hewwo")]),
        ),
    )

    assert len(routes_2000) == len(routes_2001) == 2
    assert len(routes_2000[1].dependencies) == 2
    dependency = routes_2000[1].dependencies[1].dependency
    assert dependency is not None
    assert dependency() == "hewwo"

    assert len(routes_2001[1].dependencies) == 1


def test__only_exists_in_older_versions__endpoint_is_not_a_route__error(
    router: VersionedAPIRouter,
    test_endpoint: Endpoint,
):
    with pytest.raises(
        LookupError,
        match=re.escape(
            'Route not found on endpoint: "test2". Are you sure it\'s a route and decorators are in the correct order?',
        ),
    ):

        @router.only_exists_in_older_versions
        async def test2():  # pragma: no branch
            raise NotImplementedError


def test__only_exists_in_older_versions__applied_twice__should_raise_error(
    router: VersionedAPIRouter,
    create_versioned_api_routes: CreateVersionedAPIRoutes,
):
    with pytest.raises(
        CadwynError,
        match=re.escape('The route "test_endpoint" was already deleted. You can\'t delete it again.'),
    ):

        @router.only_exists_in_older_versions  # pragma: no branch
        @router.only_exists_in_older_versions
        @router.get("/test")
        async def test_endpoint():  # pragma: no branch
            raise NotImplementedError


def test__router_generation__changing_a_deleted_endpoint__error(
    router: VersionedAPIRouter,
    create_versioned_app: CreateVersionedApp,
):
    @router.only_exists_in_older_versions
    @router.get("/test")
    async def test():
        raise NotImplementedError

    with pytest.raises(
        RouterGenerationError,
        match=re.escape(
            'Endpoint "[\'GET\'] /test" you tried to change in "MyVersionChange" doesn\'t exist',
        ),
    ):
        create_versioned_app(version_change(endpoint("/test", ["GET"]).had(description="Hewwo")))


def test__router_generation__changing_a_non_existent_endpoint__error(
    router: VersionedAPIRouter,
    create_versioned_app: CreateVersionedApp,
):
    with pytest.raises(
        RouterGenerationError,
        match=re.escape(
            'Endpoint "[\'GET\'] /test" you tried to change in "MyVersionChange" doesn\'t exist',
        ),
    ):
        create_versioned_app(version_change(endpoint("/test", ["GET"]).had(dependencies=[])))


def test__router_generation__re_creating_an_existing_endpoint__error(
    test_endpoint: Endpoint,
    test_path: str,
    create_versioned_app: CreateVersionedApp,
):
    with pytest.raises(
        RouterGenerationError,
        match=re.escape(
            "Endpoint \"['GET'] /test/{hewwo}\" you tried to restore in "
            '"MyVersionChange" already existed in a newer version',
        ),
    ):
        create_versioned_app(version_change(endpoint(test_path, ["GET"]).existed))


def test__router_generation__editing_an_endpoint_with_wrong_method__should_raise_error(
    test_endpoint: Endpoint,
    test_path: str,
    create_versioned_app: CreateVersionedApp,
):
    with pytest.raises(
        RouterGenerationError,
        match=re.escape('Endpoint "[\'POST\'] /test/{hewwo}" you tried to change in "MyVersionChange" doesn\'t exist'),
    ):
        create_versioned_app(version_change(endpoint(test_path, ["POST"]).had(description="Hewwo")))


def test__router_generation__editing_an_endpoint_with_a_less_general_method__should_raise_error(
    router: VersionedAPIRouter,
    create_versioned_app: CreateVersionedApp,
):
    @router.route("/test/{hewwo}", methods=["GET", "POST"])
    async def test(hewwo: int):
        raise NotImplementedError

    with pytest.raises(
        RouterGenerationError,
        match=re.escape('Endpoint "[\'GET\'] /test/{hewwo}" you tried to change in "MyVersionChange" doesn\'t exist'),
    ):
        create_versioned_app(version_change(endpoint("/test/{hewwo}", ["GET"]).had(description="Hewwo")))


def test__router_generation__editing_multiple_endpoints_with_same_route(
    router: VersionedAPIRouter,
    create_versioned_api_routes: CreateVersionedAPIRoutes,
):
    @router.api_route("/test/{hewwo}", methods=["GET", "POST"])
    async def test(hewwo: int):
        raise NotImplementedError

    routes_2000, routes_2001 = create_versioned_api_routes(
        version_change(
            endpoint("/test/{hewwo}", ["GET", "POST"]).had(description="Meaw"),
        ),
    )
    assert len(routes_2000) == len(routes_2001) == 2
    assert routes_2000[1].description == "Meaw"
    assert routes_2001[1].description == ""


def test__router_generation__editing_an_endpoint_with_a_more_general_method__should_raise_error(
    test_endpoint: Endpoint,
    test_path: str,
    create_versioned_app: CreateVersionedApp,
):
    with pytest.raises(
        RouterGenerationError,
        match=re.escape('Endpoint "[\'POST\'] /test/{hewwo}" you tried to change in "MyVersionChange" doesn\'t exist'),
    ):
        create_versioned_app(version_change(endpoint(test_path, ["GET", "POST"]).had(description="Hewwo")))


def test__router_generation__editing_multiple_methods_of_multiple_endpoints__should_edit_both_methods(
    router: VersionedAPIRouter,
    create_versioned_api_routes: CreateVersionedAPIRoutes,
):
    @router.get("/test")
    async def test_get():
        raise NotImplementedError

    @router.post("/test")
    async def test_post():
        raise NotImplementedError

    routes_2000, routes_2001 = create_versioned_api_routes(
        version_change(
            endpoint("/test", ["GET", "POST"]).had(description="Meaw"),
        ),
    )
    assert routes_2000[1].description == "Meaw"
    assert routes_2000[2].description == "Meaw"

    assert routes_2001[1].description == ""
    assert routes_2001[2].description == ""


def test__router_generation__deleting_a_deleted_endpoint__error(
    router: VersionedAPIRouter,
    create_versioned_app: CreateVersionedApp,
):
    @router.only_exists_in_older_versions
    @router.get("/test")
    async def test():
        raise NotImplementedError

    # with insert_pytest_raises():
    with pytest.raises(
        RouterGenerationError,
        match=re.escape(
            'Endpoint "[\'GET\'] /test" you tried to delete in "MyVersionChange" was already deleted in a '
            "newer version. If you really have two routes with the same paths and methods, please, use "
            '"endpoint(..., func_name=...)" to distinguish between them. '
            "Function names of endpoints that were already deleted: ['test']",
        ),
    ):
        create_versioned_app(version_change(endpoint("/test", ["GET"]).didnt_exist))


@pytest.mark.parametrize("delete_first", [True, False])
@pytest.mark.parametrize("route_index_to_delete_first", [0, 1])
def test__router_generation__restoring_deleted_routes_for_same_path_with_func_name__should_restore_only_one_route(
    router: VersionedAPIRouter,
    create_versioned_api_routes: CreateVersionedAPIRoutes,
    route_index_to_delete_first: int,
    delete_first: bool,
):
    @router.get("/test")
    async def test_get0():
        raise NotImplementedError

    @router.get("/test")
    async def test_get1():
        raise NotImplementedError

    routes = [test_get0, test_get1]
    route_to_delete_first = routes[route_index_to_delete_first]
    route_to_delete_second = routes[route_index_to_delete_first - 1]
    router.only_exists_in_older_versions(route_to_delete_first)
    instructions = [
        endpoint("/test", ["GET"], func_name=route_to_delete_first.__name__).existed,
        endpoint("/test", ["GET"], func_name=route_to_delete_second.__name__).didnt_exist,
    ]
    if delete_first:
        instructions.reverse()

    routes_2000, routes_2001 = create_versioned_api_routes(version_change(*instructions))

    assert len(routes_2000) == len(routes_2001) == 2
    assert endpoints_equal(routes_2000[1].endpoint, routes[route_index_to_delete_first])
    assert endpoints_equal(routes_2001[1].endpoint, routes[route_index_to_delete_first - 1])


def test__router_generation__changing_status_code_of_endpoint(
    router: VersionedAPIRouter,
    create_versioned_app: CreateVersionedApp,
):
    @router.post("/test", status_code=201)
    async def test_get():
        return {"hello": "world"}

    app = create_versioned_app(
        version_change(
            endpoint("/test", ["POST"]).had(status_code=200),
        ),
    )
    client = TestClient(app)
    assert client.post("/test", headers={app.router.api_version_header_name: "2000-01-01"}).status_code == 200
    assert client.post("/test", headers={app.router.api_version_header_name: "2001-01-01"}).status_code == 201


@pytest.mark.parametrize("route_index_to_delete_first", [0, 1])
def test__router_generation__restoring_deleted_route_for_same_path_without_func_name__should_raise_error(
    router: VersionedAPIRouter,
    create_versioned_api_routes: CreateVersionedAPIRoutes,
    route_index_to_delete_first: int,
):
    @router.get("/test")
    async def test_get0():
        raise NotImplementedError

    @router.get("/test")
    async def test_get1():
        raise NotImplementedError

    routes = [test_get0, test_get1]

    router.only_exists_in_older_versions(routes[route_index_to_delete_first])

    with pytest.raises(
        RouterGenerationError,
        match=re.escape(
            'Endpoint "[\'GET\'] /test" you tried to delete in "MyVersionChange" was already deleted in '
            "a newer version. If you really have two routes with the same paths and methods, please, use "
            '"endpoint(..., func_name=...)" to distinguish between them. '
            f"Function names of endpoints that were already deleted: ['test_get{route_index_to_delete_first}']",
        ),
    ):
        create_versioned_api_routes(
            version_change(
                endpoint("/test", ["GET"]).didnt_exist,
                endpoint("/test", ["GET"]).existed,
            ),
        )


@pytest.fixture
def two_deleted_routes(router: VersionedAPIRouter):
    @router.only_exists_in_older_versions
    @router.get("/test")
    async def test_get0():
        raise NotImplementedError

    @router.only_exists_in_older_versions
    @router.get("/test")
    async def test_get1():
        raise NotImplementedError

    return test_get0, test_get1


def test__router_generation__restoring_two_deleted_routes_for_same_path__should_raise_error(
    two_deleted_routes: tuple[Endpoint, Endpoint],
    create_versioned_api_routes: CreateVersionedAPIRoutes,
):
    with pytest.raises(
        RouterGenerationError,
        match=re.escape(
            'Endpoint "[\'GET\'] /test" you tried to restore in "MyVersionChange" has 2 applicable routes that '
            "could be restored. If you really have two routes with the same paths and methods, please, use "
            '"endpoint(..., func_name=...)" to distinguish between them. '
            "Function names of endpoints that can be restored: ['test_get1', 'test_get0']",
        ),
    ):
        create_versioned_api_routes(version_change(endpoint("/test", ["GET"]).existed))


@pytest.mark.parametrize("route_index_to_restore_first", [0, 1])
def test__endpoint_existed__deleting_and_restoring_two_routes_for_the_same_endpoint(
    router: VersionedAPIRouter,
    api_version_var: ContextVar[date | None],
    two_deleted_routes: tuple[Endpoint, Endpoint],
    route_index_to_restore_first: int,
):
    route_to_restore_first = two_deleted_routes[route_index_to_restore_first]
    route_to_restore_second = two_deleted_routes[route_index_to_restore_first - 1]

    class MyVersionChange2(VersionChange):
        description = "..."
        instructions_to_migrate_to_previous_version = [
            endpoint("/test", ["GET"], func_name=route_to_restore_first.__name__).existed,
        ]

    class MyVersionChange1(VersionChange):
        description = "..."
        instructions_to_migrate_to_previous_version = [
            endpoint("/test", ["GET"], func_name=route_to_restore_second.__name__).existed,
        ]

    versions = VersionBundle(
        Version(date(2002, 1, 1), MyVersionChange2),
        Version(date(2001, 1, 1), MyVersionChange1),
        Version(date(2000, 1, 1)),
        api_version_var=api_version_var,
    )
    routers = generate_versioned_routers(router, versions=versions)

    assert len(routers.endpoints[date(2002, 1, 1)].routes) == 0
    assert len(routers.endpoints[date(2001, 1, 1)].routes) == 1
    assert len(routers.endpoints[date(2000, 1, 1)].routes) == 2

    assert endpoints_equal(routers.endpoints[date(2001, 1, 1)].routes[0].endpoint, route_to_restore_first)  # pyright: ignore
    assert {
        get_wrapped_endpoint(routers.endpoints[date(2000, 1, 1)].routes[0].endpoint),  # pyright: ignore
        get_wrapped_endpoint(routers.endpoints[date(2000, 1, 1)].routes[1].endpoint),  # pyright: ignore
    } == {
        route_to_restore_first,
        route_to_restore_second,
    }


def get_nested_field_type(annotation: Any) -> type[BaseModel] | None:
    get_args(annotation)[1]
    first_generic_arg_of_second_generic_arg = get_args(get_args(annotation)[1])[0]
    its_fields = first_generic_arg_of_second_generic_arg.model_fields
    annotation_of_its_foo_field = its_fields["foo"].annotation
    assert annotation_of_its_foo_field is not None
    return annotation_of_its_foo_field.model_fields["foo"].annotation


def test__router_generation__re_creating_a_non_endpoint__error(
    create_versioned_app: CreateVersionedApp,
):
    with pytest.raises(
        RouterGenerationError,
        match=re.escape(
            'Endpoint "[\'GET\'] /test" you tried to restore in "MyVersionChange" wasn\'t among the deleted routes',
        ),
    ):
        create_versioned_app(version_change(endpoint("/test", ["GET"]).existed))


def test__router_generation__changing_attribute_to_the_same_value__error(
    test_endpoint: Endpoint,
    test_path: str,
    create_versioned_app: CreateVersionedApp,
):
    with pytest.raises(
        RouterGenerationError,
        match=re.escape(
            'Expected attribute "path" of endpoint "[\'GET\'] /test/{hewwo}" to be different in "MyVersionChange", but '
            "it was the same. It means that your version change has no effect on the attribute and can be removed.",
        ),
    ):
        create_versioned_app(version_change(endpoint(test_path, ["GET"]).had(path=test_path)))


def test__router_generation__non_api_route_added(
    router: VersionedAPIRouter,
    test_endpoint: Endpoint,
    test_path: str,
    create_versioned_app: CreateVersionedApp,
):
    @router.websocket("/test2")
    async def test_websocket():
        raise NotImplementedError

    app = create_versioned_app(version_change(endpoint(test_path, ["GET"]).didnt_exist))
    assert len(app.router.versioned_routers[date(2000, 1, 1)].routes) == 2
    assert len(app.router.versioned_routers[date(2001, 1, 1)].routes) == 3
    route = app.router.versioned_routers[date(2001, 1, 1)].routes[1]
    assert isinstance(route, APIRoute)
    assert endpoints_equal(route.endpoint, test_endpoint)


def test__router_generation__updating_response_model(
    router: VersionedAPIRouter,
    create_versioned_app: CreateVersionedApp,
):
    @router.get(
        "/test",
        response_model=dict[str, list[SchemaWithOnePydanticField]],
    )
    async def test():
        raise NotImplementedError

    app = create_versioned_app(version_change(schema(SchemaWithOneIntField).field("foo").had(type=list[str])))
    schemas = generate_versioned_models(app.versions)

    routes_2000 = cast(list[APIRoute], app.router.versioned_routers[date(2000, 1, 1)].routes)
    routes_2001 = cast(list[APIRoute], app.router.versioned_routers[date(2001, 1, 1)].routes)

    assert len(routes_2000) == len(routes_2001) == 2

    schema_2000 = schemas["2000-01-01"][SchemaWithOnePydanticField]
    assert routes_2000[1].response_model == dict[str, list[schema_2000]]

    schema_2001 = schemas["2001-01-01"][SchemaWithOnePydanticField]
    assert routes_2001[1].response_model == dict[str, list[schema_2001]]

    assert get_nested_field_type(routes_2000[1].response_model) == list[str]
    assert get_nested_field_type(routes_2001[1].response_model) == int  # noqa: E721


def test__router_generation__using_unversioned_schema_from_versioned_base_dir__should_not_raise_error(
    router: VersionedAPIRouter,
    create_versioned_app: CreateVersionedApp,
):
    module = importlib.import_module("tests._data.unversioned_schema_dir")

    @router.post("/testik")
    async def testik(body: module.UnversionedSchema2):
        raise NotImplementedError

    create_versioned_app()


def test__router_generation__updating_request_models(
    router: VersionedAPIRouter,
    create_versioned_app: CreateVersionedApp,
):
    @router.get("/test")
    async def test(body: dict[str, list[SchemaWithOnePydanticField]]):
        raise NotImplementedError

    app = create_versioned_app(version_change(schema(SchemaWithOneIntField).field("foo").had(type=list[str])))
    schemas = generate_versioned_models(app.versions)

    routes_2000 = cast(list[APIRoute], app.router.versioned_routers[date(2000, 1, 1)].routes)
    routes_2001 = cast(list[APIRoute], app.router.versioned_routers[date(2001, 1, 1)].routes)
    assert len(routes_2000) == len(routes_2001) == 2

    body_param_2000 = routes_2000[1].dependant.body_params[0]
    schema_2000 = schemas["2000-01-01"][SchemaWithOnePydanticField]
    assert getattr(body_param_2000, TYPE_ATTR) == dict[str, list[schema_2000]]

    body_param_2001 = routes_2001[1].dependant.body_params[0]
    schema_2001 = schemas["2001-01-01"][SchemaWithOnePydanticField]
    assert getattr(body_param_2001, TYPE_ATTR) == dict[str, list[schema_2001]]

    assert get_nested_field_type(getattr(routes_2000[1].dependant.body_params[0], TYPE_ATTR)) == list[str]
    assert get_nested_field_type(getattr(routes_2001[1].dependant.body_params[0], TYPE_ATTR)) is int


def test__router_generation__updating_request_models_with_inheritance(
    router: VersionedAPIRouter,
    create_versioned_app: CreateVersionedApp,
):
    class ParentSchema(BaseModel):
        foo: str
        bar: int

    class ChildSchema(ParentSchema):
        pass

    @router.get("/test")
    async def test(body: ChildSchema):
        raise NotImplementedError

    app = create_versioned_app(version_change(schema(ParentSchema).field("foo").didnt_exist))

    routes_2000 = cast(list[APIRoute], app.router.versioned_routers[date(2000, 1, 1)].routes)
    routes_2001 = cast(list[APIRoute], app.router.versioned_routers[date(2001, 1, 1)].routes)
    assert len(routes_2000) == len(routes_2001) == 2

    body_param_2000 = routes_2000[1].dependant.body_params[0]
    body_param_2001 = routes_2001[1].dependant.body_params[0]
    assert set(getattr(body_param_2000, TYPE_ATTR).model_fields) == {"bar"}
    assert set(getattr(body_param_2001, TYPE_ATTR).model_fields) == {"foo", "bar"}


def test__router_generation__using_unversioned_models(
    router: VersionedAPIRouter,
    create_versioned_app: CreateVersionedApp,
):
    """Essentially legacy from codegen times"""

    @router.get("/test")
    async def test1(body: UnversionedSchema1):
        raise NotImplementedError

    @router.get("/test2")
    async def test2(body: UnversionedSchema2):
        raise NotImplementedError

    @router.get("/test3")
    async def test3(body: UnversionedSchema3):
        raise NotImplementedError

    app = create_versioned_app(version_change(schema(SchemaWithOneIntField).field("foo").had(type=list[str])))
    schemas = generate_versioned_models(app.versions)

    routes_2000 = cast(list[APIRoute], app.router.versioned_routers[date(2000, 1, 1)].routes)
    routes_2001 = cast(list[APIRoute], app.router.versioned_routers[date(2001, 1, 1)].routes)

    assert len(routes_2000) == len(routes_2001) == 4
    assert routes_2000[1].dependant.body_params[0].type_ is schemas["2000-01-01"][UnversionedSchema1]
    assert routes_2001[1].dependant.body_params[0].type_ is schemas["2001-01-01"][UnversionedSchema1]

    assert routes_2000[2].dependant.body_params[0].type_ is schemas["2000-01-01"][UnversionedSchema2]
    assert routes_2001[2].dependant.body_params[0].type_ is schemas["2001-01-01"][UnversionedSchema2]

    assert routes_2000[3].dependant.body_params[0].type_ is schemas["2000-01-01"][UnversionedSchema3]
    assert routes_2001[3].dependant.body_params[0].type_ is schemas["2001-01-01"][UnversionedSchema3]


def test__router_generation__using_weird_typehints(
    router: VersionedAPIRouter,
    create_versioned_api_routes: CreateVersionedAPIRoutes,
):
    newtype = NewType("newtype", str)

    @router.get("/test")
    async def test(param1: newtype = Body(), param2: str | int = Body()):
        raise NotImplementedError

    routes_2000, routes_2001 = create_versioned_api_routes(
        version_change(schema(SchemaWithOneIntField).field("foo").had(type=list[str])),
    )
    assert len(routes_2000) == len(routes_2001) == 2

    assert getattr(routes_2000[1].dependant.body_params[0], TYPE_ATTR) is newtype
    assert getattr(routes_2001[1].dependant.body_params[0], TYPE_ATTR) is newtype

    assert getattr(routes_2000[1].dependant.body_params[1], TYPE_ATTR) == str | int
    assert getattr(routes_2001[1].dependant.body_params[1], TYPE_ATTR) == str | int


def test__router_generation__using_pydantic_typehints__internal_pydantic_typehints_should_work(
    router: VersionedAPIRouter,
    create_versioned_api_routes: CreateVersionedAPIRoutes,
):
    @router.get("/test")
    async def test(file: UploadFile):
        raise NotImplementedError

    routes_2000, routes_2001 = create_versioned_api_routes(
        version_change(schema(SchemaWithOneIntField).field("foo").had(type=list[str])),
    )
    assert len(routes_2000) == len(routes_2001) == 2
    # We are intentionally not checking anything here. Our goal is to validate that there is no exception


def test__router_generation__updating_request_depends(
    router: VersionedAPIRouter, create_versioned_app: CreateVersionedApp
):
    def sub_dependency1(my_schema: EmptySchema) -> EmptySchema:
        return my_schema

    def dependency1(dep: EmptySchema = Depends(sub_dependency1)):
        return dep

    def sub_dependency2(my_schema: EmptySchema) -> EmptySchema:
        return my_schema

    # TASK: What if "a" gets deleted? https://github.com/zmievsa/cadwyn/issues/25
    def dependency2(
        dep: Annotated[EmptySchema, Depends(sub_dependency2)] = None,  # pyright: ignore[reportArgumentType]
    ):
        return dep

    @router.post("/test1")
    async def test_with_dep1(dep: EmptySchema = Depends(dependency1)):
        return dep

    @router.post("/test2")
    async def test_with_dep2(dep: EmptySchema = Depends(dependency2)):
        return dep

    app = create_versioned_app(version_change(schema(EmptySchema).field("foo").existed_as(type=str)))

    client_2000 = TestClient(app, headers={app.router.api_version_header_name: "2000-01-01"})
    client_2001 = TestClient(app, headers={app.router.api_version_header_name: "2001-01-01"})
    resp_from_test1 = client_2000.post("/test1", json={}).json()
    resp_from_test2 = client_2000.post("/test2", json={}).json()
    assert resp_from_test1 == {
        "detail": [
            {
                "type": "missing",
                "loc": ["body", "foo"],
                "msg": "Field required",
                "input": {},
            },
        ],
    }
    assert resp_from_test2 == {
        "detail": [
            {
                "type": "missing",
                "loc": ["body", "foo"],
                "msg": "Field required",
                "input": {},
            },
        ],
    }

    assert client_2000.post("/test1", json={"foo": "bar"}).json() == {}
    assert client_2000.post("/test2", json={"foo": "bar"}).json() == {}

    assert client_2001.post("/test1", json={}).json() == {}
    assert client_2001.post("/test1", json={"my_schema": {"foo": "bar"}}).json() == {}

    assert client_2001.post("/test2", json={}).json() == {}
    assert client_2001.post("/test2", json={"my_schema": {"foo": "bar"}}).json() == {}


def test__router_generation__using_unversioned_schema_in_body(
    router: VersionedAPIRouter, create_versioned_app: CreateVersionedApp
):
    class MySchema(BaseModel):
        bar: str

    @router.post("/test")
    async def test_with_dep1(dep: MySchema):
        return dep

    app = create_versioned_app(version_change())

    client_2000 = TestClient(app, headers={app.router.api_version_header_name: "2000-01-01"})
    client_2001 = TestClient(app, headers={app.router.api_version_header_name: "2001-01-01"})
    assert client_2000.post("/test", json={"bar": "hello"}).json() == {"bar": "hello"}
    assert client_2001.post("/test", json={"bar": "hello"}).json() == {"bar": "hello"}


def test__router_generation_updating_unused_dependencies__with_migration(
    router: VersionedAPIRouter,
    create_versioned_app: CreateVersionedApp,
):
    saved_enum_names = []

    async def dependency(my_enum: StrEnum):
        saved_enum_names.append(my_enum.name)

    @router.get("/test", dependencies=[Depends(dependency)])
    async def test_with_dep():
        pass

    def migration(request: Any):
        return None

    app = create_versioned_app(
        version_change(
            enum(StrEnum).didnt_have("a"),
            enum(StrEnum).had(b="1"),
            migration=convert_request_to_next_version_for("/test", ["GET"])(migration),
        ),
    )

    client_2000 = TestClient(app, headers={app.router.api_version_header_name: "2000-01-01"})
    client_2001 = TestClient(app, headers={app.router.api_version_header_name: "2001-01-01"})

    resp = client_2000.get("/test", params={"my_enum": "1"})
    assert resp.status_code == 200

    resp = client_2001.get("/test", params={"my_enum": "1"})
    assert resp.status_code == 200

    assert saved_enum_names == [
        "b",  # Fastapi called our dependency and got b in 2000
        "a",  # We called our dependency and got a in 2001
        "a",  # Fastapi called our dependency and got a in 2001
        "a",  # We called our dependency and got a in 2001
    ]


def test__router_generation__updating_callbacks(
    router: VersionedAPIRouter,
    create_versioned_app: CreateVersionedApp,
):
    callback_router = APIRouter()

    @callback_router.websocket_route("/{request}")
    def useless_callback():
        raise NotImplementedError

    @callback_router.get("{request.body}")
    def callback(body: SchemaWithOneIntField):
        raise NotImplementedError

    @router.post("/test", callbacks=callback_router.routes)
    async def test_with_callbacks(body: SchemaWithOneIntField):
        raise NotImplementedError

    app = create_versioned_app(
        version_change(schema(SchemaWithOneIntField).field("bar").existed_as(type=str)),
    )

    route = app.router.versioned_routers[date(2000, 1, 1)].routes[1]
    assert isinstance(route, APIRoute)
    assert route.callbacks is not None
    generated_callback = route.callbacks[1]
    assert isinstance(generated_callback, APIRoute)
    assert generated_callback.dependant.body_params[0].type_.model_fields["bar"].annotation is str

    route = app.router.versioned_routers[date(2001, 1, 1)].routes[1]
    assert isinstance(route, APIRoute)
    assert route.callbacks is not None
    generated_callback = route.callbacks[1]
    assert isinstance(generated_callback, APIRoute)
    assert "bar" not in generated_callback.dependant.body_params[0].type_.model_fields


def test__cascading_router_exists(router: VersionedAPIRouter, api_version_var: ContextVar[date | None]):
    @router.only_exists_in_older_versions
    @router.get("/test")
    async def test_with_dep1():
        return 83

    class V2002(VersionChange):
        description = ""
        instructions_to_migrate_to_previous_version = [endpoint("/test", ["GET"]).existed]

    versions = VersionBundle(
        Version(date(2002, 1, 1), V2002),
        Version(date(2001, 1, 1)),
        Version(date(2000, 1, 1)),
        api_version_var=api_version_var,
    )
    routers = generate_versioned_routers(router, versions=versions)

    assert client(routers.endpoints[date(2002, 1, 1)]).get("/test").json() == {"detail": "Not Found"}
    assert client(routers.endpoints[date(2001, 1, 1)]).get("/test").json() == 83
    assert client(routers.endpoints[date(2000, 1, 1)]).get("/test").json() == 83


def test__cascading_router_didnt_exist(
    router: VersionedAPIRouter,
    api_version_var: ContextVar[date | None],
):
    @router.get("/test")
    async def test_with_dep1():
        return 83

    class V2002(VersionChange):
        description = ""
        instructions_to_migrate_to_previous_version = [
            endpoint("/test", ["GET"]).didnt_exist,
        ]

    versions = VersionBundle(
        Version(date(2002, 1, 1), V2002),
        Version(date(2001, 1, 1)),
        Version(date(2000, 1, 1)),
        api_version_var=api_version_var,
    )
    routers = generate_versioned_routers(router, versions=versions)

    assert client(routers.endpoints[date(2002, 1, 1)]).get("/test").json() == 83

    assert client(routers.endpoints[date(2001, 1, 1)]).get("/test").json() == {
        "detail": "Not Found",
    }

    assert client(routers.endpoints[date(2000, 1, 1)]).get("/test").json() == {
        "detail": "Not Found",
    }


def test__generate_versioned_routers__two_routers(
    router: VersionedAPIRouter,
    test_endpoint: Endpoint,
    test_path: str,
    api_version_var: ContextVar[date | None],
):
    router2 = VersionedAPIRouter(prefix="/api2")

    @router2.get("/test")
    async def test_endpoint2():
        raise NotImplementedError

    class V2001(VersionChange):
        description = ""
        instructions_to_migrate_to_previous_version = [
            endpoint(test_path, ["GET"]).didnt_exist,
            endpoint("/api2/test", ["GET"]).had(description="Meaw"),
        ]

    versions = VersionBundle(
        Version(date(2001, 1, 1), V2001),
        Version(date(2000, 1, 1)),
        api_version_var=api_version_var,
    )

    root_router = APIRouter()
    root_router.include_router(router)
    root_router.include_router(router2)

    routers = generate_versioned_routers(root_router, versions=versions).endpoints
    assert all(type(r) is APIRouter for r in routers.values())
    assert len(routers[date(2001, 1, 1)].routes) == 2
    assert len(routers[date(2000, 1, 1)].routes) == 1
    assert {
        get_wrapped_endpoint(routers[date(2001, 1, 1)].routes[0].endpoint),  # pyright: ignore
        get_wrapped_endpoint(routers[date(2001, 1, 1)].routes[1].endpoint),  # pyright: ignore
    } == {
        test_endpoint,
        test_endpoint2,
    }
    assert endpoints_equal(routers[date(2000, 1, 1)].routes[0].endpoint, test_endpoint2)  # pyright: ignore
    assert endpoints_equal(routers[date(2000, 1, 1)].routes[0].endpoint, test_endpoint2)  # pyright: ignore


class MyHTTPBearer(HTTPBearer):
    pass


@pytest.mark.parametrize(
    ("security_cls", "expected_status_code"),
    [(HTTPBearer, 403), (MyHTTPBearer, 403), (HTTPBasic, 401)],
)
def test__basic_router_generation__using_http_security_dependency__should_generate_the_required_security_params(
    router: VersionedAPIRouter,
    create_versioned_clients: CreateVersionedClients,
    security_cls: type[HTTPBearer] | type[MyHTTPBearer] | type[HTTPBasic],
    expected_status_code: int,
):
    auth_header_scheme = security_cls(description="Bearer token for authentication")

    def auth(
        auth_header: Annotated[
            HTTPAuthorizationCredentials | None,
            Depends(auth_header_scheme),
        ],
    ):
        raise NotImplementedError

    @router.get("/test", dependencies=[Depends(auth)])
    async def test():
        raise NotImplementedError

    client_2000, *_ = create_versioned_clients().values()

    dependant = cast(APIRoute, client_2000.app.router.versioned_routers[date(2000, 1, 1)].routes[-1]).dependant
    assert dependant.dependencies[1].dependencies[0].security_requirements[0].security_scheme is auth_header_scheme
    response = client_2000.get("/test")
    assert response.status_code == expected_status_code
    assert response.json() == {"detail": "Not authenticated"}


def test__basic_router_generation__using_custom_class_based_dependency__should_migrate_as_usual(
    router: VersionedAPIRouter,
    create_versioned_clients: CreateVersionedClients,
):
    payloads_dependency_was_called_with = []

    class MyCustomDependency:
        def __call__(self, my_body: SchemaWithOneIntField):
            payloads_dependency_was_called_with.append(my_body.model_dump())
            return my_body

    @router.post("/test")
    async def route(dependency: Any = Depends(MyCustomDependency())):
        return dependency

    client_2000, client_2001 = create_versioned_clients(
        version_change(schema(SchemaWithOneIntField).field("bar").existed_as(type=str)),
    ).values()

    response = client_2000.post("/test", json={"foo": 3, "bar": "meaw"})
    assert response.status_code == 200
    assert response.json() == {"foo": 3}

    response = client_2001.post("/test", json={"foo": 3})
    assert response.status_code == 200
    assert response.json() == {"foo": 3}

    # This is not a nice behavior but this is the way Cadwyn functions: the dependency is going to be called
    # twice: once by fastapi with solve_dependencies for the old version and once by Cadwyn
    # with solve_dependencies for the new version.
    assert payloads_dependency_was_called_with == [
        {"foo": 3, "bar": "meaw"},  # client_2000
        {"foo": 3},  # client_2000
        {"foo": 3},  # client_2001
        {"foo": 3},  # client_2001
    ]


def test__basic_router_generation__subclass_of_security_class_based_dependency_with_overridden_call__will_not_migrate(
    router: VersionedAPIRouter,
    create_versioned_clients: CreateVersionedClients,
):
    payloads_dependency_was_called_with = []

    class MyCustomDependency(HTTPBearer):
        def __call__(self, my_body: SchemaWithOneIntField):  # pyright: ignore[reportIncompatibleMethodOverride]
            payloads_dependency_was_called_with.append(my_body.model_dump())
            return my_body

    @router.post("/test")
    async def route(dependency: Any = Depends(MyCustomDependency())):
        return dependency

    client_2000, client_2001 = create_versioned_clients(
        version_change(schema(SchemaWithOneIntField).field("bar").existed_as(type=str)),
    ).values()

    response = client_2000.post("/test", json={"foo": 3, "bar": "meaw"})
    assert response.status_code == 200
    assert response.json() == {"foo": 3}

    response = client_2001.post("/test", json={"foo": 3})
    assert response.status_code == 200
    assert response.json() == {"foo": 3}

    # I.e. It was not migrated at all because we do not migrate any security classes that belong to FastAPI
    assert payloads_dependency_was_called_with == [
        {"foo": 3},  # client_2000
        {"foo": 3},  # client_2000
        {"foo": 3},  # client_2001
        {"foo": 3},  # client_2001
    ]


######################
# External lib testing
######################


def test__router_generation__using_svcs_in_dependencies(
    router: VersionedAPIRouter,
    create_versioned_api_routes: CreateVersionedAPIRoutes,
):
    # This makes sure that svcs is compatible with Cadwyn
    @router.api_route("/test")
    async def test(my_dep: svcs.fastapi.DepContainer):
        raise NotImplementedError

    # We are simply validating that the routes were generated without errors
    routes_2000, routes_2001 = create_versioned_api_routes(version_change())
    assert len(routes_2000) == len(routes_2001) == 2
