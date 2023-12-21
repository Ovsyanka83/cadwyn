import re
from contextvars import ContextVar
from datetime import date
from typing import Any

import pytest
from pydantic import BaseModel

from cadwyn._compat import PYDANTIC_V2
from cadwyn.exceptions import CadwynError, CadwynStructureError, LintingError
from cadwyn.structure import (
    Version,
    VersionBundle,
    VersionChange,
    VersionChangeWithSideEffects,
    convert_request_to_next_version_for,
    convert_response_to_previous_version_for,
    endpoint,
    schema,
)


class DummySubClass2000_001(VersionChangeWithSideEffects):  # noqa: N801
    description = "dummy description"
    instructions_to_migrate_to_previous_version = []


class DummySubClass2000_002(VersionChangeWithSideEffects):  # noqa: N801
    description = "dummy description2"
    instructions_to_migrate_to_previous_version = []


class DummySubClass2001(VersionChangeWithSideEffects):
    description = "dummy description3"
    instructions_to_migrate_to_previous_version = []


class DummySubClass2002(VersionChangeWithSideEffects):
    description = "dummy description4"
    instructions_to_migrate_to_previous_version = []


@pytest.fixture()
def dummy_sub_class_without_version():
    class DummySubClassWithoutVersion(VersionChangeWithSideEffects):
        description = "dummy description4"
        instructions_to_migrate_to_previous_version = []

    return DummySubClassWithoutVersion


@pytest.fixture()
def versions(api_version_var: ContextVar[date | None]):
    try:
        yield VersionBundle(
            Version(date(2002, 1, 1), DummySubClass2002),
            Version(date(2001, 1, 1), DummySubClass2001),
            Version(date(2000, 1, 1), DummySubClass2000_001, DummySubClass2000_002),
            Version(date(1999, 1, 1)),
            api_version_var=api_version_var,
        )
    finally:
        DummySubClass2002._bound_version_bundle = None
        DummySubClass2001._bound_version_bundle = None
        DummySubClass2000_001._bound_version_bundle = None
        DummySubClass2000_002._bound_version_bundle = None


class TestVersionChange:
    def test__description__not_set__should_raise_error(self):
        with pytest.raises(
            CadwynStructureError,
            match=re.escape(
                "Version change description is not set on 'DummySubClass' but is required.",
            ),
        ):

            class DummySubClass(VersionChange):
                instructions_to_migrate_to_previous_version = []

    def test__instructions_to_migrate_to_previous_version__not_set__should_raise_error(self):
        with pytest.raises(
            CadwynStructureError,
            match=re.escape(
                "Attribute 'instructions_to_migrate_to_previous_version' "
                "is not set on 'DummySubClass' but is required.",
            ),
        ):

            class DummySubClass(VersionChange):
                description = "dummy description"

    def test__instructions_to_migrate_to_previous_version__not_a_sequence__should_raise_error(self):
        with pytest.raises(
            CadwynStructureError,
            match=re.escape(
                "Attribute 'instructions_to_migrate_to_previous_version' must be a sequence in 'DummySubClass'.",
            ),
        ):

            class DummySubClass(VersionChange):
                description = "dummy description"
                instructions_to_migrate_to_previous_version = True  # pyright: ignore[reportGeneralTypeIssues]

    def test__instructions_to_migrate_to_previous_version__non_instruction_specified_in_list__should_raise_error(self):
        with pytest.raises(
            CadwynStructureError,
            match=re.escape(
                "Instruction 'True' is not allowed. Please, use the correct instruction types",
            ),
        ):

            class DummySubClass(VersionChange):
                description = "dummy description"
                instructions_to_migrate_to_previous_version = [True]  # pyright: ignore[reportGeneralTypeIssues]

    def test__non_instruction_attribute_set__should_raise_error(self):
        with pytest.raises(
            CadwynStructureError,
            match=re.escape(
                "Found: 'dummy_attribute' attribute of type '<class 'str'>' in 'DummySubClass'. "
                "Only migration instructions and schema properties are allowed in version change class body.",
            ),
        ):

            class DummySubClass(VersionChange):
                description = "dummy description"
                instructions_to_migrate_to_previous_version = []
                dummy_attribute = "dummy attribute"

    @pytest.mark.parametrize(
        "version_change_type",
        [VersionChange, VersionChangeWithSideEffects],
    )
    def test__init_subclass__incorrect_subclass_hierarchy__should_raise_error(
        self,
        version_change_type: type[VersionChange],
    ):
        class DummySubClass(version_change_type):
            description = "dummy description"
            instructions_to_migrate_to_previous_version = []

        with pytest.raises(
            TypeError,
            match=re.escape(
                "Can't subclass DummySubSubClass as it was never meant to be subclassed.",
            ),
        ):

            class DummySubSubClass(DummySubClass):
                pass

    def test__init__instantiation_attempt__should_raise_error(self):
        with pytest.raises(
            TypeError,
            match=re.escape(
                "Can't instantiate DummySubClass as it was never meant to be instantiated.",
            ),
        ):

            class DummySubClass(VersionChange):
                description = "dummy description"
                instructions_to_migrate_to_previous_version = []

            DummySubClass()  # this will raise a TypeError


class TestVersionChangeWithSideEffects:
    def test__is_applied__api_version_var_is_none__everything_is_applied(
        self,
        versions: VersionBundle,
        api_version_var: ContextVar[date | None],
    ):
        api_version_var.set(None)
        assert DummySubClass2002.is_applied is True
        assert DummySubClass2001.is_applied is True
        assert DummySubClass2000_001.is_applied is True
        assert DummySubClass2000_002.is_applied is True

    def test__is_applied__api_version_var_is_later_than_latest__everything_is_applied(
        self,
        versions: VersionBundle,
        api_version_var: ContextVar[date | None],
    ):
        api_version_var.set(date(2003, 1, 1))
        assert DummySubClass2002.is_applied is True
        assert DummySubClass2001.is_applied is True
        assert DummySubClass2000_001.is_applied is True
        assert DummySubClass2000_002.is_applied is True

    def test__is_applied__api_version_var_is_before_latest__latest_is_inactive(
        self,
        versions: VersionBundle,
        api_version_var: ContextVar[date | None],
    ):
        api_version_var.set(date(2001, 1, 1))
        assert DummySubClass2002.is_applied is False
        assert DummySubClass2001.is_applied is True
        assert DummySubClass2000_001.is_applied is True
        assert DummySubClass2000_002.is_applied is True

    def test__is_applied__api_version_var_is_at_earliest__everything_is_inactive(
        self,
        versions: VersionBundle,
        api_version_var: ContextVar[date | None],
    ):
        api_version_var.set(date(1999, 3, 1))
        assert DummySubClass2002.is_applied is False
        assert DummySubClass2001.is_applied is False
        assert DummySubClass2000_001.is_applied is False
        assert DummySubClass2000_002.is_applied is False

    def test__is_applied__api_version_var_set_and_version_change_class_not_in_versions__should_raise_error(
        self,
        dummy_sub_class_without_version: type[VersionChangeWithSideEffects],
        api_version_var: ContextVar[date | None],
    ):
        api_version_var.set(date(1999, 3, 1))
        with pytest.raises(
            CadwynError,
            match=re.escape(
                "You tried to check whether 'DummySubClassWithoutVersion' "
                "is active but it was never bound to any version.",
            ),
        ):
            assert dummy_sub_class_without_version.is_applied

    def test__is_applied__api_version_var_unset_and_version_change_class_not_in_versions__should_raise_error(
        self,
        dummy_sub_class_without_version: type[VersionChangeWithSideEffects],
    ):
        with pytest.raises(
            CadwynError,
            match=re.escape(
                "You tried to check whether 'DummySubClassWithoutVersion' "
                "is active but it was never bound to any version.",
            ),
        ):
            assert dummy_sub_class_without_version.is_applied


class TestVersionBundle:
    def test__init__incorrectly_sorted_versions(self, api_version_var: ContextVar[date | None]):
        with pytest.raises(
            CadwynStructureError,
            match=re.escape(
                "Versions are not sorted correctly. Please sort them in descending order.",
            ),
        ):
            VersionBundle(
                Version(date(2000, 1, 1)),
                Version(date(2001, 1, 1)),
                api_version_var=api_version_var,
            )

    def test__init__one_version_change_attached_to_two_version_bundles__should_raise_error(
        self,
        dummy_sub_class_without_version: type[VersionChangeWithSideEffects],
        api_version_var: ContextVar[date | None],
    ):
        VersionBundle(
            Version(date(2001, 1, 1), dummy_sub_class_without_version),
            Version(date(2000, 1, 1)),
            api_version_var=api_version_var,
        )
        with pytest.raises(
            CadwynStructureError,
            match=re.escape(
                "You tried to bind version change 'DummySubClassWithoutVersion' to two different versions."
                " It is prohibited.",
            ),
        ):
            VersionBundle(
                Version(date(2001, 1, 1), dummy_sub_class_without_version),
                Version(date(2000, 1, 1)),
                api_version_var=api_version_var,
            )

    def test__init__one_version_change_attached_to_two_versions__should_raise_error(
        self,
        dummy_sub_class_without_version: type[VersionChangeWithSideEffects],
        api_version_var: ContextVar[date | None],
    ):
        with pytest.raises(
            CadwynStructureError,
            match=re.escape(
                "You tried to bind version change 'DummySubClassWithoutVersion' to two different versions. "
                "It is prohibited.",
            ),
        ):
            VersionBundle(
                Version(date(2002, 1, 1), dummy_sub_class_without_version),
                Version(date(2001, 1, 1), dummy_sub_class_without_version),
                Version(date(2000, 1, 1)),
                api_version_var=api_version_var,
            )

    def test__init__two_versions_with_the_same_value__should_raise_error(
        self,
        api_version_var: ContextVar[date | None],
    ):
        with pytest.raises(
            CadwynStructureError,
            match=re.escape(
                "You tried to define two versions with the same value in the same VersionBundle: '2000-01-01'.",
            ),
        ):
            VersionBundle(
                Version(date(2000, 1, 1)),
                Version(date(2000, 1, 1)),
                api_version_var=api_version_var,
            )

    def test__init__version_change_in_the_first_version__should_raise_error(
        self,
        dummy_sub_class_without_version: type[VersionChangeWithSideEffects],
        api_version_var: ContextVar[date | None],
    ):
        with pytest.raises(
            CadwynStructureError,
            match=re.escape(
                'The first version "2000-01-01" cannot have any version changes. '
                "Version changes are defined to migrate to/from a previous version "
                "so you cannot define one for the very first version.",
            ),
        ):
            VersionBundle(
                Version(date(2000, 1, 1), dummy_sub_class_without_version),
                api_version_var=api_version_var,
            )


class SomeSchema(BaseModel):
    pass


def test__convert_response_to_previous_version_for__with_incorrect_args__should_raise_error():
    with pytest.raises(
        ValueError,
        match=re.escape(
            "Method 'my_conversion_method' must have 2 parameters: cls and response",
        ),
    ):

        @convert_response_to_previous_version_for(SomeSchema)
        def my_conversion_method(cls: Any, payload: Any):
            raise NotImplementedError


def test__convert_response_to_previous_version_for__with_no_args__should_raise_error():
    with pytest.raises(
        ValueError,
        match=re.escape(
            "Method 'my_conversion_method2' must have 2 parameters: cls and response",
        ),
    ):

        @convert_response_to_previous_version_for(SomeSchema)
        def my_conversion_method2():
            raise NotImplementedError


def test__convert_request_to_next_version_for__with_incorrect_args__should_raise_error():
    with pytest.raises(
        ValueError,
        match=re.escape(
            "Method 'my_conversion_method' must have 2 parameters: cls and request",
        ),
    ):

        @convert_request_to_next_version_for(SomeSchema)
        def my_conversion_method(cls: Any, payload: Any):
            raise NotImplementedError


def test__convert_request_to_next_version_for__with_no_args__should_raise_error():
    with pytest.raises(
        ValueError,
        match=re.escape(
            "Method 'my_conversion_method2' must have 2 parameters: cls and request",
        ),
    ):

        @convert_request_to_next_version_for(SomeSchema)
        def my_conversion_method2():
            raise NotImplementedError


@pytest.mark.parametrize(
    ("attr_name", "attr_value"),
    [
        ("regex", r"hewwo"),
        ("include", ["a", "b"]),
        ("min_items", 3),
        ("max_items", 4),
        ("unique_items", True),
    ],
)
def test__schema_field_had_pydantic_1_field_in_pydantic_2__should_raise_error(attr_name: str, attr_value: Any):
    if not PYDANTIC_V2:
        return
    with pytest.raises(CadwynStructureError, match=f"`{attr_name}` was removed in Pydantic 2. Use `"):
        schema(SomeSchema).field("foo").had(**{attr_name: attr_value})


def test__schema_field_had_pydantic_2_field_in_pydantic_1__should_raise_error():
    if PYDANTIC_V2:
        return
    with pytest.raises(CadwynStructureError, match="`pattern` is only available in Pydantic 2. use `regex` instead"):
        schema(SomeSchema).field("foo").had(pattern=r"rawr")


def test__endpoint_instruction_factory_interface__with_wrong_http_methods__should_raise_error():
    with pytest.raises(
        LintingError,
        match=re.escape(
            "The following HTTP methods are not valid: DEATH, STRAND. "
            "Please use valid HTTP methods such as GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD.",
        ),
    ):
        endpoint("/test", ["DEATH", "STRAND"])
