import re
from collections.abc import Awaitable, Callable
from datetime import date
from types import ModuleType
from typing import Annotated, Any, TypeAlias, cast, get_args

import pytest
from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel
from starlette.responses import FileResponse

from tests._data import latest
from tests._data.latest import some_schema
from tests.test_codegen import (
    generate_test_version_packages,
)

# TODO: It's bad to import between tests like that
from universi import VersionedAPIRouter, Versions
from universi.exceptions import RouterGenerationError
from universi.structure import Version, endpoint, schema
from universi.structure.endpoints import AlterEndpointSubInstruction
from universi.structure.enums import AlterEnumSubInstruction, enum
from universi.structure.schemas import AlterSchemaSubInstruction
from universi.structure.versions import AbstractVersionChange

Endpoint: TypeAlias = Callable[..., Awaitable[Any]]


@pytest.fixture()
def router() -> VersionedAPIRouter:
    return VersionedAPIRouter()


@pytest.fixture()
def test_endpoint(router: VersionedAPIRouter) -> Endpoint:
    @router.get("/test")
    async def test():
        raise NotImplementedError

    return test


def create_versioned_copies(
    router: VersionedAPIRouter,
    *instructions: AlterSchemaSubInstruction | AlterEndpointSubInstruction | AlterEnumSubInstruction,
    latest_schemas_module: ModuleType | None = None,
) -> dict[date, VersionedAPIRouter]:
    class VersionChange(AbstractVersionChange):
        description = "..."
        instructions_to_migrate_to_previous_version = instructions

    return router.create_versioned_copies(
        Versions(
            Version(date(2001, 1, 1), VersionChange),
            Version(date(2000, 1, 1)),
        ),
        latest_schemas_module=latest_schemas_module,
    )


def create_versioned_api_routes(
    router: VersionedAPIRouter,
    *instructions: AlterSchemaSubInstruction | AlterEndpointSubInstruction | AlterEnumSubInstruction,
    latest_schemas_module: ModuleType | None = None,
) -> tuple[list[APIRoute], list[APIRoute]]:
    routers = create_versioned_copies(
        router,
        *instructions,
        latest_schemas_module=latest_schemas_module,
    )
    for router in routers.values():
        for route in router.routes:
            assert isinstance(route, APIRoute)
    return cast(
        tuple[list[APIRoute], list[APIRoute]],
        (routers[date(2000, 1, 1)].routes, routers[date(2001, 1, 1)].routes),
    )


def test__router_generation__forgot_to_generate_schemas__error(
    router: VersionedAPIRouter,
):
    with pytest.raises(
        RouterGenerationError,
        match="Versioned schema directory '.+' does not exist.",
    ):
        create_versioned_api_routes(router, latest_schemas_module=latest)


def test__endpoint_didnt_exist(router: VersionedAPIRouter, test_endpoint: Endpoint):
    routes_2000, routes_2001 = create_versioned_api_routes(
        router,
        endpoint(test_endpoint).didnt_exist,
    )

    assert routes_2000 == []
    assert len(routes_2001) == 1
    assert routes_2001[0].endpoint.func == test_endpoint


# TODO: Add a test for removing an endpoint and adding it back
def test__endpoint_existed(router: VersionedAPIRouter):
    @router.only_exists_in_older_versions
    @router.get("/test")
    async def test_endpoint():
        raise NotImplementedError

    routes_2000, routes_2001 = create_versioned_api_routes(
        router,
        endpoint(test_endpoint).existed,
    )

    assert len(routes_2000) == 1
    assert routes_2001 == []
    assert routes_2000[0].endpoint.func == test_endpoint


@pytest.mark.parametrize(
    ("attr", "attr_value"),
    [
        ("path", "/wow"),
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
        ("methods", ["GET", "POST"]),
        ("operation_id", "my_operation_id"),
        ("response_class", FileResponse),
        ("dependencies", [Depends(lambda: "hewwo")]),  # pragma: no cover
        (
            "generate_unique_id_function",
            lambda api_route: api_route.endpoint.__name__,
        ),  # pragma: no cover
    ],
)
def test__endpoint_had(
    router: VersionedAPIRouter,
    attr: str,
    attr_value: Any,
    test_endpoint: Endpoint,
):
    routes_2000, routes_2001 = create_versioned_api_routes(
        router,
        endpoint(test_endpoint).had(**{attr: attr_value}),
    )

    assert len(routes_2000) == len(routes_2001) == 1
    assert getattr(routes_2000[0], attr) == attr_value
    assert getattr(routes_2001[0], attr) != attr_value


def test__endpoint_only_exists_in_older_versions__endpoint_is_not_a_route__error(
    router: VersionedAPIRouter,
    test_endpoint: Endpoint,
):
    with pytest.raises(
        LookupError,
        match=re.escape("Route not found on endpoint: 'test2'"),
    ):

        @router.only_exists_in_older_versions
        async def test2():
            raise NotImplementedError


def test__router_generation__non_api_route_added(
    router: VersionedAPIRouter,
    test_endpoint: Endpoint,
):
    @router.websocket("/test2")
    async def test_websocket():
        raise NotImplementedError

    routers = create_versioned_copies(router, endpoint(test_endpoint).didnt_exist)
    assert len(routers[date(2000, 1, 1)].routes) == 1
    assert len(routers[date(2001, 1, 1)].routes) == 2
    route = routers[date(2001, 1, 1)].routes[0]
    assert isinstance(route, APIRoute)
    assert route.endpoint.func == test_endpoint


def test__router_generation__creating_a_synchronous_endpoint__error(
    router: VersionedAPIRouter,
):
    @router.get("/test")
    def test():
        raise NotImplementedError

    with pytest.raises(
        TypeError,
        match=re.escape("All versioned endpoints must be asynchronous."),
    ):
        create_versioned_copies(router, endpoint(test).didnt_exist)


def test__router_generation__changing_a_deleted_endpoint__error(
    router: VersionedAPIRouter,
):
    @router.only_exists_in_older_versions
    @router.get("/test")
    async def test():
        raise NotImplementedError

    with pytest.raises(
        RouterGenerationError,
        match=re.escape(
            "Endpoint 'test' you tried to delete in 'VersionChange' doesn't exist in new version",
        ),
    ):
        create_versioned_copies(router, endpoint(test).had(description="Hewwo"))


def test__router_generation__deleting_a_deleted_endpoint__error(
    router: VersionedAPIRouter,
):
    @router.only_exists_in_older_versions
    @router.get("/test")
    async def test():
        raise NotImplementedError

    with pytest.raises(
        RouterGenerationError,
        match=re.escape(
            "Endpoint 'test' you tried to delete in 'VersionChange' doesn't exist in new version",
        ),
    ):
        create_versioned_copies(router, endpoint(test).didnt_exist)


def test__router_generation__re_creating_an_existing_endpoint__error(
    router: VersionedAPIRouter,
    test_endpoint: Endpoint,
):
    with pytest.raises(
        RouterGenerationError,
        match=re.escape(
            "Endpoint 'test' you tried to re-create in 'VersionChange' already existed in newer versions",
        ),
    ):
        create_versioned_copies(router, endpoint(test_endpoint).existed)


def get_nested_field_type(annotation: Any) -> type[BaseModel]:
    return get_args(get_args(annotation)[1])[0].__fields__["foo"].type_.__fields__["foo"].annotation


def test__router_generation__re_creating_a_non_endpoint__error(
    router: VersionedAPIRouter,
):
    async def test():
        raise NotImplementedError

    with pytest.raises(
        RouterGenerationError,
        match=re.escape(
            "Endpoint 'test' you tried to re-create in 'VersionChange' wasn't among the deleted routes",
        ),
    ):
        create_versioned_copies(router, endpoint(test).existed)


def test__router_generation__non_api_route_added_with_schemas(
    router: VersionedAPIRouter,
    test_endpoint: Endpoint,
):
    @router.websocket("/test2")
    async def test_websocket():
        raise NotImplementedError

    generate_test_version_packages()
    routers = create_versioned_copies(
        router,
        endpoint(test_endpoint).didnt_exist,
        latest_schemas_module=latest,
    )
    assert len(routers[date(2000, 1, 1)].routes) == 1
    assert len(routers[date(2001, 1, 1)].routes) == 2
    route = routers[date(2001, 1, 1)].routes[0]
    assert isinstance(route, APIRoute)
    assert route.endpoint.func == test_endpoint


def test__router_generation__updating_response_model_when_schema_is_defined_in_a_non_init_file(
    router: VersionedAPIRouter,
    reload_autogenerated_modules: None,
):
    @router.get("/test", response_model=some_schema.MySchema)
    async def test():
        raise NotImplementedError

    instruction = schema(some_schema.MySchema).field("foo").had(type=str)
    generate_test_version_packages(instruction)

    routes_2000, routes_2001 = create_versioned_api_routes(
        router,
        instruction,
        latest_schemas_module=latest,
    )
    assert routes_2000[0].response_model.__fields__["foo"].annotation == str
    assert routes_2001[0].response_model.__fields__["foo"].annotation == int


def test__router_generation__updating_response_model(
    router: VersionedAPIRouter,
    reload_autogenerated_modules: None,
):
    @router.get(
        "/test",
        response_model=dict[str, list[latest.SchemaWithOnePydanticField]],
    )
    async def test():
        raise NotImplementedError

    instruction = schema(latest.SchemaWithOneIntField).field("foo").had(type=list[str])
    schemas_2000, schemas_2001 = generate_test_version_packages(instruction)

    routes_2000, routes_2001 = create_versioned_api_routes(
        router,
        instruction,
        latest_schemas_module=latest,
    )
    assert len(routes_2000) == len(routes_2001) == 1
    assert routes_2000[0].response_model == dict[str, list[schemas_2000.SchemaWithOnePydanticField]]
    assert routes_2001[0].response_model == dict[str, list[schemas_2001.SchemaWithOnePydanticField]]

    assert get_nested_field_type(routes_2000[0].response_model) == list[str]
    assert get_nested_field_type(routes_2001[0].response_model) == int


def test__router_generation__updating_request_models(
    router: VersionedAPIRouter,
    reload_autogenerated_modules: None,
):
    @router.get("/test")
    async def test(body: dict[str, list[latest.SchemaWithOnePydanticField]]):
        raise NotImplementedError

    instruction = schema(latest.SchemaWithOneIntField).field("foo").had(type=list[str])
    schemas_2000, schemas_2001 = generate_test_version_packages(instruction)

    routes_2000, routes_2001 = create_versioned_api_routes(
        router,
        instruction,
        latest_schemas_module=latest,
    )
    assert len(routes_2000) == len(routes_2001) == 1
    assert (
        routes_2000[0].dependant.body_params[0].annotation == dict[str, list[schemas_2000.SchemaWithOnePydanticField]]
    )
    assert (
        routes_2001[0].dependant.body_params[0].annotation == dict[str, list[schemas_2001.SchemaWithOnePydanticField]]
    )

    assert get_nested_field_type(routes_2000[0].dependant.body_params[0].annotation) == list[str]
    assert get_nested_field_type(routes_2001[0].dependant.body_params[0].annotation) == int


# TODO: This test should become multiple tests
def test__router_generation__updating_request_depends(
    router: VersionedAPIRouter,
    reload_autogenerated_modules: None,
):
    def sub_dependency1(my_enum: latest.StrEnum):
        return my_enum

    def dependency1(dep=Depends(sub_dependency1)):
        return dep

    def sub_dependency2(my_enum: latest.StrEnum):
        return my_enum

    # TODO: What if "a" gets deleted?
    def dependency2(
        dep: Annotated[latest.StrEnum, Depends(sub_dependency2)] = latest.StrEnum.a,
    ):
        return dep

    @router.get("/test1")
    async def test_with_dep1(dep=Depends(dependency1)):
        return dep

    @router.get("/test2")
    async def test_with_dep2(dep=Depends(dependency2)):
        return dep

    instruction = enum(latest.StrEnum).had(foo="bar")
    generate_test_version_packages(instruction)

    routers = create_versioned_copies(router, instruction, latest_schemas_module=latest)
    app_2000 = FastAPI()
    app_2001 = FastAPI()
    app_2000.include_router(routers[date(2000, 1, 1)])
    app_2001.include_router(routers[date(2001, 1, 1)])
    client_2000 = TestClient(app_2000)
    client_2001 = TestClient(app_2001)
    assert client_2000.get("/test1", params={"my_enum": "bar"}).json() == "bar"
    assert client_2000.get("/test2", params={"my_enum": "bar"}).json() == "bar"

    # insert_assert(client_2001.get("/test1", params={"my_enum": "bar"}).json())
    assert client_2001.get("/test1", params={"my_enum": "bar"}).json() == {
        "detail": [
            {
                "loc": ["query", "my_enum"],
                "msg": "value is not a valid enumeration member; permitted: '1'",
                "type": "type_error.enum",
                "ctx": {"enum_values": ["1"]},
            },
        ],
    }
    # insert_assert(client_2001.get("/test2", params={"my_enum": "bar"}).json())
    assert client_2001.get("/test2", params={"my_enum": "bar"}).json() == {
        "detail": [
            {
                "loc": ["query", "my_enum"],
                "msg": "value is not a valid enumeration member; permitted: '1'",
                "type": "type_error.enum",
                "ctx": {"enum_values": ["1"]},
            },
        ],
    }


def test__router_generation__updating_unused_dependencies(
    router: VersionedAPIRouter,
    reload_autogenerated_modules: None,
):
    def dependency(my_enum: latest.StrEnum):
        return my_enum

    @router.get("/test", dependencies=[Depends(dependency)])
    async def test_with_dep():
        pass

    instruction = enum(latest.StrEnum).had(foo="bar")
    generate_test_version_packages(instruction)

    routers = create_versioned_copies(router, instruction, latest_schemas_module=latest)
    app_2000 = FastAPI()
    app_2001 = FastAPI()
    app_2000.include_router(routers[date(2000, 1, 1)])
    app_2001.include_router(routers[date(2001, 1, 1)])
    client_2000 = TestClient(app_2000)
    client_2001 = TestClient(app_2001)
    assert client_2000.get("/test", params={"my_enum": "bar"}).json() is None

    # insert_assert(client_2001.get("/test1", params={"my_enum": "bar"}).json())
    assert client_2001.get("/test", params={"my_enum": "bar"}).json() == {
        "detail": [
            {
                "loc": ["query", "my_enum"],
                "msg": "value is not a valid enumeration member; permitted: '1'",
                "type": "type_error.enum",
                "ctx": {"enum_values": ["1"]},
            },
        ],
    }
