from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal, Union, cast

import attrs
from issubclass import issubclass as lenient_issubclass
from pydantic import BaseModel, Field
from pydantic._internal._decorators import PydanticDescriptorProxy, unwrap_wrapped_function
from pydantic.fields import FieldInfo

from cadwyn._utils import Sentinel, fully_unwrap_decorator
from cadwyn.exceptions import CadwynStructureError

from .common import _HiddenAttributeMixin

if TYPE_CHECKING:
    from pydantic.typing import AbstractSetIntStr, MappingIntStrAny


PossibleFieldAttributes = Literal[
    "default",
    "default_factory",
    "alias",
    "title",
    "description",
    "exclude",
    "const",
    "gt",
    "ge",
    "lt",
    "le",
    "deprecated",
    "fail_fast",
    "strict",
    "multiple_of",
    "allow_inf_nan",
    "max_digits",
    "decimal_places",
    "min_length",
    "max_length",
    "allow_mutation",
    "pattern",
    "discriminator",
    "repr",
]


@attrs.define(slots=True)
class FieldChanges:
    default: Any
    default_factory: Any
    alias: str
    title: str
    description: str
    exclude: AbstractSetIntStr | MappingIntStrAny | Any
    const: bool
    deprecated: bool
    fail_fast: bool
    gt: float
    ge: float
    lt: float
    le: float
    strict: bool
    multiple_of: float
    allow_inf_nan: bool
    max_digits: int
    decimal_places: int
    min_length: int
    max_length: int
    allow_mutation: bool
    pattern: str
    discriminator: str
    repr: bool


@attrs.define(slots=True)
class FieldHadInstruction(_HiddenAttributeMixin):
    schema: type[BaseModel]
    name: str
    type: type
    field_changes: FieldChanges
    new_name: str


@attrs.define(slots=True)
class FieldDidntHaveInstruction(_HiddenAttributeMixin):
    schema: type[BaseModel]
    name: str
    attributes: tuple[str, ...]


@attrs.define(slots=True)
class FieldDidntExistInstruction(_HiddenAttributeMixin):
    schema: type[BaseModel]
    name: str


@attrs.define(slots=True)
class FieldExistedAsInstruction(_HiddenAttributeMixin):
    schema: type[BaseModel]
    name: str
    field: FieldInfo


# TODO (https://github.com/zmievsa/cadwyn/issues/112): Add an ability to add extras
@attrs.define(slots=True)
class AlterFieldInstructionFactory:
    schema: type[BaseModel]
    name: str

    def had(
        self,
        *,
        name: str = Sentinel,
        type: Any = Sentinel,
        default: Any = Sentinel,
        default_factory: Callable = Sentinel,
        alias: str = Sentinel,
        title: str = Sentinel,
        description: str = Sentinel,
        exclude: AbstractSetIntStr | MappingIntStrAny | Any = Sentinel,
        const: bool = Sentinel,
        gt: float = Sentinel,
        ge: float = Sentinel,
        lt: float = Sentinel,
        le: float = Sentinel,
        strict: bool = Sentinel,
        deprecated: bool = Sentinel,
        multiple_of: float = Sentinel,
        allow_inf_nan: bool = Sentinel,
        max_digits: int = Sentinel,
        decimal_places: int = Sentinel,
        min_length: int = Sentinel,
        max_length: int = Sentinel,
        allow_mutation: bool = Sentinel,
        pattern: str = Sentinel,
        discriminator: str = Sentinel,
        repr: bool = Sentinel,
        fail_fast: bool = Sentinel,
    ) -> FieldHadInstruction:
        return FieldHadInstruction(
            schema=self.schema,
            name=self.name,
            type=type,
            new_name=name,
            field_changes=FieldChanges(
                default=default,
                default_factory=default_factory,
                alias=alias,
                title=title,
                description=description,
                exclude=exclude,
                const=const,
                gt=gt,
                ge=ge,
                lt=lt,
                le=le,
                deprecated=deprecated,
                strict=strict,
                multiple_of=multiple_of,
                allow_inf_nan=allow_inf_nan,
                max_digits=max_digits,
                decimal_places=decimal_places,
                min_length=min_length,
                max_length=max_length,
                allow_mutation=allow_mutation,
                pattern=pattern,
                discriminator=discriminator,
                repr=repr,
                fail_fast=fail_fast,
            ),
        )

    def didnt_have(self, *attributes: PossibleFieldAttributes) -> FieldDidntHaveInstruction:
        for attribute in attributes:
            if attribute not in attrs.fields_dict(FieldChanges):
                raise CadwynStructureError(
                    f"Unknown attribute {attribute!r}. Are you sure it's a valid field attribute?"
                )
        return FieldDidntHaveInstruction(self.schema, self.name, attributes)

    @property
    def didnt_exist(self) -> FieldDidntExistInstruction:
        return FieldDidntExistInstruction(self.schema, name=self.name)

    def existed_as(
        self,
        *,
        type: Any,
        info: FieldInfo | Any | None = None,
    ) -> FieldExistedAsInstruction:
        if info is None:
            info = cast(FieldInfo, Field())
        info.annotation = type
        return FieldExistedAsInstruction(self.schema, name=self.name, field=info)


def _get_model_decorators(model: type[BaseModel]):
    return [
        *model.__pydantic_decorators__.validators.values(),
        *model.__pydantic_decorators__.field_validators.values(),
        *model.__pydantic_decorators__.root_validators.values(),
        *model.__pydantic_decorators__.field_serializers.values(),
        *model.__pydantic_decorators__.model_serializers.values(),
        *model.__pydantic_decorators__.model_validators.values(),
        *model.__pydantic_decorators__.computed_fields.values(),
    ]


@attrs.define(slots=True)
class ValidatorExistedInstruction:
    schema: type[BaseModel]
    validator: Callable[..., Any] | PydanticDescriptorProxy


@attrs.define(slots=True)
class ValidatorDidntExistInstruction:
    schema: type[BaseModel]
    name: str


@attrs.define(slots=True)
class AlterValidatorInstructionFactory:
    schema: type[BaseModel]
    func: Callable[..., Any] | PydanticDescriptorProxy

    @property
    def existed(self) -> ValidatorExistedInstruction:
        return ValidatorExistedInstruction(self.schema, self.func)

    @property
    def didnt_exist(self) -> ValidatorDidntExistInstruction:
        return ValidatorDidntExistInstruction(self.schema, self.func.__name__)


AlterSchemaSubInstruction = Union[
    FieldHadInstruction,
    FieldDidntHaveInstruction,
    FieldDidntExistInstruction,
    FieldExistedAsInstruction,
    ValidatorExistedInstruction,
    ValidatorDidntExistInstruction,
]


@attrs.define(slots=True)
class SchemaHadInstruction(_HiddenAttributeMixin):
    schema: type[BaseModel]
    name: str


@attrs.define(slots=True)
class AlterSchemaInstructionFactory:
    schema: type[BaseModel]

    def field(self, name: str, /) -> AlterFieldInstructionFactory:
        return AlterFieldInstructionFactory(self.schema, name)

    def validator(
        self, func: Callable[..., Any] | classmethod[Any, Any, Any] | PydanticDescriptorProxy, /
    ) -> AlterValidatorInstructionFactory:
        func = cast("Callable | PydanticDescriptorProxy", unwrap_wrapped_function(func))

        if not isinstance(func, PydanticDescriptorProxy):
            if hasattr(func, "__self__"):
                owner = func.__self__
                if lenient_issubclass(owner, BaseModel) and any(  # pragma: no branch
                    fully_unwrap_decorator(decorator.func, decorator.shim) == func
                    for decorator in _get_model_decorators(owner)
                ):
                    return AlterValidatorInstructionFactory(self.schema, func)
            raise CadwynStructureError("The passed function must be a pydantic validator")
        return AlterValidatorInstructionFactory(self.schema, func)

    def had(self, *, name: str) -> SchemaHadInstruction:
        return SchemaHadInstruction(self.schema, name)


def schema(model: type[BaseModel], /) -> AlterSchemaInstructionFactory:
    return AlterSchemaInstructionFactory(model)
