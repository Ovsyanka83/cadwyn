import inspect
import json
import re
import sys
import time
from contextvars import ContextVar
from datetime import date
from enum import Enum, auto
from pathlib import Path
from typing import Any, Union

import pytest
from pydantic import BaseModel, Field

from tests._data import latest
from tests._data.latest import some_schema, weird_schemas
from tests._data.unversioned_schema_dir import UnversionedSchema2
from tests._data.unversioned_schemas import UnversionedSchema3
from tests.conftest import GenerateTestVersionPackages
from universi import regenerate_dir_to_all_versions
from universi.exceptions import (
    CodeGenerationError,
    InvalidGenerationInstructionError,
    UniversiStructureError,
)
from universi.structure import (
    Version,
    VersionBundle,
    VersionChange,
    enum,
    schema,
)


@pytest.fixture(autouse=True)
def _autouse_reload_autogenerated_modules(_reload_autogenerated_modules: None):
    ...


def serialize(enum: type[Enum]) -> dict[str, Any]:
    return {member.name: member.value for member in enum}


def assert_field_had_changes_apply(
    model: type[BaseModel],
    attr: str,
    attr_value: Any,
    generate_test_version_packages: GenerateTestVersionPackages,
):
    v2000_01_01, v2001_01_01 = generate_test_version_packages(
        schema(getattr(latest, model.__name__)).field("foo").had(**{attr: attr_value}),
    )
    # For some reason it said that auto and Field were not defined, even though I was importing them
    d1 = {"auto": auto, "Field": Field}
    d2 = {"auto": auto, "Field": Field}
    # Otherwise, when re-importing and rewriting the same files many times, at some point python just starts
    # putting the module into a hardcore cache that cannot be updated by removing entry from sys.modules or
    # using importlib.reload -- only by waiting around 1.5 seconds in-between tests.
    exec(inspect.getsource(v2000_01_01), d1, d1)
    exec(inspect.getsource(v2001_01_01), d2, d2)
    assert getattr(d1[model.__name__].__fields__["foo"].field_info, attr) == attr_value
    assert getattr(d2[model.__name__].__fields__["foo"].field_info, attr) == getattr(
        getattr(latest, model.__name__).__fields__["foo"].field_info,
        attr,
    )


def test__latest_enums_are_unchanged():
    """If it is changed -- all tests will break

    So I suggest checking this test first :)
    """

    assert serialize(latest.EmptyEnum) == {}

    assert serialize(latest.EnumWithOneMember) == {"a": 1}

    assert serialize(latest.EnumWithTwoMembers) == {"a": 1, "b": 2}


def test__enum_had__original_enum_is_empty(generate_test_version_packages: GenerateTestVersionPackages):
    v2000_01_01, v2001_01_01 = generate_test_version_packages(
        enum(latest.EmptyEnum).had(b=auto()),
    )

    assert serialize(v2000_01_01.EmptyEnum) == {"b": 1}
    assert serialize(v2001_01_01.EmptyEnum) == serialize(latest.EmptyEnum)


def test__enum_had__original_enum_is_nonempty(generate_test_version_packages: GenerateTestVersionPackages):
    if sys.platform.startswith("win"):
        time.sleep(1)
    v2000_01_01, v2001_01_01 = generate_test_version_packages(
        enum(latest.EnumWithOneMember).had(b=7),
    )

    assert serialize(v2000_01_01.EnumWithOneMember) == {"a": 1, "b": 7}
    assert serialize(v2001_01_01.EnumWithOneMember) == serialize(
        latest.EnumWithOneMember,
    )


def test__enum_didnt_have__original_enum_has_one_member(generate_test_version_packages: GenerateTestVersionPackages):
    v2000_01_01, v2001_01_01 = generate_test_version_packages(
        enum(latest.EnumWithOneMember).didnt_have("a"),
    )

    assert serialize(v2000_01_01.EnumWithOneMember) == {}
    assert serialize(latest.EnumWithOneMember) == serialize(
        v2001_01_01.EnumWithOneMember,
    )


def test__enum_didnt_have__original_enum_has_two_members(generate_test_version_packages: GenerateTestVersionPackages):
    v2000_01_01, v2001_01_01 = generate_test_version_packages(
        enum(latest.EnumWithTwoMembers).didnt_have("a"),
    )

    assert serialize(v2000_01_01.EnumWithTwoMembers) == {"b": 2}
    assert serialize(latest.EnumWithTwoMembers) == serialize(
        v2001_01_01.EnumWithTwoMembers,
    )


def test__enum_had__original_schema_is_empty(generate_test_version_packages: GenerateTestVersionPackages):
    v2000_01_01, v2001_01_01 = generate_test_version_packages(
        enum(latest.EmptyEnum).had(b=7),
    )

    assert serialize(v2000_01_01.EmptyEnum) == {"b": 7}
    assert serialize(v2001_01_01.EmptyEnum) == serialize(latest.EmptyEnum)


def test__field_existed_with__original_schema_is_empty(generate_test_version_packages: GenerateTestVersionPackages):
    v2000_01_01, v2001_01_01 = generate_test_version_packages(
        schema(latest.EmptySchema).field("bar").existed_with(type=int, info=Field(description="hewwo")),
    )
    assert len(v2001_01_01.EmptySchema.__fields__) == 0

    assert (
        inspect.getsource(v2000_01_01.EmptySchema)
        == "class EmptySchema(BaseModel):\n    bar: int = Field(description='hewwo')\n"
    )


def test__field_existed_with__original_schema_has_a_field(generate_test_version_packages: GenerateTestVersionPackages):
    v2000_01_01, v2001_01_01 = generate_test_version_packages(
        schema(latest.SchemaWithOneStrField).field("bar").existed_with(type=int, info=Field(description="hewwo")),
    )

    assert inspect.getsource(v2000_01_01.SchemaWithOneStrField) == (
        "class SchemaWithOneStrField(BaseModel):\n"
        "    foo: str = Field(default='foo')\n"
        "    bar: int = Field(description='hewwo')\n"
    )

    assert (
        inspect.getsource(v2001_01_01.SchemaWithOneStrField)
        == "class SchemaWithOneStrField(BaseModel):\n    foo: str = Field(default='foo')\n"
    )


def test__field_existed_with__extras_are_added__should_generate_properly(
    generate_test_version_packages: GenerateTestVersionPackages,
):
    v2000_01_01, v2001_01_01 = generate_test_version_packages(
        schema(latest.SchemaWithExtras).field("bar").existed_with(type=int, info=Field(deflolt="hewwo")),
    )

    assert inspect.getsource(v2000_01_01.SchemaWithExtras) == (
        "class SchemaWithExtras(BaseModel):\n"
        "    foo: str = Field(lulz='foo')\n"
        "    bar: int = Field(deflolt='hewwo')\n"
    )
    assert (
        inspect.getsource(v2001_01_01.SchemaWithExtras)
        == "class SchemaWithExtras(BaseModel):\n    foo: str = Field(lulz='foo')\n"
    )


def test__field_didnt_exist(generate_test_version_packages: GenerateTestVersionPackages):
    v2000_01_01, v2001_01_01 = generate_test_version_packages(
        schema(latest.SchemaWithOneStrField).field("foo").didnt_exist,
    )

    assert inspect.getsource(v2000_01_01.SchemaWithOneStrField) == "class SchemaWithOneStrField(BaseModel):\n    pass\n"

    assert (
        inspect.getsource(v2001_01_01.SchemaWithOneStrField)
        == "class SchemaWithOneStrField(BaseModel):\n    foo: str = Field(default='foo')\n"
    )


def test__field_didnt_exist__field_is_missing__should_raise_error(
    generate_test_version_packages: GenerateTestVersionPackages,
):
    with pytest.raises(
        InvalidGenerationInstructionError,
        match=re.escape(
            'You tried to delete a field "bar" from "SchemaWithOneStrField" in '
            '"SomeVersionChange" but it doesn\'t have such a field.',
        ),
    ):
        generate_test_version_packages(
            schema(latest.SchemaWithOneStrField).field("bar").didnt_exist,
        )


@pytest.mark.parametrize(
    ("attr", "attr_value"),
    [
        ("default", 100),
        ("alias", "myalias"),
        ("title", "mytitle"),
        ("description", "mydescription"),
        ("gt", 3),
        ("ge", 4),
        ("lt", 5),
        ("le", 6),
        ("multiple_of", 7),
        ("repr", False),
    ],
)
def test__field_had__int_field(attr: str, attr_value: Any, generate_test_version_packages: GenerateTestVersionPackages):
    """This test is here to guarantee that we can handle all parameter types we provide"""
    assert_field_had_changes_apply(latest.SchemaWithOneIntField, attr, attr_value, generate_test_version_packages)


@pytest.mark.parametrize(
    ("attr", "attr_value"),
    [
        ("min_length", 20),
        ("max_length", 50),
        ("regex", r"hewwo darkness"),
    ],
)
def test__field_had__str_field(attr: str, attr_value: Any, generate_test_version_packages: GenerateTestVersionPackages):
    assert_field_had_changes_apply(latest.SchemaWithOneStrField, attr, attr_value, generate_test_version_packages)


@pytest.mark.parametrize(
    ("attr", "attr_value"),
    [
        ("max_digits", 12),
        ("decimal_places", 15),
    ],
)
def test__field_had__decimal_field(
    attr: str,
    attr_value: Any,
    generate_test_version_packages: GenerateTestVersionPackages,
):
    assert_field_had_changes_apply(latest.SchemaWithOneDecimalField, attr, attr_value, generate_test_version_packages)


def test__field_had__default_factory(generate_test_version_packages: GenerateTestVersionPackages):
    v2000_01_01, v2001_01_01 = generate_test_version_packages(  # pragma: no cover
        schema(latest.SchemaWithOneIntField).field("foo").had(default_factory=lambda: 91),
    )

    assert v2000_01_01.SchemaWithOneIntField.__fields__["foo"].default_factory() == 91
    assert (
        v2001_01_01.SchemaWithOneIntField.__fields__["foo"].default_factory
        is latest.SchemaWithOneIntField.__fields__["foo"].default_factory
    )


def test__field_had__type(generate_test_version_packages: GenerateTestVersionPackages):
    v2000_01_01, v2001_01_01 = generate_test_version_packages(
        schema(latest.SchemaWithOneIntField).field("foo").had(type=bytes),
    )

    assert v2000_01_01.SchemaWithOneIntField.__fields__["foo"].annotation is bytes
    assert (
        v2001_01_01.SchemaWithOneIntField.__fields__["foo"].annotation
        is latest.SchemaWithOneIntField.__fields__["foo"].annotation
    )


@pytest.mark.parametrize(
    ("attr", "attr_value"),
    [
        ("exclude", [16, 17, 18]),
        ("include", [19, 20, 21]),
        ("min_items", 10),
        ("max_items", 15),
        ("unique_items", True),
    ],
)
def test__field_had__list_of_int_field(
    attr: str,
    attr_value: Any,
    generate_test_version_packages: GenerateTestVersionPackages,
):
    assert_field_had_changes_apply(latest.SchemaWithOneListOfIntField, attr, attr_value, generate_test_version_packages)


def test__field_had__float_field(generate_test_version_packages: GenerateTestVersionPackages):
    assert_field_had_changes_apply(
        latest.SchemaWithOneFloatField,
        "allow_inf_nan",
        attr_value=False,
        generate_test_version_packages=generate_test_version_packages,
    )


def test__schema_field_had__change_to_the_same_field_type__should_raise_error(
    generate_test_version_packages: GenerateTestVersionPackages,
):
    with pytest.raises(
        InvalidGenerationInstructionError,
        match=re.escape(
            'You tried to change the type of field "foo" to "<class \'int\'>" from'
            ' "SchemaWithOneIntField" in "SomeVersionChange" but it already has type "<class \'int\'>"',
        ),
    ):
        generate_test_version_packages(
            schema(latest.SchemaWithOneIntField).field("foo").had(type=int),
        )


def test__schema_field_had__change_attr_to_same_value__should_raise_error(
    generate_test_version_packages: GenerateTestVersionPackages,
):
    with pytest.raises(
        InvalidGenerationInstructionError,
        match=re.escape(
            'You tried to change the attribute "default" of field "foo" from "SchemaWithOneStrField" to \'foo\' '
            'in "SomeVersionChange" but it already has that value.',
        ),
    ):
        generate_test_version_packages(
            schema(latest.SchemaWithOneStrField).field("foo").had(default="foo"),
        )


def test__schema_field_had__nonexistent_field__should_raise_error(
    generate_test_version_packages: GenerateTestVersionPackages,
):
    with pytest.raises(
        InvalidGenerationInstructionError,
        match=re.escape(
            'You tried to change the type of field "boo" from "SchemaWithOneIntField" in '
            '"SomeVersionChange" but it doesn\'t have such a field.',
        ),
    ):
        generate_test_version_packages(
            schema(latest.SchemaWithOneIntField).field("boo").had(type=int),
        )


def test__enum_had__same_name_as_other_value__error(generate_test_version_packages: GenerateTestVersionPackages):
    with pytest.raises(
        InvalidGenerationInstructionError,
        match=re.escape(
            'You tried to add a member "a" to "EnumWithOneMember" in '
            '"SomeVersionChange" but there is already a member with that name and value.',
        ),
    ):
        generate_test_version_packages(enum(latest.EnumWithOneMember).had(a=1))


def test__enum_didnt_have__nonexisting_name__error(generate_test_version_packages: GenerateTestVersionPackages):
    with pytest.raises(
        InvalidGenerationInstructionError,
        match=re.escape(
            'You tried to delete a member "foo" from "EmptyEnum" in '
            '"SomeVersionChange" but it doesn\'t have such a member.',
        ),
    ):
        generate_test_version_packages(enum(latest.EmptyEnum).didnt_have("foo"))


def test__codegen__with_deleted_source_file__error(generate_test_version_packages: GenerateTestVersionPackages):
    Path("tests/_data/latest/another_temp1").mkdir(exist_ok=True)
    Path("tests/_data/latest/another_temp1/hello.py").touch()
    from tests._data.latest.another_temp1 import hello  # pyright: ignore[reportMissingImports]

    with pytest.raises(
        CodeGenerationError,
        match="Module <module 'tests._data.latest.another_temp1.hello' from .+ is not a package",
    ):
        generate_test_version_packages(
            enum(latest.EnumWithOneMember).didnt_have("foo"),
            package=hello,
        )


def test__codegen__non_python_files__copied_to_all_dirs(generate_test_version_packages: GenerateTestVersionPackages):
    generate_test_version_packages()
    assert json.loads(
        Path("tests/_data/v2000_01_01/json_files/foo.json").read_text(),
    ) == {"hello": "world"}
    assert json.loads(
        Path("tests/_data/v2001_01_01/json_files/foo.json").read_text(),
    ) == {"hello": "world"}


def test__codegen__non_pydantic_schema__error(generate_test_version_packages: GenerateTestVersionPackages):
    with pytest.raises(
        CodeGenerationError,
        match=re.escape(
            "Model <class 'tests._data.latest.NonPydanticSchema'> is not a subclass of BaseModel",
        ),
    ):
        generate_test_version_packages(
            schema(latest.NonPydanticSchema).field("foo").didnt_exist,  # pyright: ignore[reportGeneralTypeIssues]
        )


def test__codegen__schema_that_overrides_fields_from_mro(generate_test_version_packages: GenerateTestVersionPackages):
    v2000_01_01, v2001_01_01 = generate_test_version_packages(
        schema(latest.SchemaThatOverridesField).field("bar").existed_with(type=int),
    )

    assert (
        inspect.getsource(v2000_01_01.SchemaThatOverridesField)
        == "class SchemaThatOverridesField(SchemaWithOneIntField):\n    foo: bytes = Field()\n    bar: int = Field()\n"
    )

    assert (
        inspect.getsource(v2001_01_01.SchemaThatOverridesField)
        == "class SchemaThatOverridesField(SchemaWithOneIntField):\n    foo: bytes = Field()\n"
    )


def test__codegen_schema_existed_with(generate_test_version_packages: GenerateTestVersionPackages):
    v2000_01_01, v2001_01_01 = generate_test_version_packages(
        schema(latest.EmptySchema).field("bar").existed_with(type=int, info=Field(example=83)),
    )

    assert (
        inspect.getsource(v2000_01_01.EmptySchema)
        == "class EmptySchema(BaseModel):\n    bar: int = Field(example=83)\n"
    )

    assert inspect.getsource(v2001_01_01.EmptySchema) == "class EmptySchema(BaseModel):\n    pass\n"


def test__codegen_schema_field_existed_with__already_existing_field__should_raise_error(
    generate_test_version_packages: GenerateTestVersionPackages,
):
    with pytest.raises(
        InvalidGenerationInstructionError,
        match=re.escape(
            'You tried to add a field "foo" to "SchemaWithOneIntField" in '
            '"SomeVersionChange" but there is already a field with that name.',
        ),
    ):
        generate_test_version_packages(
            schema(latest.SchemaWithOneIntField).field("foo").existed_with(type=str),
        )


def test__codegen__schema_defined_in_a_non_init_file(generate_test_version_packages: GenerateTestVersionPackages):
    from tests._data.latest.some_schema import MySchema

    generate_test_version_packages(schema(MySchema).field("foo").didnt_exist)

    from tests._data.v2000_01_01.some_schema import MySchema as MySchema2000  # pyright: ignore[reportMissingImports]
    from tests._data.v2001_01_01.some_schema import MySchema as MySchema2001  # pyright: ignore[reportMissingImports]

    assert inspect.getsource(MySchema2000) == "class MySchema(BaseModel):\n    pass\n"

    assert inspect.getsource(MySchema2001) == "class MySchema(BaseModel):\n    foo: int = Field()\n"


def test__codegen__with_weird_data_types(generate_test_version_packages: GenerateTestVersionPackages):
    generate_test_version_packages(
        schema(weird_schemas.ModelWithWeirdFields).field("bad").existed_with(type=int),
    )

    from tests._data.v2000_01_01.weird_schemas import (  # pyright: ignore[reportMissingImports]
        ModelWithWeirdFields as MySchema2000,
    )
    from tests._data.v2001_01_01.weird_schemas import (  # pyright: ignore[reportMissingImports]
        ModelWithWeirdFields as MySchema2001,
    )

    assert inspect.getsource(MySchema2000) == (
        "class ModelWithWeirdFields(BaseModel):\n"
        "    foo: dict = Field(default={'a': 'b'})\n"
        "    bar: list[int] = Field(default_factory=my_default_factory)\n"
        "    baz: typing.Literal[MyEnum.baz] = Field()\n"
        "    bad: int = Field()\n"
    )

    assert inspect.getsource(MySchema2001) == (
        "class ModelWithWeirdFields(BaseModel):\n"
        "    foo: dict = Field(default={'a': 'b'})\n"
        "    bar: list[int] = Field(default_factory=my_default_factory)\n"
        "    baz: typing.Literal[MyEnum.baz] = Field()\n"
    )


def test__codegen_union_fields(generate_test_version_packages: GenerateTestVersionPackages):
    v2000_01_01, v2001_01_01 = generate_test_version_packages(
        schema(latest.SchemaWithUnionFields).field("baz").existed_with(type=int | latest.EmptySchema),
        schema(latest.SchemaWithUnionFields).field("daz").existed_with(type=Union[int, latest.EmptySchema]),
    )

    assert inspect.getsource(v2000_01_01.SchemaWithUnionFields) == (
        "class SchemaWithUnionFields(BaseModel):\n"
        "    foo: typing.Union[int, str] = Field()\n"
        "    bar: typing.Union[EmptySchema, None] = Field()\n"
        "    baz: typing.Union[int, EmptySchema] = Field()\n"
        "    daz: typing.Union[int, EmptySchema] = Field()\n"
    )
    assert inspect.getsource(v2001_01_01.SchemaWithUnionFields) == (
        "class SchemaWithUnionFields(BaseModel):\n"
        "    foo: typing.Union[int, str] = Field()\n"
        "    bar: typing.Union[EmptySchema, None] = Field()\n"
    )


def test__codegen_imports_and_aliases(generate_test_version_packages: GenerateTestVersionPackages):
    v2000_01_01, v2001_01_01 = generate_test_version_packages(
        schema(latest.EmptySchemaWithArbitraryTypesAllowed)
        .field("foo")
        .existed_with(type="Logger", import_from="logging", import_as="MyLogger"),
        schema(latest.EmptySchemaWithArbitraryTypesAllowed)
        .field("bar")
        .existed_with(
            type=UnversionedSchema3,
            import_from="..unversioned_schemas",
            import_as="MyLittleSchema",
        ),
        schema(latest.EmptySchemaWithArbitraryTypesAllowed)
        .field("baz")
        .existed_with(type=UnversionedSchema2, import_from="..unversioned_schema_dir"),
    )
    assert inspect.getsource(v2000_01_01.EmptySchemaWithArbitraryTypesAllowed) == (
        "class EmptySchemaWithArbitraryTypesAllowed(BaseModel, arbitrary_types_allowed=True):\n"
        "    foo: 'MyLogger' = Field()\n"
        "    bar: 'MyLittleSchema' = Field()\n"
        "    baz: UnversionedSchema2 = Field()\n"
    )
    assert inspect.getsource(v2001_01_01.EmptySchemaWithArbitraryTypesAllowed) == (
        "class EmptySchemaWithArbitraryTypesAllowed(BaseModel, arbitrary_types_allowed=True):\n    pass\n"
    )


def test__codegen_imports_and_aliases__alias_without_import__should_raise_error():
    with pytest.raises(
        UniversiStructureError,
        match=re.escape('Field "baz" has "import_as" but not "import_from" which is prohibited'),
    ):
        schema(latest.SchemaWithOneFloatField).field("baz").existed_with(type=str, import_as="MyStr")


def test__codegen_unions__init_file(generate_test_version_packages: GenerateTestVersionPackages):
    generate_test_version_packages()
    from tests._data import v2000_01_01, v2001_01_01  # pyright: ignore[reportGeneralTypeIssues]
    from tests._data.unions import (  # pyright: ignore[reportMissingImports]
        EnumWithOneMember,
        SchemaWithOneIntField,
    )

    assert EnumWithOneMember == v2000_01_01.EnumWithOneMember | v2001_01_01.EnumWithOneMember | latest.EnumWithOneMember
    assert (
        SchemaWithOneIntField
        == v2000_01_01.SchemaWithOneIntField | v2001_01_01.SchemaWithOneIntField | latest.SchemaWithOneIntField
    )


def test__codegen_unions__regular_file(generate_test_version_packages: GenerateTestVersionPackages):
    generate_test_version_packages()
    from tests._data.latest.some_schema import MySchema as MySchemaLatest
    from tests._data.unions.some_schema import MySchema  # pyright: ignore[reportMissingImports]
    from tests._data.v2000_01_01.some_schema import MySchema as MySchema2000  # pyright: ignore[reportMissingImports]
    from tests._data.v2001_01_01.some_schema import MySchema as MySchema2001  # pyright: ignore[reportMissingImports]

    assert MySchema == MySchema2000 | MySchema2001 | MySchemaLatest


def test__codegen_property(api_version_var: ContextVar[date | None]):
    def baz_property(hewwo: Any):
        raise NotImplementedError

    class VersionChange2(VersionChange):
        description = "..."
        instructions_to_migrate_to_previous_version = (
            schema(latest.SchemaWithOneFloatField).property("baz")(baz_property),
        )

        @schema(latest.SchemaWithOneFloatField).had_property("bar")
        def bar_property(arg1: list[str]):
            return 83

    class VersionChange1(VersionChange):
        description = "..."
        instructions_to_migrate_to_previous_version = (
            schema(latest.SchemaWithOneFloatField).property("bar").didnt_exist,
        )

    assert VersionChange2.bar_property([]) == 83

    regenerate_dir_to_all_versions(
        latest,
        VersionBundle(
            Version(date(2002, 1, 1), VersionChange2),
            Version(date(2001, 1, 1), VersionChange1),
            Version(date(2000, 1, 1)),
            api_version_var=api_version_var,
        ),
    )

    from tests._data import v2000_01_01, v2001_01_01, v2002_01_01  # pyright: ignore[reportGeneralTypeIssues]

    assert inspect.getsource(v2000_01_01.SchemaWithOneFloatField) == (
        "class SchemaWithOneFloatField(BaseModel):\n"
        "    foo: float = Field()\n\n"
        "    @property\n"
        "    def baz(hewwo):\n"
        "        raise NotImplementedError\n"
    )

    assert inspect.getsource(v2001_01_01.SchemaWithOneFloatField) == (
        "class SchemaWithOneFloatField(BaseModel):\n"
        "    foo: float = Field()\n\n"
        "    @property\n"
        "    def baz(hewwo):\n"
        "        raise NotImplementedError\n\n"
        "    @property\n"
        "    def bar(arg1):\n"
        "        return 83\n"
    )

    assert inspect.getsource(v2002_01_01.SchemaWithOneFloatField) == (
        "class SchemaWithOneFloatField(BaseModel):\n    foo: float = Field()\n"
    )


def test__codegen_delete_nonexistent_property(generate_test_version_packages: GenerateTestVersionPackages):
    with pytest.raises(
        InvalidGenerationInstructionError,
        match=re.escape(
            'You tried to delete a property "bar" from "SchemaWithOneFloatField" in '
            '"SomeVersionChange" but there is no such property defined in any of the migrations.',
        ),
    ):
        generate_test_version_packages(
            schema(latest.SchemaWithOneFloatField).property("bar").didnt_exist,
        )


def test__codegen_lambda_property(generate_test_version_packages: GenerateTestVersionPackages):
    with pytest.raises(
        CodeGenerationError,
        match=re.escape(
            'Failed to migrate class "SchemaWithOneFloatField" to an older version because: '
            "You passed a lambda as a schema property. It is not supported yet. "
            "Please, use a regular function instead. The lambda you have passed: "
            'schema(latest.SchemaWithOneFloatField).property("bar")(lambda _: "Hewwo"),  # pragma: no cover\n',
        ),
    ):
        generate_test_version_packages(
            schema(latest.SchemaWithOneFloatField).property("bar")(lambda _: "Hewwo"),  # pragma: no cover
        )


def test__codegen_property_with_wrong_number_of_args():
    def baz(hello: Any, world: Any):
        raise NotImplementedError

    with pytest.raises(
        UniversiStructureError,
        match=re.escape("Property 'baz' must have one argument and it has 2"),
    ):
        schema(latest.SchemaWithOneFloatField).property("baz")(baz)


def test__codegen_property__there_is_already_field_with_the_same_name__error(
    generate_test_version_packages: GenerateTestVersionPackages,
):
    def baz(hello: Any):
        raise NotImplementedError

    with pytest.raises(
        InvalidGenerationInstructionError,
        match=re.escape(
            'You tried to define a property "foo" inside "SchemaWithOneFloatField" in '
            '"SomeVersionChange" but there is already a field with that name.',
        ),
    ):
        generate_test_version_packages(
            schema(latest.SchemaWithOneFloatField).property("foo")(baz),
        )


def test__codegen_schema_had_name__dependent_schema_is_not_altered(api_version_var: ContextVar[date | None]):
    class VersionChange2(VersionChange):
        description = "..."
        instructions_to_migrate_to_previous_version = [
            schema(latest.SchemaWithOneFloatField).had(name="MyFloatySchema"),
        ]

    class VersionChange1(VersionChange):
        description = "..."
        instructions_to_migrate_to_previous_version = [
            schema(latest.SchemaWithOneFloatField).had(name="MyFloatySchema2"),
        ]

    regenerate_dir_to_all_versions(
        latest,
        VersionBundle(
            Version(date(2002, 1, 1), VersionChange2),
            Version(date(2001, 1, 1), VersionChange1),
            Version(date(2000, 1, 1)),
            api_version_var=api_version_var,
        ),
    )

    from tests._data import v2000_01_01, v2001_01_01, v2002_01_01  # pyright: ignore[reportGeneralTypeIssues]

    assert inspect.getsource(v2000_01_01.MyFloatySchema2) == (
        "class MyFloatySchema2(BaseModel):\n    foo: float = Field()\n"
    )
    assert inspect.getsource(v2001_01_01.MyFloatySchema) == (
        "class MyFloatySchema(BaseModel):\n    foo: float = Field()\n"
    )
    assert inspect.getsource(v2002_01_01.SchemaWithOneFloatField) == (
        "class SchemaWithOneFloatField(BaseModel):\n    foo: float = Field()\n"
    )
    assert inspect.getsource(v2000_01_01.SchemaThatDependsOnAnotherSchema) == (
        "class SchemaThatDependsOnAnotherSchema(MyFloatySchema2):\n"
        "    foo: MyFloatySchema2\n"
        "    bat: MyFloatySchema2 | int = Field(default=MyFloatySchema2(foo=3.14))\n\n"
        "    def baz(self, daz: MyFloatySchema2) -> MyFloatySchema2:\n"
        "        return MyFloatySchema2(foo=3.14)\n"
    )
    assert inspect.getsource(v2001_01_01.SchemaThatDependsOnAnotherSchema) == (
        "class SchemaThatDependsOnAnotherSchema(MyFloatySchema):\n"
        "    foo: MyFloatySchema\n"
        "    bat: MyFloatySchema | int = Field(default=MyFloatySchema(foo=3.14))\n\n"
        "    def baz(self, daz: MyFloatySchema) -> MyFloatySchema:\n"
        "        return MyFloatySchema(foo=3.14)\n"
    )
    assert inspect.getsource(v2002_01_01.SchemaThatDependsOnAnotherSchema) == (
        "class SchemaThatDependsOnAnotherSchema(SchemaWithOneFloatField):\n"
        "    foo: SchemaWithOneFloatField\n"
        "    bat: SchemaWithOneFloatField | int = Field(default=SchemaWithOneFloatField(foo=3.14))\n\n"
        "    def baz(self, daz: SchemaWithOneFloatField) -> SchemaWithOneFloatField:\n"
        "        return SchemaWithOneFloatField(foo=3.14)\n"
    )

    from tests._data.v2000_01_01.some_schema import SchemaThatDependsOnAnotherSchema

    assert inspect.getsource(SchemaThatDependsOnAnotherSchema) == (
        "class SchemaThatDependsOnAnotherSchema(BaseModel):\n    foo: MyFloatySchema2\n    bar: int\n"
    )

    from tests._data.v2001_01_01.some_schema import SchemaThatDependsOnAnotherSchema

    assert inspect.getsource(SchemaThatDependsOnAnotherSchema) == (
        "class SchemaThatDependsOnAnotherSchema(BaseModel):\n    foo: MyFloatySchema\n    bar: int\n"
    )
    from tests._data.v2002_01_01.some_schema import SchemaThatDependsOnAnotherSchema

    assert inspect.getsource(SchemaThatDependsOnAnotherSchema) == (
        "class SchemaThatDependsOnAnotherSchema(BaseModel):\n    foo: SchemaWithOneFloatField\n    bar: int\n"
    )
    from tests._data.unions import SchemaWithOneFloatField

    assert str(SchemaWithOneFloatField) == (
        "tests._data.latest.SchemaWithOneFloatField | "
        "tests._data.v2002_01_01.SchemaWithOneFloatField | "
        "tests._data.v2001_01_01.MyFloatySchema | "
        "tests._data.v2000_01_01.MyFloatySchema2"
    )


def test__codegen_schema_had_name__dependent_schema_is_altered(api_version_var: ContextVar[date | None]):
    class VersionChange2(VersionChange):
        description = "..."
        instructions_to_migrate_to_previous_version = [
            schema(latest.SchemaWithOneFloatField).had(name="MyFloatySchema"),
            schema(latest.SchemaThatDependsOnAnotherSchema).field("gaz").existed_with(type=int),
            schema(some_schema.SchemaThatDependsOnAnotherSchema).field("bar").didnt_exist,
        ]

    class VersionChange1(VersionChange):
        description = "..."
        instructions_to_migrate_to_previous_version = [
            schema(latest.SchemaWithOneFloatField).had(name="MyFloatySchema2"),
            schema(latest.SchemaThatDependsOnAnotherSchema).field("gaz").didnt_exist,
        ]

    regenerate_dir_to_all_versions(
        latest,
        VersionBundle(
            Version(date(2002, 1, 1), VersionChange2),
            Version(date(2001, 1, 1), VersionChange1),
            Version(date(2000, 1, 1)),
            api_version_var=api_version_var,
        ),
    )

    from tests._data import v2000_01_01, v2001_01_01, v2002_01_01  # pyright: ignore[reportGeneralTypeIssues]

    assert inspect.getsource(v2000_01_01.MyFloatySchema2) == (
        "class MyFloatySchema2(BaseModel):\n    foo: float = Field()\n"
    )
    assert inspect.getsource(v2001_01_01.MyFloatySchema) == (
        "class MyFloatySchema(BaseModel):\n    foo: float = Field()\n"
    )
    assert inspect.getsource(v2002_01_01.SchemaWithOneFloatField) == (
        "class SchemaWithOneFloatField(BaseModel):\n    foo: float = Field()\n"
    )
    assert (
        inspect.getsource(v2000_01_01.SchemaThatDependsOnAnotherSchema)
        == "class SchemaThatDependsOnAnotherSchema(MyFloatySchema2):\n"
        "    foo: MyFloatySchema2 = Field()\n"
        "    bat: typing.Union[MyFloatySchema2, int] = Field(default=MyFloatySchema2(foo=3.14))\n\n"
        "    def baz(self, daz: MyFloatySchema2) -> MyFloatySchema2:\n"
        "        return MyFloatySchema2(foo=3.14)\n"
    )
    assert (
        inspect.getsource(v2001_01_01.SchemaThatDependsOnAnotherSchema)
        == "class SchemaThatDependsOnAnotherSchema(MyFloatySchema):\n"
        "    foo: MyFloatySchema = Field()\n"
        "    bat: typing.Union[MyFloatySchema, int] = Field(default=MyFloatySchema(foo=3.14))\n"
        "    gaz: int = Field()\n\n"
        "    def baz(self, daz: MyFloatySchema) -> MyFloatySchema:\n"
        "        return MyFloatySchema(foo=3.14)\n"
    )
    assert (
        inspect.getsource(v2002_01_01.SchemaThatDependsOnAnotherSchema)
        == "class SchemaThatDependsOnAnotherSchema(SchemaWithOneFloatField):\n"
        "    foo: SchemaWithOneFloatField = Field()\n"
        "    bat: typing.Union[SchemaWithOneFloatField, int] = Field(default=SchemaWithOneFloatField(foo=3.14))\n\n"
        "    def baz(self, daz: SchemaWithOneFloatField) -> SchemaWithOneFloatField:\n"
        "        return SchemaWithOneFloatField(foo=3.14)\n"
    )
    from tests._data.v2000_01_01.some_schema import SchemaThatDependsOnAnotherSchema

    assert inspect.getsource(SchemaThatDependsOnAnotherSchema) == (
        "class SchemaThatDependsOnAnotherSchema(BaseModel):\n    foo: MyFloatySchema2 = Field()\n"
    )

    from tests._data.v2001_01_01.some_schema import SchemaThatDependsOnAnotherSchema

    assert inspect.getsource(SchemaThatDependsOnAnotherSchema) == (
        "class SchemaThatDependsOnAnotherSchema(BaseModel):\n    foo: MyFloatySchema = Field()\n"
    )
    from tests._data.v2002_01_01.some_schema import SchemaThatDependsOnAnotherSchema

    assert inspect.getsource(SchemaThatDependsOnAnotherSchema) == (
        "class SchemaThatDependsOnAnotherSchema(BaseModel):\n"
        "    foo: SchemaWithOneFloatField = Field()\n"
        "    bar: int = Field()\n"
    )

    from tests._data.unions import SchemaWithOneFloatField

    assert str(SchemaWithOneFloatField) == (
        "tests._data.latest.SchemaWithOneFloatField | "
        "tests._data.v2002_01_01.SchemaWithOneFloatField | "
        "tests._data.v2001_01_01.MyFloatySchema | "
        "tests._data.v2000_01_01.MyFloatySchema2"
    )
