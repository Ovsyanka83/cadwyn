from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import date

import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cadwyn import Cadwyn
from cadwyn.structure.versions import Version, VersionBundle
from tests._resources.utils import BASIC_HEADERS
from tests._resources.versioned_app.v2021_01_01 import router as v2021_01_01_router
from tests._resources.versioned_app.v2022_01_02 import router as v2022_01_02_router
from tests._resources.versioned_app.webhooks import router as webhooks_router


# TODO: Add better tests for covering lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield  # pragma: no cover


versioned_app = Cadwyn(versions=VersionBundle(Version(date(2022, 11, 16))), lifespan=lifespan)
versioned_app.add_header_versioned_routers(v2021_01_01_router, header_value="2021-01-01")
versioned_app.add_header_versioned_routers(v2022_01_02_router, header_value="2022-02-02")
versioned_app.include_router(webhooks_router)

versioned_app_with_custom_api_version_var = Cadwyn(
    versions=VersionBundle(Version(date(2022, 11, 16))), lifespan=lifespan, api_version_var=ContextVar("My api version")
)
versioned_app_with_custom_api_version_var.add_header_versioned_routers(v2021_01_01_router, header_value="2021-01-01")
versioned_app_with_custom_api_version_var.add_header_versioned_routers(v2022_01_02_router, header_value="2022-02-02")
versioned_app_with_custom_api_version_var.include_router(webhooks_router)

# TODO: We should not have any clients that are run like this. Instead, all of them must run using "with"
client = TestClient(versioned_app, raise_server_exceptions=False, headers=BASIC_HEADERS)
client_without_headers = TestClient(versioned_app)
client_without_headers_and_with_custom_api_version_var = TestClient(versioned_app_with_custom_api_version_var)

if __name__ == "__main__":
    uvicorn.run(versioned_app)
