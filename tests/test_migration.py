from contextvars import ContextVar
from datetime import date
from typing import Any

import pytest
from pydantic import BaseModel

from universi.structure import (
    Version,
    VersionBundle,
    VersionChange,
    convert_response_to_previous_version_for,
)


class DummySchema(BaseModel):
    name: str
    vat_ids: list[dict[str, str]]


class DummySchema2(BaseModel):
    name: str


@pytest.fixture()
def version_change_1():
    class VersionChange1(VersionChange):
        description = "Change vat id to list"
        instructions_to_migrate_to_previous_version = ()

        @convert_response_to_previous_version_for(DummySchema)
        def change_vat_ids_to_list(cls, data: dict[str, Any]) -> None:
            data["vat_ids"] = [id["value"] for id in data.pop("_prefetched_vat_ids")]

    return VersionChange1


@pytest.fixture()
def version_change_2():
    class VersionChange2(VersionChange):
        description = "Change vat ids to str"
        instructions_to_migrate_to_previous_version = ()

        @convert_response_to_previous_version_for(DummySchema)
        def change_vat_ids_to_single_item(cls, data: dict[str, Any]) -> None:
            data["vat_id"] = data.pop("vat_ids")[0]

    return VersionChange2


def test__migrate__with_no_migrations__should_not_raise_error(api_version_var: ContextVar[date | None]):
    assert VersionBundle(api_version_var=api_version_var).migrate_response(
        DummySchema,
        {"A": "B"},
        date(2000, 1, 1),
    ) == {"A": "B"}


def test__migrate_simple_data_one_version_down(
    version_change_1: type[VersionChange],
    api_version_var: ContextVar[date | None],
):
    versions = VersionBundle(
        Version(date(2002, 1, 1), version_change_1),
        Version(date(2001, 1, 1)),
        api_version_var=api_version_var,
    )
    assert versions.migrate_response(
        DummySchema,
        {
            "name": "HeliCorp",
            "_prefetched_vat_ids": [{"value": "Foo"}, {"value": "Bar"}],
        },
        date(2001, 1, 1),
    ) == {"name": "HeliCorp", "vat_ids": ["Foo", "Bar"]}


def test__migrate_simple_data_one_version_down__with_some_inapplicable_migrations__result_is_unaffected(
    version_change_1: type[VersionChange],
    api_version_var: ContextVar[date | None],
):
    class VersionChange3(VersionChange):
        description = "Change vat ids to str"
        instructions_to_migrate_to_previous_version = ()

        @convert_response_to_previous_version_for(DummySchema2)
        def break_vat_ids(cls, data: dict[str, Any]) -> None:
            raise NotImplementedError

    versions = VersionBundle(
        Version(date(2002, 1, 1), version_change_1, VersionChange3),
        Version(date(2001, 1, 1)),
        api_version_var=api_version_var,
    )
    assert versions.migrate_response(
        DummySchema,
        {
            "name": "HeliCorp",
            "_prefetched_vat_ids": [{"value": "Foo"}, {"value": "Bar"}],
        },
        date(2001, 1, 1),
    ) == {"name": "HeliCorp", "vat_ids": ["Foo", "Bar"]}


def test__migrate_simple_data_two_versions_down(
    version_change_1: type[VersionChange],
    version_change_2: type[VersionChange],
    api_version_var: ContextVar[date | None],
):
    versions = VersionBundle(
        Version(date(2002, 1, 1), version_change_1),
        Version(date(2001, 1, 1), version_change_2),
        Version(date(2000, 1, 1)),
        api_version_var=api_version_var,
    )
    assert versions.migrate_response(
        DummySchema,
        {
            "name": "HeliCorp",
            "_prefetched_vat_ids": [{"value": "Foo"}, {"value": "Bar"}],
        },
        date(2000, 1, 1),
    ) == {"name": "HeliCorp", "vat_id": "Foo"}


@pytest.mark.parametrize("api_version", [None, date(2001, 1, 1)])
async def test__versioned_decorator__with_latest_version__response_is_unchanged(
    api_version: date | None,
    version_change_2: type[VersionChange],
    api_version_var: ContextVar[date | None],
):
    versions = VersionBundle(
        Version(date(2001, 1, 1), version_change_2),
        Version(date(2000, 1, 1)),
        api_version_var=api_version_var,
    )

    @versions.migrate_responses_backward(DummySchema)
    async def test():
        return {"name": "HeliCorp", "vat_ids": ["Foo", "Bar"]}

    api_version_var.set(api_version)
    assert await test() == {"name": "HeliCorp", "vat_ids": ["Foo", "Bar"]}


async def test__versioned_decorator__with_earlier_version__response_is_migrated(
    version_change_2: type[VersionChange],
    api_version_var: ContextVar[date | None],
):
    versions = VersionBundle(
        Version(date(2001, 1, 1), version_change_2),
        Version(date(2000, 1, 1)),
        api_version_var=api_version_var,
    )

    @versions.migrate_responses_backward(DummySchema)
    async def test():
        return {"name": "HeliCorp", "vat_ids": ["Foo", "Bar"]}

    api_version_var.set(date(2000, 1, 1))
    assert await test() == {"name": "HeliCorp", "vat_id": "Foo"}
