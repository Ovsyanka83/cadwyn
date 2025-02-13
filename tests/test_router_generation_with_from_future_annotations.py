from __future__ import annotations

from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from cadwyn.applications import Cadwyn
from cadwyn.route_generation import VersionedAPIRouter
from cadwyn.structure.schemas import schema
from cadwyn.structure.versions import Version, VersionBundle, VersionChange


class OuterSchema(BaseModel):
    bar: MySchema


class MySchema(BaseModel):
    foo: str = Field(coerce_numbers_to_str=True)


class MyVersionChange(VersionChange):
    description = "Hello"
    instructions_to_migrate_to_previous_version = (
        schema(MySchema).field("foo").had(type=int),
        schema(MySchema).field("foo").didnt_have("coerce_numbers_to_str"),
    )


app = Cadwyn(versions=VersionBundle(Version("2001-01-01", MyVersionChange), Version("2000-01-01")))
router = VersionedAPIRouter()


@router.post("/test")
async def test_with_inner_schema_forwardref(dep: MySchema) -> MySchema:
    return dep


@router.post("/test2")
async def test_with_outer_schema_forwardref(dep: OuterSchema) -> OuterSchema:
    return dep


app.generate_and_include_versioned_routers(router)


def test__router_generation__using_forwardref_inner_global_schema_in_body():
    unversioned_client = TestClient(app)
    client_2000 = TestClient(app, headers={app.router.api_version_header_name: "2000-01-01"})
    client_2001 = TestClient(app, headers={app.router.api_version_header_name: "2001-01-01"})
    assert client_2000.post("/test", json={"foo": 1}).json() == {"foo": 1}
    assert client_2001.post("/test", json={"foo": 1}).json() == {"foo": "1"}
    assert unversioned_client.get("/openapi.json?version=2000-01-01").status_code == 200
    assert unversioned_client.get("/openapi.json?version=2001-01-01").status_code == 200


def test__router_generation__using_forwardref_outer_global_schema_in_body():
    unversioned_client = TestClient(app)
    client_2000 = TestClient(app, headers={app.router.api_version_header_name: "2000-01-01"})
    client_2001 = TestClient(app, headers={app.router.api_version_header_name: "2001-01-01"})
    assert client_2000.post("/test", json={"bar": {"foo": 1}}).json() == {"bar": {"foo": 1}}
    assert client_2001.post("/test", json={"bar": {"foo": 1}}).json() == {"bar": {"foo": "1"}}
    assert unversioned_client.get("/openapi.json?version=2000-01-01").status_code == 200
    assert unversioned_client.get("/openapi.json?version=2001-01-01").status_code == 200
