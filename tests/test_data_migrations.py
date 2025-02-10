from __future__ import annotations

import http.cookies
import re
from collections.abc import Callable, Coroutine
from contextvars import ContextVar
from datetime import date
from io import StringIO
from typing import Any, Literal

import fastapi
import pytest
from dirty_equals import IsPartialDict, IsStr
from fastapi import APIRouter, Body, Cookie, File, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field, RootModel
from starlette.responses import StreamingResponse

from cadwyn import VersionedAPIRouter
from cadwyn.exceptions import (
    CadwynError,
    CadwynHeadRequestValidationError,
    RouteByPathConverterDoesNotApplyToAnythingError,
    RouteRequestBySchemaConverterDoesNotApplyToAnythingError,
    RouteResponseBySchemaConverterDoesNotApplyToAnythingError,
)
from cadwyn.schema_generation import migrate_response_body
from cadwyn.structure import (
    VersionChange,
    convert_request_to_next_version_for,
    convert_response_to_previous_version_for,
)
from cadwyn.structure.data import RequestInfo, ResponseInfo
from cadwyn.structure.schemas import schema
from cadwyn.structure.versions import Version, VersionBundle
from tests.conftest import (
    CreateVersionedClients,
    client,
    version_change,
)


@pytest.fixture
def test_path():
    return "/test"


class EmptySchema(BaseModel):
    pass


# so `RootModel[Any] is RootModel[Any]`. This causes dire consequences if you try to make "different"
# request and response root models with the same definitions
class AnyRequestSchema(RootModel[Any]):
    pass


class AnyResponseSchema(RootModel[Any]):
    pass


class SchemaWithHeadMigrations(BaseModel):
    foo: int
    bar: str | None = Field(default=None)


class SchemaWithInternalRepresentation(BaseModel):
    foo: int


class InternalSchema(SchemaWithInternalRepresentation):
    bar: str | None = Field(default=None)


@pytest.fixture(params=["is_async", "is_sync"])
def _get_endpoint(
    request: pytest.FixtureRequest,
    test_path: str,
    router: VersionedAPIRouter,
):
    def _get_response_data(request: Request):
        return {
            "headers": dict(request.headers),
            "cookies": request.cookies,
            "query_params": dict(request.query_params),
        }

    if request.param == "is_async":

        @router.get(test_path, response_model=AnyResponseSchema)
        async def get_async_endpoint(request: Request):
            return _get_response_data(request)

    else:

        @router.get(test_path, response_model=AnyResponseSchema)
        def get_sync_endpoint(request: Request):
            return _get_response_data(request)


@pytest.fixture(params=["is_async", "is_sync"])
def _post_endpoint(request, test_path: str, router: VersionedAPIRouter):
    def _get_request_data(request: Request, body: AnyRequestSchema):
        return {
            "body": body,
            "headers": dict(request.headers),
            "cookies": request.cookies,
            "query_params": dict(request.query_params),
        }

    if request.param == "is_async":

        @router.post(test_path, response_model=AnyResponseSchema)
        async def post_async_endpoint(request: Request, body: AnyRequestSchema):
            return _get_request_data(request, body)

    else:

        @router.post(test_path, response_model=AnyResponseSchema)
        def post_sync_endpoint(request: Request, body: AnyRequestSchema):
            return _get_request_data(request, body)


@pytest.fixture(params=["by path", "by schema"])
def version_change_1(
    request,
    test_path: str,
):
    if request.param == "by path":
        convert_request = convert_request_to_next_version_for(test_path, ["POST"])
        convert_response = convert_response_to_previous_version_for(test_path, ["POST"])
    else:
        convert_request = convert_request_to_next_version_for(AnyRequestSchema)
        convert_response = convert_response_to_previous_version_for(AnyResponseSchema)

    @convert_request
    def change_address_to_multiple_items(request: RequestInfo):
        request.body.append("request change 1")

    @convert_response
    def change_addresses_to_single_item(response: ResponseInfo) -> None:
        response.body["body"].append("response change 1")

    return version_change(
        change_address_to_multiple_items=change_address_to_multiple_items,
        change_addresses_to_single_item=change_addresses_to_single_item,
    )


@pytest.fixture
def version_change_2():
    @convert_request_to_next_version_for(AnyRequestSchema)
    def change_addresses_to_default_address(request: RequestInfo):
        request.body.append("request change 2")

    @convert_response_to_previous_version_for(AnyResponseSchema)
    def change_addresses_to_list(response: ResponseInfo) -> None:
        response.body["body"].append("response change 2")

    return version_change(
        change_addresses_to_default_address=change_addresses_to_default_address,
        change_addresses_to_list=change_addresses_to_list,
    )


@pytest.fixture(params=["without_request", "with request"])
def _post_endpoint_with_extra_depends(
    request: pytest.FixtureRequest,
    router: VersionedAPIRouter,
    test_path: Literal["/test"],
    _post_endpoint: Callable[..., Coroutine[Any, Any, dict[str, Any]]],  # pyright: ignore[reportRedeclaration]
):
    if request.param == "without_request":
        router.routes = []

        @router.post(test_path)
        async def _post_endpoint(
            body: AnyRequestSchema,
            header_key: str | None = Header(default=None, alias="header_key"),
            n_header: str | None = Header(default=None, alias="3"),
            cookie_key: str | None = Cookie(default=None),
            n_cookie: str | None = Cookie(default=None, alias="5"),
            query_param_key: str | None = Query(default=None),
            n_query: str | None = Query(default=None, alias="7"),
        ):
            headers: Any = {"header_key": header_key}
            cookies: Any = {"cookie_key": cookie_key}
            query_params: Any = {"query_param_key": query_param_key}

            if n_header is not None:
                headers["3"] = n_header
                cookies["5"] = n_cookie
                query_params["7"] = n_query
            return {
                "body": body,
                "headers": dict(headers),
                "cookies": cookies,
                "query_params": dict(query_params),
            }


class TestRequestMigrations:
    def test__all_request_components_migration__post_endpoint__migration_filled_results_up(
        self,
        create_versioned_clients: CreateVersionedClients,
        test_path: Literal["/test"],
        _post_endpoint_with_extra_depends: None,
    ):
        @convert_request_to_next_version_for(AnyRequestSchema)
        def migrator(request: RequestInfo):
            request.body["hello"] = "hello"
            request.headers["header_key"] = "header val 2"
            request.cookies["cookie_key"] = "cookie val 2"
            request.query_params["query_param_key"] = "query_param val 2"

        clients = create_versioned_clients(version_change(migrator=migrator))

        assert clients[date(2000, 1, 1)].post(test_path, json={}).json() == {
            "body": {"hello": "hello"},
            "headers": IsPartialDict({"header_key": "header val 2"}),
            "cookies": {"cookie_key": "cookie val 2"},
            "query_params": {"query_param_key": "query_param val 2"},
        }

        clients[date(2000, 1, 1)].cookies["5"] = "6"
        assert clients[date(2000, 1, 1)].post(
            test_path,
            json={"1": "2"},
            headers={"3": "4"},
            params={"7": "8"},
        ).json() == {
            "body": {"1": "2", "hello": "hello"},
            "headers": IsPartialDict({"header_key": "header val 2", "3": "4"}),
            "cookies": {"cookie_key": "cookie val 2", "5": "6"},
            "query_params": {"query_param_key": "query_param val 2", "7": "8"},
        }

    def test__all_request_components_migration__get_endpoint__migration_filled_results_up(
        self,
        create_versioned_clients: CreateVersionedClients,
        test_path: Literal["/test"],
        router: VersionedAPIRouter,
    ):
        @router.get(test_path)
        async def get(request: Request):
            return {
                "body": await request.body(),
                "headers": dict(request.headers),
                "cookies": request.cookies,
                "query_params": dict(request.query_params),
            }

        @convert_request_to_next_version_for(test_path, ["GET"])
        def migrator(request: RequestInfo):
            request.headers["request2"] = "request2"
            request.cookies["request2"] = "request2"
            request.query_params["request2"] = "request2"

        clients = create_versioned_clients(version_change(migrator=migrator))
        assert clients[date(2000, 1, 1)].get(test_path).json() == {
            "body": "",
            "headers": IsPartialDict({"request2": "request2"}),
            "cookies": {"request2": "request2"},
            "query_params": {"request2": "request2"},
        }

    def test__depends_gets_broken_after_migration__should_raise_500(
        self,
        create_versioned_clients: CreateVersionedClients,
        router: VersionedAPIRouter,
        test_path,
    ):
        @router.get(test_path)
        async def get(my_header: str = Header()):
            return 83

        @convert_request_to_next_version_for(test_path, ["GET"])
        def migrator(request: RequestInfo):
            del request.headers["my-header"]

        clients = create_versioned_clients(version_change(migrator=migrator))

        assert clients[date(2001, 1, 1)].get(test_path, headers={"my-header": "wow"}).json() == 83
        with pytest.raises(CadwynHeadRequestValidationError):
            clients[date(2000, 1, 1)].get(test_path, headers={"my-header": "wow"}).json()

    def test__head_schema_migration__with_no_versioned_migrations__body_gets_parsed_to_head_schema(
        self,
        create_versioned_clients: CreateVersionedClients,
        test_path: Literal["/test"],
        router: VersionedAPIRouter,
    ):
        @router.post(test_path)
        async def route(payload: SchemaWithHeadMigrations):
            return payload

        clients = create_versioned_clients(
            version_change(),
            head_version_changes=[version_change(schema(SchemaWithHeadMigrations).field("bar").didnt_exist)],
        )

        # [-1] route is /openapi.json
        last_route = clients[date(2000, 1, 1)].app.router.versioned_routers[date(2000, 1, 1)].routes[-1]
        assert isinstance(last_route, APIRoute)

        assert clients[date(2000, 1, 1)].post(test_path, json={"foo": 1, "bar": "hewwo"}).json() == {
            "foo": 1,
            "bar": None,
        }
        assert clients[date(2001, 1, 1)].post(test_path, json={"foo": 1, "bar": "hewwo"}).json() == {
            "foo": 1,
            "bar": None,
        }

    def test__head_schema_migration__with_versioned_migrations__body_gets_parsed_to_internal_request_schema(
        self,
        create_versioned_clients: CreateVersionedClients,
        test_path: Literal["/test"],
        router: VersionedAPIRouter,
    ):
        @router.post(test_path)
        async def route(payload: SchemaWithHeadMigrations):
            return payload

        @convert_request_to_next_version_for(SchemaWithHeadMigrations)
        def migrator(request: RequestInfo):
            request.body["bar"] = "world"

        clients = create_versioned_clients(
            version_change(migrator=migrator),
            head_version_changes=[version_change(schema(SchemaWithHeadMigrations).field("bar").didnt_exist)],
        )

        assert clients[date(2000, 1, 1)].post(test_path, json={"foo": 1, "bar": "hewwo"}).json() == {
            "foo": 1,
            "bar": "world",
        }
        assert clients[date(2001, 1, 1)].post(test_path, json={"foo": 1, "bar": "hewwo"}).json() == {
            "foo": 1,
            "bar": None,
        }

    def test__head_schema_migration__with_invalid_versioned_migrations__internal_schema_validation_error(
        self,
        create_versioned_clients: CreateVersionedClients,
        test_path: Literal["/test"],
        router: VersionedAPIRouter,
    ):
        @router.post(test_path)
        async def route(payload: SchemaWithHeadMigrations):
            return payload

        @convert_request_to_next_version_for(SchemaWithHeadMigrations)
        def migrator(request: RequestInfo):
            request.body["bar"] = [1, 2, 3]

        clients = create_versioned_clients(
            version_change(migrator=migrator),
            head_version_changes=[version_change(schema(SchemaWithHeadMigrations).field("bar").didnt_exist)],
        )
        assert clients[date(2001, 1, 1)].post(test_path, json={"foo": 1, "bar": "hewwo"}).json() == {
            "foo": 1,
            "bar": None,
        }
        with pytest.raises(CadwynHeadRequestValidationError):
            clients[date(2000, 1, 1)].post(test_path, json={"foo": 1, "bar": "hewwo"}).json()

    def test__serialization_of_request_body__when_body_is_non_pydantic(
        self,
        create_versioned_clients: CreateVersionedClients,
        test_path: Literal["/test"],
        router: VersionedAPIRouter,
    ):
        @router.post(test_path)
        async def route(payload: dict = Body(None)):
            return payload

        payload = {"foo": "bar"}
        clients = create_versioned_clients(version_change())
        assert clients[date(2000, 1, 1)].post(url=test_path, json=payload).json() == payload
        assert clients[date(2001, 1, 1)].post(url=test_path, json=payload).json() == payload


class TestResponseMigrations:
    def test__all_response_components_migration__post_endpoint__migration_filled_results_up(
        self,
        create_versioned_clients: CreateVersionedClients,
        test_path: Literal["/test"],
        _post_endpoint: Callable[..., Coroutine[Any, Any, dict[str, Any]]],
    ):
        @convert_response_to_previous_version_for(AnyResponseSchema)
        def migrator(response: ResponseInfo):
            response.body["body_key"] = "body_val"
            assert response.status_code == 200
            response.status_code = 300
            response.headers["header"] = "header_val"
            response.set_cookie("cookie_key", "cookie_val", max_age=83)

        clients = create_versioned_clients(version_change(migrator=migrator))

        resp = clients[date(2000, 1, 1)].post(test_path, json={})

        assert resp.json() == {
            "body": {},
            "headers": {
                "host": "testserver",
                "accept": "*/*",
                "accept-encoding": "gzip, deflate",
                "connection": "keep-alive",
                "user-agent": "testclient",
                "content-length": "2",
                "content-type": "application/json",
                "x-api-version": "2000-01-01",
            },
            "cookies": {},
            "query_params": {},
            "body_key": "body_val",
        }
        assert dict(resp.headers) == {
            "content-length": "281",
            "content-type": "application/json",
            "header": "header_val",
            "set-cookie": "cookie_key=cookie_val; Max-Age=83; Path=/; SameSite=lax",
            "x-api-version": "2000-01-01",
        }
        assert dict(resp.cookies) == {"cookie_key": "cookie_val"}
        assert resp.status_code == 300

        clients[date(2000, 1, 1)].cookies["5"] = "6"
        resp = clients[date(2000, 1, 1)].post(test_path, json={"1": "2"}, headers={"3": "4"})
        assert resp.json() == {
            "body": {"1": "2"},
            "headers": {
                "host": "testserver",
                "accept": "*/*",
                "accept-encoding": "gzip, deflate",
                "connection": "keep-alive",
                "user-agent": "testclient",
                "3": "4",
                "cookie": IsStr(min_length=3),
                "content-length": "9",
                "content-type": "application/json",
                "x-api-version": "2000-01-01",
            },
            "cookies": {"5": "6", "cookie_key": "cookie_val"},
            "query_params": {},
            "body_key": "body_val",
        }
        assert dict(resp.headers) == {
            "content-length": "367",
            "content-type": "application/json",
            "header": "header_val",
            "set-cookie": "cookie_key=cookie_val; Max-Age=83; Path=/; SameSite=lax",
            "x-api-version": "2000-01-01",
        }
        assert dict(resp.cookies) == {"cookie_key": "cookie_val"}
        assert resp.status_code == 300

    def test__all_response_components_migration__get_endpoint__migration_filled_results_up(
        self,
        create_versioned_clients: CreateVersionedClients,
        test_path: Literal["/test"],
        _get_endpoint,
    ):
        @convert_response_to_previous_version_for(AnyResponseSchema)
        def migrator(response: ResponseInfo):
            response.status_code = 300
            response.headers["header_key"] = "header-val"
            response.set_cookie("cookie_key", "cookie_val", max_age=83)

        clients = create_versioned_clients(version_change(migrator=migrator))
        resp = clients[date(2000, 1, 1)].get(test_path)
        assert dict(resp.headers) == {
            "content-length": "194",
            "content-type": "application/json",
            "header_key": "header-val",
            "set-cookie": "cookie_key=cookie_val; Max-Age=83; Path=/; SameSite=lax",
            "x-api-version": "2000-01-01",
        }
        assert dict(resp.cookies) == {"cookie_key": "cookie_val"}
        assert resp.status_code == 300

    def test__fastapi_response_migration__post_endpoint(
        self,
        create_versioned_clients: CreateVersionedClients,
        test_path: Literal["/test"],
        router: VersionedAPIRouter,
    ):
        @router.post(test_path, response_model=AnyResponseSchema)
        async def post_endpoint(request: Request):
            return JSONResponse({"hewwo": "darkness"}, status_code=203, headers={"header-key": "header-val"})

        @convert_response_to_previous_version_for(AnyResponseSchema)
        def migrator(response: ResponseInfo):
            assert response.status_code == 203
            assert response.headers["header-key"] == "header-val"
            response.body |= {"migration": "body"}
            response.status_code = 201
            response.headers["header-key"] = "header-val2"

        clients = create_versioned_clients(version_change(migrator=migrator))
        resp = clients[date(2000, 1, 1)].post(test_path, json={})
        assert resp.json() == {"hewwo": "darkness", "migration": "body"}
        assert dict(resp.headers) == (
            {
                "header-key": "header-val2",
                "content-length": "39",
                "content-type": "application/json",
                "x-api-version": "2000-01-01",
            }
        )
        assert resp.status_code == 201
        assert dict(resp.cookies) == {}

        resp = clients[date(2001, 1, 1)].post(test_path, json={})
        assert resp.json() == {"hewwo": "darkness"}
        assert dict(resp.headers) == (
            {
                "header-key": "header-val",
                "content-length": "20",
                "content-type": "application/json",
                "x-api-version": "2001-01-01",
            }
        )
        assert resp.status_code == 203

    def test__fastapi_response_migration__response_only_has_status_code_and_there_is_a_migration(
        self,
        create_versioned_clients: CreateVersionedClients,
        test_path: Literal["/test"],
        router: VersionedAPIRouter,
    ):
        @router.post(test_path, response_model=AnyResponseSchema)
        async def post_endpoint(request: Request):
            return Response(status_code=200)

        @convert_response_to_previous_version_for(AnyResponseSchema)
        def migrator(response: ResponseInfo):
            response.status_code = 201

        clients = create_versioned_clients(version_change(migrator=migrator))
        resp = clients[date(2000, 1, 1)].post(test_path, json={})
        assert resp.content == b""
        assert dict(resp.headers) == (
            {
                "content-length": "0",
                "x-api-version": "2000-01-01",
            }
        )
        assert resp.status_code == 201
        assert dict(resp.cookies) == {}

        resp = clients[date(2001, 1, 1)].post(test_path, json={})
        assert resp.content == b""
        assert dict(resp.headers) == (
            {
                "content-length": "0",
                "x-api-version": "2001-01-01",
            }
        )
        assert resp.status_code == 200

    def test__fastapi_response_migration__streaming_response_and_there_is_a_migration(
        self,
        create_versioned_clients: CreateVersionedClients,
        test_path: Literal["/test"],
        router: VersionedAPIRouter,
    ):
        @router.post(test_path, response_model=AnyResponseSchema)
        async def post_endpoint(request: Request):
            return StreamingResponse(StringIO("streaming response"), status_code=200)

        @convert_response_to_previous_version_for(AnyResponseSchema)
        def migrator(response: ResponseInfo):
            response.status_code = 201

        clients = create_versioned_clients(version_change(migrator=migrator))
        resp = clients[date(2000, 1, 1)].post(test_path, json={})
        assert resp.content == b"streaming response"
        assert dict(resp.headers) == {"x-api-version": "2000-01-01"}
        assert resp.status_code == 201
        assert dict(resp.cookies) == {}

        resp = clients[date(2001, 1, 1)].post(test_path, json={})
        assert resp.content == b"streaming response"
        assert dict(resp.headers) == {"x-api-version": "2001-01-01"}
        assert resp.status_code == 200

    def test__fastapi_response_migration__response_only_has_status_code_and_there_is_no_migration(
        self,
        create_versioned_clients: CreateVersionedClients,
        test_path: Literal["/test"],
        router: VersionedAPIRouter,
    ):
        @router.post(test_path, response_model=AnyResponseSchema)
        async def post_endpoint(request: Request):
            return Response(status_code=200)

        clients = create_versioned_clients(version_change())
        resp = clients[date(2000, 1, 1)].post(test_path, json={})
        assert resp.content == b""
        assert dict(resp.headers) == (
            {
                "content-length": "0",
                "x-api-version": "2000-01-01",
            }
        )
        assert resp.status_code == 200
        assert dict(resp.cookies) == {}

        resp = clients[date(2001, 1, 1)].post(test_path, json={})
        assert resp.content == b""
        assert dict(resp.headers) == (
            {
                "content-length": "0",
                "x-api-version": "2001-01-01",
            }
        )
        assert resp.status_code == 200

    def test__fastapi_response_migration__with_custom_response(
        self,
        create_versioned_clients: CreateVersionedClients,
        test_path: Literal["/test"],
        router: VersionedAPIRouter,
    ):
        @router.post(test_path)
        async def post_endpoint(request: Request):
            return Response(status_code=200, content="Hello, world")

        @convert_response_to_previous_version_for(test_path, ["POST"])
        def converter(response: ResponseInfo):
            assert response.body == "Hello, world"

        client_2000, client_2001 = create_versioned_clients(version_change(converter=converter)).values()
        resp = client_2000.post(test_path, json={})
        assert resp.content == b"Hello, world"
        assert resp.status_code == 200

        resp = client_2001.post(test_path, json={})
        assert resp.content == b"Hello, world"
        assert resp.status_code == 200


class TestHowAndWhenMigrationsApply:
    def test__migrate_request_and_response__with_no_migrations__should_not_raise_error(
        self,
        test_path: Literal["/test"],
        create_versioned_clients: CreateVersionedClients,
        _post_endpoint: Callable[..., Coroutine[Any, Any, dict[str, Any]]],
    ):
        clients = create_versioned_clients()
        assert clients[date(2000, 1, 1)].post(test_path, json={"A": "B"}).json() == {
            "body": {"A": "B"},
            "headers": IsPartialDict(),
            "cookies": {},
            "query_params": {},
        }

    def test__migrate_request__with_no_migrations__request_schema_should_be_from_latest(
        self,
        create_versioned_clients: CreateVersionedClients,
        test_path: Literal["/test"],
        router: VersionedAPIRouter,
    ):
        @router.post(test_path, response_model=AnyResponseSchema)
        async def endpoint(foo: AnyRequestSchema):
            assert isinstance(foo, AnyRequestSchema), f"Request schema is from: {foo.__class__.__module__}"
            return {}

        clients = create_versioned_clients(version_change(), version_change())
        resp_2000 = clients[date(2000, 1, 1)].post(test_path, json={})
        assert resp_2000.status_code, resp_2000.json()

        resp_2001 = clients[date(2001, 1, 1)].post(test_path, json={})
        assert resp_2001.status_code, resp_2001.json()

        resp_2002 = clients[date(2002, 1, 1)].post(test_path, json={})
        assert resp_2002.status_code, resp_2002.json()

    def test__migrate_one_version_down__migrations_are_applied_to_2000_version_but_not_to_2000(
        self,
        version_change_1: type[VersionChange],
        create_versioned_clients: CreateVersionedClients,
        test_path: Literal["/test"],
        _post_endpoint,
    ):
        clients = create_versioned_clients(version_change_1)
        assert clients[date(2000, 1, 1)].post(test_path, json=[]).json()["body"] == [
            "request change 1",
            "response change 1",
        ]
        assert clients[date(2001, 1, 1)].post(test_path, json=[]).json()["body"] == []

    def test__migrate_two_versions_down__2002_applies_to_2001_and_2000_while_2001_only_applies_to_2000(
        self,
        create_versioned_clients: CreateVersionedClients,
        version_change_1: type[VersionChange],
        version_change_2: type[VersionChange],
        test_path: str,
        _post_endpoint,
    ):
        clients = create_versioned_clients(version_change_1, version_change_2)
        assert clients[date(2000, 1, 1)].post(test_path, json=[]).json()["body"] == [
            "request change 1",
            "request change 2",
            "response change 2",
            "response change 1",
        ]
        assert clients[date(2001, 1, 1)].post(test_path, json=[]).json()["body"] == [
            "request change 2",
            "response change 2",
        ]
        assert clients[date(2002, 1, 1)].post(test_path, json=[]).json()["body"] == []

    def test__try_migrating_when_version_is_none__no_migrations_get_applied(
        self,
        create_versioned_clients: CreateVersionedClients,
        version_change_1: type[VersionChange],
        version_change_2: type[VersionChange],
        test_path: str,
        api_version_var: ContextVar[date | None],
        _post_endpoint,
    ):
        clients = create_versioned_clients(version_change_1, version_change_2)
        app = clients[date(2000, 1, 1)].app
        none_client = client(
            APIRouter(routes=app.router.versioned_routers[date(2000, 1, 1)].routes),
            api_version=None,
            api_version_var=api_version_var,
        )
        # The version below is not actually used anywhere, but it's required so we pass a dummy one
        assert (
            none_client.post(
                test_path,
                json=[],
                headers={app.router.api_version_header_name: "2000-11-11"},
            ).json()["body"]
            == []
        )

    # TODO: An error is a better behavior here
    def test__try_migrating_to_version_below_earliest__undefined_behaior(
        self,
        create_versioned_clients: CreateVersionedClients,
        version_change_1: type[VersionChange],
        version_change_2: type[VersionChange],
        test_path: str,
        api_version_var: ContextVar[date | None],
        _post_endpoint,
    ):
        clients = create_versioned_clients(version_change_1, version_change_2)
        app = clients[date(2000, 1, 1)].app
        earlier_client = client(
            APIRouter(routes=app.router.versioned_routers[date(2000, 1, 1)].routes),
            api_version=date(1998, 2, 10),
            api_version_var=api_version_var,
        )
        assert earlier_client.post(
            test_path,
            json=[],
            headers={app.router.api_version_header_name: "2000-01-01"},
        ).json()["body"] == [
            "request change 1",
            "request change 2",
            "response change 2",
            "response change 1",
        ]

    def test__try_migrating_to_version_above_latest__no_migrations_get_applied(
        self,
        create_versioned_clients: CreateVersionedClients,
        version_change_1: type[VersionChange],
        version_change_2: type[VersionChange],
        test_path: str,
        api_version_var: ContextVar[date | None],
        _post_endpoint,
    ):
        clients = create_versioned_clients(version_change_1, version_change_2)
        app = clients[date(2000, 1, 1)].app
        assert (
            clients[date(2000, 1, 1)]
            .post(test_path, json=[], headers={app.router.api_version_header_name: "2050-01-01"})
            .json()["body"]
            == []
        )

    def test__cookies_can_be_deleted_during_migrations(
        self,
        create_versioned_clients: CreateVersionedClients,
        test_path: Literal["/test"],
        router: VersionedAPIRouter,
    ):
        @router.post(test_path, response_model=AnyResponseSchema)
        async def endpoint(response: Response):
            response.set_cookie("cookie_key", "cookie_val")
            return 83

        @convert_response_to_previous_version_for(AnyResponseSchema)
        def migration(response: ResponseInfo):
            response.delete_cookie("cookie_key")

        clients = create_versioned_clients(version_change(migration=migration))
        resp_2000 = clients[date(2000, 1, 1)].post(test_path, json={})
        resp_2001 = clients[date(2001, 1, 1)].post(test_path, json={})

        assert dict(resp_2000.cookies) == {"cookie_key": "cookie_val"}

        assert dict(resp_2000.headers) == {
            "content-length": "2",
            "content-type": "application/json",
            "set-cookie": IsStr(),
            "x-api-version": "2000-01-01",
        }
        assert dict(http.cookies.SimpleCookie(resp_2000.headers["set-cookie"])["cookie_key"]) == {
            "expires": IsStr(),
            "path": "/",
            "comment": "",
            "domain": "",
            "max-age": "0",
            "secure": "",
            "httponly": "",
            "version": "",
            "samesite": "lax",
        }
        assert dict(resp_2001.cookies) == {"cookie_key": "cookie_val"}
        assert dict(resp_2001.headers) == {
            "content-length": "2",
            "content-type": "application/json",
            "set-cookie": "cookie_key=cookie_val; Path=/; SameSite=lax",
            "x-api-version": "2001-01-01",
        }


def test__invalid_path_migration_syntax():
    with pytest.raises(
        TypeError,
        match=re.escape("If path was provided as a first argument, methods must be provided as a second argument"),
    ):
        convert_request_to_next_version_for("/test")  # pyright: ignore[reportArgumentType]


def test__schema_migration_syntax__with_methods_after_a_schema__should_raise_error():
    with pytest.raises(
        TypeError,
        match=re.escape("If schema was provided as a first argument, all other arguments must also be schemas"),
    ):
        convert_request_to_next_version_for(  # pyright: ignore[reportCallIssue]
            AnyRequestSchema,
            ["POST"],  # pyright: ignore[reportArgumentType]
        )


def test__schema_migration_syntax__with_additional_schemas_after_methods__should_raise_error():
    with pytest.raises(
        TypeError,
        match=re.escape("If path was provided as a first argument, then additional schemas cannot be added"),
    ):
        convert_request_to_next_version_for("/v1/test", ["POST"], AnyRequestSchema)  # pyright: ignore[reportArgumentType]


def test__uploadfile_can_work(
    create_versioned_clients: CreateVersionedClients,
    test_path: Literal["/test"],
    router: VersionedAPIRouter,
):
    @router.post(test_path, response_model=AnyResponseSchema)
    async def endpoint(file: UploadFile = File(...)):
        # PydanticV2 can no longer serialize files directly like it could in v1
        file_dict = {k: v for k, v in file.__dict__.items() if not k.startswith("_") and k != "file"}
        file_dict["headers"] = dict(file_dict["headers"])
        return file_dict

    clients = create_versioned_clients(version_change())
    resp_2000 = clients[date(2000, 1, 1)].post(test_path, files={"file": b"Hewwo"})
    resp_2001 = clients[date(2001, 1, 1)].post(test_path, files={"file": b"Hewwo"})

    assert resp_2000.json() == {
        "filename": "upload",
        "size": 5,
        "headers": {
            "content-disposition": 'form-data; name="file"; filename="upload"',
            "content-type": "application/octet-stream",
        },
    }
    assert resp_2001.json() == {
        "filename": "upload",
        "size": 5,
        "headers": {
            "content-disposition": 'form-data; name="file"; filename="upload"',
            "content-type": "application/octet-stream",
        },
    }


def test__request_and_response_migrations__for_paths_with_variables__can_match(
    create_versioned_clients: CreateVersionedClients,
    router: VersionedAPIRouter,
):
    @router.post("/test/{id}")
    async def endpoint(id: int, my_query: str = fastapi.Query(default="wow")):
        return [id, my_query]

    @convert_request_to_next_version_for("/test/{id}", ["POST"])
    def request_converter(request: RequestInfo):
        request.query_params["my_query"] = "Hewwo"

    @convert_response_to_previous_version_for("/test/{id}", ["POST"])
    def response_converter(response: ResponseInfo):
        response.body.append("World")

    clients = create_versioned_clients(version_change(req=request_converter, resp=response_converter))
    assert clients[date(2000, 1, 1)].post("/test/83").json() == [83, "Hewwo", "World"]
    assert clients[date(2001, 1, 1)].post("/test/83").json() == [83, "wow"]


def test__request_and_response_migrations__for_endpoint_with_http_exception__can_migrate_to_200(
    create_versioned_clients: CreateVersionedClients,
    router: VersionedAPIRouter,
):
    @router.post("/test")
    async def endpoint():
        raise HTTPException(status_code=404)

    @convert_response_to_previous_version_for("/test", ["POST"], migrate_http_errors=True)
    def response_converter(response: ResponseInfo):
        response.status_code = 200
        response.body = {"hello": "darkness"}
        response.headers["hewwo"] = "dawkness"

    clients = create_versioned_clients(version_change(resp=response_converter))
    resp_2000 = clients[date(2000, 1, 1)].post("/test")
    assert resp_2000.status_code == 200
    assert resp_2000.json() == {"hello": "darkness"}
    assert resp_2000.headers["hewwo"] == "dawkness"

    resp_2001 = clients[date(2001, 1, 1)].post("/test")
    assert resp_2001.status_code == 404
    assert resp_2001.json() == {"detail": "Not Found"}
    assert "hewwo" not in resp_2001.headers


def test__request_and_response_migrations__for_endpoint_with_http_exception_and_no_error_migrations__wont_migrate(
    create_versioned_clients: CreateVersionedClients,
    router: VersionedAPIRouter,
):
    @router.post("/test")
    async def endpoint():
        raise HTTPException(status_code=400)

    @convert_response_to_previous_version_for("/test", ["POST"])
    def response_converter(response: ResponseInfo):
        raise NotImplementedError("This should not be called")

    clients = create_versioned_clients(version_change(resp=response_converter))
    resp_2000 = clients[date(2000, 1, 1)].post("/test")
    assert resp_2000.status_code == 400

    resp_2001 = clients[date(2001, 1, 1)].post("/test")
    assert resp_2001.status_code == 400


def test__request_and_response_migrations__for_endpoint_with_http_exception__can_migrate_to_another_error(
    create_versioned_clients: CreateVersionedClients,
    router: VersionedAPIRouter,
):
    @router.post("/test")
    async def endpoint():
        raise HTTPException(status_code=404)

    @convert_response_to_previous_version_for("/test", ["POST"], migrate_http_errors=True)
    def response_converter(response: ResponseInfo):
        response.status_code = 401
        response.body = None

    clients = create_versioned_clients(version_change(resp=response_converter))
    resp_2000 = clients[date(2000, 1, 1)].post("/test")
    assert resp_2000.status_code == 401
    assert resp_2000.json() == {"detail": "Unauthorized"}

    resp_2001 = clients[date(2001, 1, 1)].post("/test")
    assert resp_2001.status_code == 404
    assert resp_2001.json() == {"detail": "Not Found"}


def test__request_and_response_migrations__for_endpoint_with_no_default_status_code__response_should_contain_default(
    create_versioned_clients: CreateVersionedClients,
    router: VersionedAPIRouter,
):
    @router.post("/test")
    async def endpoint():
        return 83

    @convert_response_to_previous_version_for("/test", ["POST"])
    def response_converter(response: ResponseInfo):
        assert response.status_code == 200

    clients = create_versioned_clients(version_change(resp=response_converter))

    resp_2000 = clients[date(2000, 1, 1)].post("/test")
    assert resp_2000.status_code == 200
    assert resp_2000.json() == 83

    resp_2001 = clients[date(2001, 1, 1)].post("/test")
    assert resp_2001.status_code == 200
    assert resp_2001.json() == 83


def test__request_and_response_migrations__for_endpoint_with_custom_status_code__response_should_contain_default(
    create_versioned_clients: CreateVersionedClients,
    router: VersionedAPIRouter,
):
    @router.post("/test", status_code=201)
    async def endpoint():
        return 83

    @convert_response_to_previous_version_for("/test", ["POST"])
    def response_converter(response: ResponseInfo):
        assert response.status_code == 201

    clients = create_versioned_clients(version_change(resp=response_converter))

    resp_2000 = clients[date(2000, 1, 1)].post("/test")
    assert resp_2000.status_code == 201
    assert resp_2000.json() == 83

    resp_2001 = clients[date(2001, 1, 1)].post("/test")
    assert resp_2001.status_code == 201
    assert resp_2001.json() == 83


def test__request_and_response_migrations__for_endpoint_with_modified_status_code__response_should_not_change(
    create_versioned_clients: CreateVersionedClients,
    router: VersionedAPIRouter,
):
    @router.post("/test")
    async def endpoint(response: Response):
        response.status_code = 201
        return 83

    @convert_response_to_previous_version_for("/test", ["POST"])
    def response_converter(response: ResponseInfo):
        assert response.status_code == 201

    clients = create_versioned_clients(version_change(resp=response_converter))

    resp_2000 = clients[date(2000, 1, 1)].post("/test")
    assert resp_2000.status_code == 201
    assert resp_2000.json() == 83

    resp_2001 = clients[date(2001, 1, 1)].post("/test")
    assert resp_2001.status_code == 201
    assert resp_2001.json() == 83


def test__response_migrations__with_manual_string_json_response_and_migration(
    create_versioned_clients: CreateVersionedClients,
    router: VersionedAPIRouter,
):
    @router.post("/test")
    async def endpoint():
        return JSONResponse(content="My content")

    @convert_response_to_previous_version_for("/test", ["POST"])
    def response_converter(response: ResponseInfo):
        pass

    clients = create_versioned_clients(version_change(resp=response_converter))

    resp_2000 = clients[date(2000, 1, 1)].post("/test")
    assert resp_2000.status_code == 200
    assert resp_2000.json() == "My content"

    resp_2001 = clients[date(2001, 1, 1)].post("/test")
    assert resp_2001.status_code == 200
    assert resp_2001.json() == "My content"


@pytest.mark.parametrize(("path", "method"), [("/NOT_test", "POST"), ("/test", "PUT")])
def test__request_by_path_migration__for_nonexistent_endpoint_path__should_raise_error(
    create_versioned_clients: CreateVersionedClients,
    router: VersionedAPIRouter,
    path: str,
    method: str,
):
    @router.post("/test")
    async def endpoint():
        raise NotImplementedError

    @convert_request_to_next_version_for(path, [method])
    def request_converter(request: RequestInfo):
        raise NotImplementedError

    with pytest.raises(RouteByPathConverterDoesNotApplyToAnythingError):
        create_versioned_clients(version_change(converter=request_converter))


@pytest.mark.parametrize(("path", "method"), [("/NOT_test", "POST"), ("/test", "PUT")])
def test__response_by_path_migration__for_nonexistent_endpoint_path__should_raise_error(
    create_versioned_clients: CreateVersionedClients,
    router: VersionedAPIRouter,
    path: str,
    method: str,
):
    @router.post("/test")
    async def endpoint():
        raise NotImplementedError

    @convert_response_to_previous_version_for(path, [method])
    def response_converter(response: ResponseInfo):
        raise NotImplementedError

    with pytest.raises(RouteByPathConverterDoesNotApplyToAnythingError):
        create_versioned_clients(version_change(converter=response_converter))


def test__request_by_schema_migration__for_nonexistent_schema__should_raise_error(
    create_versioned_clients: CreateVersionedClients,
    router: VersionedAPIRouter,
):
    @router.post("/test", response_model=AnyResponseSchema)
    async def endpoint(body: AnyRequestSchema):
        raise NotImplementedError

    # Using response model for requests to cause an error
    @convert_request_to_next_version_for(AnyResponseSchema)
    def request_converter(request: RequestInfo):
        raise NotImplementedError

    with pytest.raises(RouteRequestBySchemaConverterDoesNotApplyToAnythingError):
        create_versioned_clients(version_change(converter=request_converter))


def test__response_by_schema_migration__for_nonexistent_schema__should_raise_error(
    create_versioned_clients: CreateVersionedClients,
    router: VersionedAPIRouter,
):
    @router.post("/test", response_model=AnyResponseSchema)
    async def endpoint(body: AnyRequestSchema):
        raise NotImplementedError

    # Using request model for responses to cause an error
    @convert_response_to_previous_version_for(AnyRequestSchema)
    def response_converter(response: ResponseInfo):
        raise NotImplementedError

    with pytest.raises(RouteResponseBySchemaConverterDoesNotApplyToAnythingError):
        create_versioned_clients(version_change(converter=response_converter))


def test__manual_response_migrations():
    @convert_response_to_previous_version_for(EmptySchema)
    def response_converter(response: ResponseInfo):
        response.body["amount"] = 83

    version_bundle = VersionBundle(
        Version(
            date(2001, 1, 1),
            version_change(
                schema(EmptySchema).field("name").existed_as(type=str, info=Field(default="Apples")),
                schema(EmptySchema).field("amount").existed_as(type=int),
                convert=response_converter,
            ),
        ),
        Version(date(2000, 1, 1)),
    )

    new_response = migrate_response_body(
        version_bundle, EmptySchema, latest_body={"id": "hewwo"}, version=date(2000, 1, 1)
    )
    assert new_response.model_dump() == {
        "name": "Apples",
        "amount": 83,
    }
    assert new_response.model_dump(exclude_unset=True) == {"amount": 83}

    with pytest.raises(CadwynError):
        new_response = migrate_response_body(
            version_bundle, EmptySchema, latest_body={"id": "hewwo"}, version="1999-01-01"
        )


def test__request_and_response_migrations__with_multiple_schemas_in_converters(
    create_versioned_clients: CreateVersionedClients,
    router: VersionedAPIRouter,
) -> None:
    class Request1(BaseModel):
        i: list[str]

    class Response1(BaseModel):
        i: list[str]

    class Request2(BaseModel):
        i: list[str]

    class Response2(BaseModel):
        i: list[str]

    class Request3(BaseModel):
        i: list[str]

    class Response3(BaseModel):
        i: list[str]

    @router.post("/test_1", response_model=Response1)
    async def endpoint_1(body: Request1):
        body.i.append("test_1")
        return body

    @router.post("/test_2", response_model=Response2)
    async def endpoint_2(body: Request2):
        body.i.append("test_2")
        return body

    @router.post("/test_3", response_model=Response3)
    async def endpoint_3(body: Request3):
        body.i.append("test_3")
        return body

    @convert_request_to_next_version_for(Request1, Request2, Request3)
    def request_converter(request: RequestInfo):
        request.body["i"].append("request_migration")

    @convert_response_to_previous_version_for(Response1, Response2, Response3)
    def response_converter(response: ResponseInfo):
        response.body["i"].append("response_migration")

    clients = create_versioned_clients(version_change(req=request_converter, resp=response_converter))
    client_2000, client_2001 = clients.values()

    for endpoint in ("test_1", "test_2", "test_3"):
        resp_2000 = client_2000.post(f"/{endpoint}", json={"i": ["original_request"]})
        assert resp_2000.status_code == 200
        assert resp_2000.json() == {"i": ["original_request", "request_migration", endpoint, "response_migration"]}

        resp_2001 = client_2001.post(f"/{endpoint}", json={"i": ["original_request"]})
        assert resp_2001.status_code == 200
        assert resp_2001.json() == {"i": ["original_request", endpoint]}
