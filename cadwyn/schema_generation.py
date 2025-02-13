import copy
import dataclasses
import functools
import inspect
import types
import typing
from collections.abc import Callable, Sequence
from datetime import date
from enum import Enum
from functools import cache
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Generic,
    TypeAlias,
    TypeVar,
    _BaseGenericAlias,  # pyright: ignore[reportAttributeAccessIssue]
    cast,
    final,
    get_args,
    get_origin,
    overload,
)

import fastapi.params
import fastapi.security.base
import fastapi.utils
import pydantic
import pydantic._internal._decorators
from fastapi import Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field, RootModel
from pydantic._internal import _decorators
from pydantic._internal._decorators import (
    FieldSerializerDecoratorInfo,
    FieldValidatorDecoratorInfo,
    ModelSerializerDecoratorInfo,
    ModelValidatorDecoratorInfo,
    RootValidatorDecoratorInfo,
    ValidatorDecoratorInfo,
)
from pydantic._internal._typing_extra import try_eval_type as pydantic_try_eval_type
from pydantic.fields import ComputedFieldInfo, FieldInfo
from typing_extensions import Doc, Self, _AnnotatedAlias, assert_never

from cadwyn._utils import Sentinel, UnionType, fully_unwrap_decorator, lenient_issubclass
from cadwyn.exceptions import InvalidGenerationInstructionError
from cadwyn.structure.common import VersionDate
from cadwyn.structure.data import ResponseInfo
from cadwyn.structure.enums import AlterEnumSubInstruction, EnumDidntHaveMembersInstruction, EnumHadMembersInstruction
from cadwyn.structure.schemas import (
    AlterSchemaSubInstruction,
    FieldDidntExistInstruction,
    FieldDidntHaveInstruction,
    FieldExistedAsInstruction,
    FieldHadInstruction,
    SchemaHadInstruction,
    ValidatorDidntExistInstruction,
    ValidatorExistedInstruction,
    _get_model_decorators,
)
from cadwyn.structure.versions import _CADWYN_REQUEST_PARAM_NAME, _CADWYN_RESPONSE_PARAM_NAME, VersionBundle

if TYPE_CHECKING:
    from cadwyn.structure.versions import HeadVersion, Version, VersionBundle

_Call = TypeVar("_Call", bound=Callable[..., Any])

_FieldName: TypeAlias = str
_T_ANY_MODEL = TypeVar("_T_ANY_MODEL", bound=BaseModel | Enum)
_T_ENUM = TypeVar("_T_ENUM", bound=Enum)

_T_PYDANTIC_MODEL = TypeVar("_T_PYDANTIC_MODEL", bound=BaseModel)
PYDANTIC_DECORATOR_TYPE_TO_DECORATOR_MAP = {
    ValidatorDecoratorInfo: pydantic.validator,  # pyright: ignore[reportDeprecated]
    FieldValidatorDecoratorInfo: pydantic.field_validator,
    FieldSerializerDecoratorInfo: pydantic.field_serializer,
    RootValidatorDecoratorInfo: pydantic.root_validator,  # pyright: ignore[reportDeprecated]
    ModelValidatorDecoratorInfo: pydantic.model_validator,
    ModelSerializerDecoratorInfo: pydantic.model_serializer,
    ComputedFieldInfo: pydantic.computed_field,
}


VALIDATOR_CONFIG_KEY = "__validators__"
_all_field_arg_names = sorted(
    [
        name
        for name, param in inspect.signature(Field).parameters.items()
        if param.kind in {inspect._ParameterKind.KEYWORD_ONLY, inspect._ParameterKind.POSITIONAL_OR_KEYWORD}
    ],
)
EXTRA_FIELD_NAME = "json_schema_extra"


_empty_field_info = Field()
dict_of_empty_field_info = {k: getattr(_empty_field_info, k) for k in FieldInfo.__slots__}


@dataclasses.dataclass(slots=True)
class PydanticFieldWrapper:
    """We DO NOT maintain field.metadata at all"""

    init_model_field: dataclasses.InitVar[FieldInfo]

    annotation: Any
    name_from_newer_version: str

    passed_field_attributes: dict[str, Any] = dataclasses.field(init=False)

    def __post_init__(self, init_model_field: FieldInfo):
        self.passed_field_attributes = _extract_passed_field_attributes(init_model_field)

    def update_attribute(self, *, name: str, value: Any):
        self.passed_field_attributes[name] = value

    def delete_attribute(self, *, name: str) -> None:
        self.passed_field_attributes.pop(name)

    def generate_field_copy(self, generator: "SchemaGenerator") -> pydantic.fields.FieldInfo:
        return pydantic.Field(
            **generator.annotation_transformer.change_version_of_annotation(self.passed_field_attributes)
        )


def _extract_passed_field_attributes(field_info: FieldInfo):
    attributes = {
        attr_name: field_info._attributes_set[attr_name]
        for attr_name in _all_field_arg_names
        if attr_name in field_info._attributes_set
    }
    # PydanticV2 always adds frozen to _attributes_set but we don't want it if it wasn't explicitly set
    if attributes.get("frozen", ...) is None:
        attributes.pop("frozen")
    return attributes


@dataclasses.dataclass(slots=True)
class _ModelBundle:
    enums: dict[type[Enum], "_EnumWrapper"]
    schemas: dict[type[BaseModel], "_PydanticModelWrapper"]


@dataclasses.dataclass(slots=True, kw_only=True)
class _RuntimeSchemaGenContext:
    version_bundle: "VersionBundle"
    current_version: "Version | HeadVersion"
    models: _ModelBundle
    latest_version: "Version" = dataclasses.field(init=False)

    def __post_init__(self):
        self.latest_version = max(self.version_bundle.versions, key=lambda v: v.value)


def migrate_response_body(
    versions: "VersionBundle",
    latest_response_model: type[pydantic.BaseModel],
    *,
    latest_body: Any,
    version: VersionDate | str,
) -> Any:
    """Convert the data to a specific version

    Apply all version changes from latest until the passed version in reverse order
    and wrap the result in the correct version of latest_response_model
    """
    if isinstance(version, str):
        version = date.fromisoformat(version)
    response = ResponseInfo(Response(status_code=200), body=latest_body)
    migrated_response = versions._migrate_response(
        response,
        current_version=version,
        head_response_model=latest_response_model,
        path="\0\0\0",
        method="GET",
    )

    version = versions._get_closest_lesser_version(version)

    versioned_response_model: type[pydantic.BaseModel] = generate_versioned_models(versions)[str(version)][
        latest_response_model
    ]
    return versioned_response_model.model_validate(migrated_response.body)


def _unwrap_model(model: type[_T_ANY_MODEL]) -> type[_T_ANY_MODEL]:
    while hasattr(model, "__cadwyn_original_model__"):
        model = model.__cadwyn_original_model__  # pyright: ignore[reportAttributeAccessIssue]
    return model


@dataclasses.dataclass(slots=True, kw_only=True)
class _ValidatorWrapper:
    kwargs: dict[str, Any]
    func: Callable
    decorator: Callable
    is_deleted: bool = False


@dataclasses.dataclass(slots=True, kw_only=True)
class _PerFieldValidatorWrapper(_ValidatorWrapper):
    fields: list[str]


def _wrap_validator(func: Callable, is_pydantic_v1_style_validator: Any, decorator_info: _decorators.DecoratorInfo):
    # This is only for pydantic v1 style validators
    func = fully_unwrap_decorator(func, is_pydantic_v1_style_validator)
    if inspect.ismethod(func):
        func = func.__func__
    kwargs = dataclasses.asdict(decorator_info)
    decorator_fields = kwargs.pop("fields", None)
    actual_decorator = PYDANTIC_DECORATOR_TYPE_TO_DECORATOR_MAP[type(decorator_info)]
    if is_pydantic_v1_style_validator:
        # There's an inconsistency in their interfaces so we gotta resort to this
        mode = kwargs.pop("mode", "after")
        kwargs["pre"] = mode != "after"
        if (
            isinstance(decorator_info, RootValidatorDecoratorInfo) and decorator_info.mode == "after"
        ):  # pragma: no cover # TODO
            kwargs["skip_on_failure"] = True
    if decorator_fields is not None:
        return _PerFieldValidatorWrapper(
            func=func, fields=list(decorator_fields), decorator=actual_decorator, kwargs=kwargs
        )
    else:
        return _ValidatorWrapper(func=func, decorator=actual_decorator, kwargs=kwargs)


def _is_dunder(attr_name: str):
    return attr_name.startswith("__") and attr_name.endswith("__")


def _wrap_pydantic_model(model: type[_T_PYDANTIC_MODEL]) -> "_PydanticModelWrapper[_T_PYDANTIC_MODEL]":
    decorators = _get_model_decorators(model)
    validators = {}
    for decorator_wrapper in decorators:
        if decorator_wrapper.cls_var_name not in model.__dict__:
            continue

        wrapped_validator = _wrap_validator(decorator_wrapper.func, decorator_wrapper.shim, decorator_wrapper.info)
        validators[decorator_wrapper.cls_var_name] = wrapped_validator
    fields = {
        field_name: PydanticFieldWrapper(model.model_fields[field_name], model.__annotations__[field_name], field_name)
        for field_name in model.__annotations__
    }

    main_attributes = fields | validators
    other_attributes = {
        attr_name: attr_val
        for attr_name, attr_val in model.__dict__.items()
        if attr_name not in main_attributes
        and not (_is_dunder(attr_name) or attr_name in {"_abc_impl", "model_fields", "model_computed_fields"})
    }
    other_attributes |= {
        "model_config": model.model_config,
        "__module__": model.__module__,
        "__qualname__": model.__qualname__,
    }
    return _PydanticModelWrapper(
        model,
        name=model.__name__,
        doc=model.__doc__,
        fields=fields,
        other_attributes=other_attributes,
        validators=validators,
        annotations=model.__annotations__.copy(),
    )


@final
@dataclasses.dataclass(slots=True)
class _PydanticModelWrapper(Generic[_T_PYDANTIC_MODEL]):
    cls: type[_T_PYDANTIC_MODEL] = dataclasses.field(repr=False)
    name: str
    doc: str | None = dataclasses.field(repr=False)
    fields: Annotated[
        dict["_FieldName", PydanticFieldWrapper],
        Doc(
            "Fields that belong to this model, not to its parents. "
            "I.e. The ones that were either defined or overridden "
        ),
    ] = dataclasses.field(repr=False)
    validators: dict[str, _PerFieldValidatorWrapper | _ValidatorWrapper] = dataclasses.field(repr=False)
    other_attributes: dict[str, Any] = dataclasses.field(repr=False)
    annotations: dict[str, Any] = dataclasses.field(repr=False)
    _parents: list[Self] | None = dataclasses.field(init=False, default=None, repr=False)

    def __post_init__(self):
        # This isn't actually supposed to run, it's just a precaution
        while hasattr(self.cls, "__cadwyn_original_model__"):  # pragma: no cover
            self.cls = self.cls.__cadwyn_original_model__  # pyright: ignore[reportAttributeAccessIssue]

        for k, annotation in self.annotations.items():
            if get_origin(annotation) == Annotated:
                sub_annotations = get_args(annotation)
                # Annotated cannot be copied and is cached based on "==" and "hash", while annotated_types.Interval are
                # frozen and so are consistently hashed
                self.annotations[k] = _AnnotatedAlias(
                    copy.deepcopy(sub_annotations[0]), tuple(copy.deepcopy(sub_ann) for sub_ann in sub_annotations[1:])
                )

    def __deepcopy__(self, memo: dict[int, Any]):
        result = _PydanticModelWrapper(
            self.cls,
            name=self.name,
            doc=self.doc,
            fields=copy.deepcopy(self.fields),
            validators=copy.deepcopy(self.validators),
            other_attributes=copy.deepcopy(self.other_attributes),
            annotations=copy.deepcopy(self.annotations),
        )
        memo[id(self)] = result
        return result

    def __hash__(self) -> int:
        return hash(id(self))

    def _get_parents(self, schemas: "dict[type, Self]"):
        if self._parents is not None:
            return self._parents
        parents = []
        for base in self.cls.mro()[1:]:
            if base in schemas:
                parents.append(schemas[base])
            elif lenient_issubclass(base, BaseModel):
                parents.append(_wrap_pydantic_model(base))
        self._parents = parents
        return parents

    def _get_defined_fields_through_mro(self, schemas: "dict[type, Self]") -> dict[str, PydanticFieldWrapper]:
        fields = {}

        for parent in reversed(self._get_parents(schemas)):
            fields |= parent.fields

        return fields | self.fields

    def _get_defined_annotations_through_mro(self, schemas: "dict[type, Self]") -> dict[str, Any]:
        annotations = {}

        for parent in reversed(self._get_parents(schemas)):
            annotations |= parent.annotations

        return annotations | self.annotations

    def generate_model_copy(self, generator: "SchemaGenerator") -> type[_T_PYDANTIC_MODEL]:
        per_field_validators = {
            name: validator.decorator(*validator.fields, **validator.kwargs)(validator.func)
            for name, validator in self.validators.items()
            if not validator.is_deleted and type(validator) == _PerFieldValidatorWrapper  # noqa: E721
        }
        root_validators = {
            name: validator.decorator(**validator.kwargs)(validator.func)
            for name, validator in self.validators.items()
            if not validator.is_deleted and type(validator) == _ValidatorWrapper  # noqa: E721
        }
        fields = {name: field.generate_field_copy(generator) for name, field in self.fields.items()}
        model_copy = type(self.cls)(
            self.name,
            tuple(generator[cast(type[BaseModel], base)] for base in self.cls.__bases__),
            self.other_attributes
            | per_field_validators
            | root_validators
            | fields
            | {
                "__annotations__": generator.annotation_transformer.change_version_of_annotation(self.annotations),
                "__doc__": self.doc,
                "__qualname__": self.cls.__qualname__.removesuffix(self.cls.__name__) + self.name,
            },
        )

        model_copy.__cadwyn_original_model__ = self.cls
        return model_copy


def is_regular_function(call: Callable):
    return isinstance(call, types.FunctionType | types.MethodType)


class _CallableWrapper:
    # __eq__ and __hash__ are needed to make sure that dependency overrides work correctly.
    # They are based on putting dependencies (functions) as keys for the dictionary so if we want to be able to
    # override the wrapper, we need to make sure that it is equivalent to the original in __hash__ and __eq__

    def __init__(self, original_callable: Callable) -> None:
        super().__init__()
        self._original_callable = original_callable
        if not is_regular_function(original_callable):
            original_callable = original_callable.__call__
        functools.update_wrapper(self, original_callable)

    @property
    def __globals__(self):
        # FastAPI uses __globals__ to resolve forward references in type hints
        # It's supposed to be an attribute on the function but we use it as property to prevent python
        # from trying to pickle globals when we deepcopy this wrapper
        return self._original_callable.__globals__

    def __call__(self, *args: Any, **kwargs: Any):
        return self._original_callable(*args, **kwargs)

    def __hash__(self):
        return hash(self._original_callable)

    def __eq__(self, value: object) -> bool:
        return self._original_callable == value  # pyright: ignore[reportUnnecessaryComparison]


class _AsyncCallableWrapper(_CallableWrapper):
    async def __call__(self, *args: Any, **kwargs: Any):
        return await self._original_callable(*args, **kwargs)


@final
class _AnnotationTransformer:
    def __init__(self, generator: "SchemaGenerator") -> None:
        # This cache is not here for speeding things up. It's for preventing the creation of copies of the same object
        # because such copies could produce weird behaviors at runtime, especially if you/fastapi do any comparisons.
        # It's defined here and not on the method because of this: https://youtu.be/sVjtp6tGo0g
        self.generator = generator
        # TODO: Rewrite this to memoize
        self.change_versions_of_a_non_container_annotation = functools.cache(
            self._change_version_of_a_non_container_annotation
        )

    def change_version_of_annotation(self, annotation: Any) -> Any:
        """Recursively go through all annotations and change them to annotations corresponding to the version passed.

        So if we had a annotation "UserResponse" from "head" version, and we passed version of "2022-11-16", it would
        replace "UserResponse" with the the same class but from the "2022-11-16" version.

        """
        if isinstance(annotation, dict):
            return {
                self.change_version_of_annotation(key): self.change_version_of_annotation(value)
                for key, value in annotation.items()
            }

        elif isinstance(annotation, list | tuple):
            return type(annotation)(self.change_version_of_annotation(v) for v in annotation)
        else:
            return self.change_versions_of_a_non_container_annotation(annotation)

    def migrate_router_to_version(self, router: fastapi.routing.APIRouter):
        for route in router.routes:
            if not isinstance(route, fastapi.routing.APIRoute):
                continue
            self.migrate_route_to_version(route)

    def migrate_route_to_version(self, route: fastapi.routing.APIRoute, *, ignore_response_model: bool = False):
        if route.response_model is not None and not ignore_response_model:
            route.response_model = self.change_version_of_annotation(route.response_model)
            route.response_field = fastapi.utils.create_model_field(
                name="Response_" + route.unique_id,
                type_=route.response_model,
                mode="serialization",
            )
            route.secure_cloned_response_field = fastapi.utils.create_cloned_field(route.response_field)
        route.dependencies = self.change_version_of_annotation(route.dependencies)
        route.endpoint = self.change_version_of_annotation(route.endpoint)
        for callback in route.callbacks or []:
            if not isinstance(callback, fastapi.routing.APIRoute):
                continue
            self.migrate_route_to_version(callback, ignore_response_model=ignore_response_model)
        self._remake_endpoint_dependencies(route)

    def _change_version_of_a_non_container_annotation(self, annotation: Any) -> Any:
        if isinstance(annotation, _BaseGenericAlias | types.GenericAlias):
            return get_origin(annotation)[tuple(self.change_version_of_annotation(arg) for arg in get_args(annotation))]
        elif isinstance(annotation, fastapi.params.Security):
            return fastapi.params.Security(
                self.change_version_of_annotation(annotation.dependency),
                scopes=annotation.scopes,
                use_cache=annotation.use_cache,
            )
        elif isinstance(annotation, fastapi.params.Depends):
            return fastapi.params.Depends(
                self.change_version_of_annotation(annotation.dependency),
                use_cache=annotation.use_cache,
            )
        elif isinstance(annotation, UnionType):
            getitem = typing.Union.__getitem__  # pyright: ignore[reportAttributeAccessIssue]
            return getitem(
                tuple(self.change_version_of_annotation(a) for a in get_args(annotation)),
            )
        elif annotation is Any or isinstance(annotation, typing.NewType):
            return annotation
        elif isinstance(annotation, type):
            return self._change_version_of_type(annotation)
        elif callable(annotation):
            if type(annotation).__module__.startswith(
                ("fastapi.", "pydantic.", "pydantic_core.", "starlette.")
            ) or isinstance(annotation, fastapi.security.base.SecurityBase):
                return annotation

            # If we do not use modifier, we will get an unhashable module error
            def modifier(annotation: Any):
                return self.change_version_of_annotation(annotation)

            return self._modify_callable_annotations(
                annotation,
                modifier,
                modifier,
                annotation_modifying_wrapper_factory=self._copy_function_through_class_based_wrapper,
            )
        else:
            return annotation

    def _change_version_of_type(self, annotation: type):
        if lenient_issubclass(annotation, BaseModel | Enum):
            return self.generator[annotation]
        else:
            return annotation

    @classmethod
    def _remake_endpoint_dependencies(cls, route: fastapi.routing.APIRoute):
        # Unlike get_dependant, APIRoute is the public API of FastAPI and it's (almost) guaranteed to be stable.

        route_copy = fastapi.routing.APIRoute(route.path, route.endpoint, dependencies=route.dependencies)
        route.dependant = route_copy.dependant
        route.body_field = route_copy.body_field
        _add_request_and_response_params(route)

    @classmethod
    def _modify_callable_annotations(  # pragma: no branch # because of lambdas
        cls,
        call: _Call,
        modify_annotations: Callable[[dict[str, Any]], dict[str, Any]] = lambda a: a,
        modify_defaults: Callable[[tuple[Any, ...]], tuple[Any, ...]] = lambda a: a,
        *,
        annotation_modifying_wrapper_factory: Callable[[_Call], _Call],
    ) -> _Call:
        annotation_modifying_wrapper = annotation_modifying_wrapper_factory(call)
        old_params = inspect.signature(call).parameters
        callable_annotations = annotation_modifying_wrapper.__annotations__
        callable_annotations = {
            k: v if type(v) is not str else _try_eval_type(v, call.__globals__) for k, v in callable_annotations.items()
        }
        annotation_modifying_wrapper.__annotations__ = modify_annotations(callable_annotations)
        annotation_modifying_wrapper.__defaults__ = modify_defaults(
            tuple(p.default for p in old_params.values() if p.default is not inspect.Signature.empty),
        )
        annotation_modifying_wrapper.__signature__ = cls._generate_signature(
            annotation_modifying_wrapper,
            old_params,
        )

        return annotation_modifying_wrapper

    @staticmethod
    def _generate_signature(
        new_callable: Callable,
        old_params: types.MappingProxyType[str, inspect.Parameter],
    ):
        parameters = []
        default_counter = 0
        for param in old_params.values():
            if param.default is not inspect.Signature.empty:
                assert new_callable.__defaults__ is not None, (  # noqa: S101
                    "Defaults cannot be None here. If it is, you have found a bug in Cadwyn. "
                    "Please, report it in our issue tracker."
                )
                default = new_callable.__defaults__[default_counter]
                default_counter += 1
            else:
                default = inspect.Signature.empty
            parameters.append(
                inspect.Parameter(
                    param.name,
                    param.kind,
                    default=default,
                    annotation=new_callable.__annotations__.get(
                        param.name,
                        inspect.Signature.empty,
                    ),
                ),
            )
        return inspect.Signature(
            parameters=parameters,
            return_annotation=new_callable.__annotations__.get(
                "return",
                inspect.Signature.empty,
            ),
        )

    @classmethod
    def _copy_function_through_class_based_wrapper(cls, call: Any):
        """Separate from copy_endpoint because endpoints MUST be functions in FastAPI, they cannot be cls instances"""
        call = cls._unwrap_callable(call)
        if not is_regular_function(call):
            # This means that the callable is actually an instance of a regular class
            actual_call = call.__call__
        else:
            actual_call = call
        if inspect.iscoroutinefunction(actual_call):
            return _AsyncCallableWrapper(call)
        else:
            return _CallableWrapper(call)

    @staticmethod
    def _unwrap_callable(call: Any) -> Any:
        while hasattr(call, "_original_callable"):
            call = call._original_callable

        return call


def _add_request_and_response_params(route: APIRoute):
    if not route.dependant.request_param_name:
        route.dependant.request_param_name = _CADWYN_REQUEST_PARAM_NAME
    if not route.dependant.response_param_name:
        route.dependant.response_param_name = _CADWYN_RESPONSE_PARAM_NAME


@final
class SchemaGenerator:
    __slots__ = "annotation_transformer", "concrete_models", "model_bundle"

    def __init__(self, model_bundle: _ModelBundle) -> None:
        self.annotation_transformer = _AnnotationTransformer(self)
        self.model_bundle = model_bundle
        self.concrete_models = {}
        self.concrete_models = {
            k: wrapper.generate_model_copy(self)
            for k, wrapper in (self.model_bundle.schemas | self.model_bundle.enums).items()
        }

    def __getitem__(self, model: type[_T_ANY_MODEL], /) -> type[_T_ANY_MODEL]:
        if (
            not isinstance(model, type)
            or not lenient_issubclass(model, BaseModel | Enum)
            or model in (BaseModel, RootModel)
        ):
            return model
        model = _unwrap_model(model)

        if model in self.concrete_models:
            return self.concrete_models[model]
        else:
            wrapper = self._get_wrapper_for_model(model)

        wrapper = self._get_wrapper_for_model(model)
        model_copy = wrapper.generate_model_copy(self)
        self.concrete_models[model] = model_copy
        return cast(type[_T_ANY_MODEL], model_copy)

    @overload
    def _get_wrapper_for_model(self, model: type[BaseModel]) -> "_PydanticModelWrapper[BaseModel]": ...
    @overload
    def _get_wrapper_for_model(self, model: type[Enum]) -> "_EnumWrapper[Enum]": ...

    def _get_wrapper_for_model(
        self, model: type[BaseModel | Enum]
    ) -> "_PydanticModelWrapper[BaseModel] | _EnumWrapper[Enum]":
        model = _unwrap_model(model)

        if model in self.model_bundle.schemas:
            return self.model_bundle.schemas[model]
        elif model in self.model_bundle.enums:
            return self.model_bundle.enums[model]

        if lenient_issubclass(model, BaseModel):
            wrapper = _wrap_pydantic_model(model)
            self.model_bundle.schemas[model] = wrapper
        elif lenient_issubclass(model, Enum):
            wrapper = _EnumWrapper(model)
            self.model_bundle.enums[model] = wrapper
        else:
            assert_never(model)
        return wrapper


@cache
def generate_versioned_models(versions: "VersionBundle") -> "dict[str, SchemaGenerator]":
    models = _create_model_bundle(versions)

    version_to_context_map = {}
    context = _RuntimeSchemaGenContext(current_version=versions.head_version, models=models, version_bundle=versions)
    _migrate_classes(context)

    for version in versions.versions:
        context = _RuntimeSchemaGenContext(current_version=version, models=models, version_bundle=versions)
        version_to_context_map[str(version.value)] = SchemaGenerator(copy.deepcopy(models))
        # note that the last migration will not contain any version changes so we don't need to save the results
        _migrate_classes(context)

    return version_to_context_map


def _create_model_bundle(versions: "VersionBundle"):
    return _ModelBundle(
        enums={enum: _EnumWrapper(enum) for enum in versions.versioned_enums.values()},
        schemas={schema: _wrap_pydantic_model(schema) for schema in versions.versioned_schemas.values()},
    )


def _migrate_classes(context: _RuntimeSchemaGenContext) -> None:
    for version_change in context.current_version.changes:
        _apply_alter_schema_instructions(
            context.models.schemas,
            version_change.alter_schema_instructions,
            version_change.__name__,
        )
        _apply_alter_enum_instructions(
            context.models.enums,
            version_change.alter_enum_instructions,
            version_change.__name__,
        )


def _apply_alter_schema_instructions(
    modified_schemas: dict[type, _PydanticModelWrapper],
    alter_schema_instructions: Sequence[AlterSchemaSubInstruction | SchemaHadInstruction],
    version_change_name: str,
) -> None:
    for alter_schema_instruction in alter_schema_instructions:
        schema_info = modified_schemas[alter_schema_instruction.schema]
        if isinstance(alter_schema_instruction, FieldExistedAsInstruction):
            _add_field_to_model(schema_info, modified_schemas, alter_schema_instruction, version_change_name)
        elif isinstance(alter_schema_instruction, FieldHadInstruction | FieldDidntHaveInstruction):
            _change_field_in_model(
                schema_info,
                modified_schemas,
                alter_schema_instruction,
                version_change_name,
            )
        elif isinstance(alter_schema_instruction, FieldDidntExistInstruction):
            _delete_field_from_model(schema_info, alter_schema_instruction.name, version_change_name)
        elif isinstance(alter_schema_instruction, ValidatorExistedInstruction):
            validator_name = alter_schema_instruction.validator.__name__
            raw_validator = cast(
                pydantic._internal._decorators.PydanticDescriptorProxy, alter_schema_instruction.validator
            )
            schema_info.validators[validator_name] = _wrap_validator(
                raw_validator.wrapped,
                is_pydantic_v1_style_validator=raw_validator.shim,
                decorator_info=raw_validator.decorator_info,
            )
        elif isinstance(alter_schema_instruction, ValidatorDidntExistInstruction):
            if alter_schema_instruction.name not in schema_info.validators:
                raise InvalidGenerationInstructionError(
                    f'You tried to delete a validator "{alter_schema_instruction.name}" from "{schema_info.name}" '
                    f'in "{version_change_name}" but it doesn\'t have such a validator.',
                )
            if schema_info.validators[alter_schema_instruction.name].is_deleted:
                raise InvalidGenerationInstructionError(
                    f'You tried to delete a validator "{alter_schema_instruction.name}" from "{schema_info.name}" '
                    f'in "{version_change_name}" but it is already deleted.',
                )
            schema_info.validators[alter_schema_instruction.name].is_deleted = True
        elif isinstance(alter_schema_instruction, SchemaHadInstruction):
            _change_model(schema_info, alter_schema_instruction, version_change_name)
        else:
            assert_never(alter_schema_instruction)


def _apply_alter_enum_instructions(
    enums: "dict[type, _EnumWrapper]",
    alter_enum_instructions: Sequence[AlterEnumSubInstruction],
    version_change_name: str,
):
    for alter_enum_instruction in alter_enum_instructions:
        enum = enums[alter_enum_instruction.enum]
        if isinstance(alter_enum_instruction, EnumDidntHaveMembersInstruction):
            for member in alter_enum_instruction.members:
                if member not in enum.members:
                    raise InvalidGenerationInstructionError(
                        f'You tried to delete a member "{member}" from "{enum.cls.__name__}" '
                        f'in "{version_change_name}" but it doesn\'t have such a member.',
                    )
                enum.members.pop(member)
        elif isinstance(alter_enum_instruction, EnumHadMembersInstruction):
            for member, member_value in alter_enum_instruction.members.items():
                if member in enum.members and enum.members[member] == member_value:
                    raise InvalidGenerationInstructionError(
                        f'You tried to add a member "{member}" to "{enum.cls.__name__}" '
                        f'in "{version_change_name}" but there is already a member with that name and value.',
                    )
                enum.members[member] = member_value
        else:
            assert_never(alter_enum_instruction)


def _change_model(
    model: _PydanticModelWrapper,
    alter_schema_instruction: SchemaHadInstruction,
    version_change_name: str,
):
    if alter_schema_instruction.name == model.name:
        raise InvalidGenerationInstructionError(
            f'You tried to change the name of "{model.name}" in "{version_change_name}" '
            "but it already has the name you tried to assign.",
        )

    model.name = alter_schema_instruction.name


def _add_field_to_model(
    model: _PydanticModelWrapper,
    schemas: "dict[type, _PydanticModelWrapper]",
    alter_schema_instruction: FieldExistedAsInstruction,
    version_change_name: str,
):
    defined_fields = model._get_defined_fields_through_mro(schemas)
    if alter_schema_instruction.name in defined_fields:
        raise InvalidGenerationInstructionError(
            f'You tried to add a field "{alter_schema_instruction.name}" to "{model.name}" '
            f'in "{version_change_name}" but there is already a field with that name.',
        )

    field = PydanticFieldWrapper(
        alter_schema_instruction.field, alter_schema_instruction.field.annotation, alter_schema_instruction.name
    )
    model.fields[alter_schema_instruction.name] = field
    model.annotations[alter_schema_instruction.name] = alter_schema_instruction.field.annotation


def _change_field_in_model(
    model: _PydanticModelWrapper,
    schemas: "dict[type, _PydanticModelWrapper]",
    alter_schema_instruction: FieldHadInstruction | FieldDidntHaveInstruction,
    version_change_name: str,
):
    defined_annotations = model._get_defined_annotations_through_mro(schemas)
    defined_fields = model._get_defined_fields_through_mro(schemas)
    if alter_schema_instruction.name not in defined_fields:
        raise InvalidGenerationInstructionError(
            f'You tried to change the field "{alter_schema_instruction.name}" from '
            f'"{model.name}" in "{version_change_name}" but it doesn\'t have such a field.',
        )

    field = defined_fields[alter_schema_instruction.name]
    model.fields[alter_schema_instruction.name] = field
    model.annotations[alter_schema_instruction.name] = defined_annotations[alter_schema_instruction.name]

    if isinstance(alter_schema_instruction, FieldHadInstruction):
        # TODO: This naming sucks
        _change_field(
            model,
            alter_schema_instruction,
            version_change_name,
            defined_annotations,
            field,
            model.annotations[alter_schema_instruction.name],
        )
    else:
        _delete_field_attributes(
            model,
            alter_schema_instruction,
            version_change_name,
            field,
            model.annotations[alter_schema_instruction.name],
        )


def _change_field(
    model: _PydanticModelWrapper,
    alter_schema_instruction: FieldHadInstruction,
    version_change_name: str,
    defined_annotations: dict[str, Any],
    field: PydanticFieldWrapper,
    annotation: Any | None,
):
    if alter_schema_instruction.type is not Sentinel:
        if field.annotation == alter_schema_instruction.type:
            raise InvalidGenerationInstructionError(
                f'You tried to change the type of field "{alter_schema_instruction.name}" to '
                f'"{alter_schema_instruction.type}" from "{model.name}" in "{version_change_name}" '
                f'but it already has type "{field.annotation}"',
            )
        field.annotation = alter_schema_instruction.type
        model.annotations[alter_schema_instruction.name] = alter_schema_instruction.type

    if alter_schema_instruction.new_name is not Sentinel:
        if alter_schema_instruction.new_name == alter_schema_instruction.name:
            raise InvalidGenerationInstructionError(
                f'You tried to change the name of field "{alter_schema_instruction.name}" '
                f'from "{model.name}" in "{version_change_name}" '
                "but it already has that name.",
            )
        model.fields[alter_schema_instruction.new_name] = model.fields.pop(alter_schema_instruction.name)
        model.annotations[alter_schema_instruction.new_name] = model.annotations.pop(
            alter_schema_instruction.name,
            defined_annotations[alter_schema_instruction.name],
        )

    for attr_name in alter_schema_instruction.field_changes.__dataclass_fields__:
        attr_value = getattr(alter_schema_instruction.field_changes, attr_name)
        if attr_value is not Sentinel:
            if field.passed_field_attributes.get(attr_name, Sentinel) == attr_value:
                raise InvalidGenerationInstructionError(
                    f'You tried to change the attribute "{attr_name}" of field '
                    f'"{alter_schema_instruction.name}" '
                    f'from "{model.name}" to {attr_value!r} in "{version_change_name}" '
                    "but it already has that value.",
                )
            field.update_attribute(name=attr_name, value=attr_value)


def _delete_field_attributes(
    model: _PydanticModelWrapper,
    alter_schema_instruction: FieldDidntHaveInstruction,
    version_change_name: str,
    field: PydanticFieldWrapper,
    annotation: Any,
) -> None:
    for attr_name in alter_schema_instruction.attributes:
        if attr_name in field.passed_field_attributes:
            field.delete_attribute(name=attr_name)
        elif get_origin(annotation) == Annotated and any(  # pragma: no branch
            hasattr(sub_ann, attr_name) for sub_ann in get_args(annotation)
        ):
            for sub_ann in get_args(annotation):
                if hasattr(sub_ann, attr_name):
                    object.__setattr__(sub_ann, attr_name, None)
        else:
            raise InvalidGenerationInstructionError(
                f'You tried to delete the attribute "{attr_name}" of field "{alter_schema_instruction.name}" '
                f'from "{model.name}" in "{version_change_name}" '
                "but it already doesn't have that attribute.",
            )


def _delete_field_from_model(model: _PydanticModelWrapper, field_name: str, version_change_name: str):
    if field_name not in model.fields:
        raise InvalidGenerationInstructionError(
            f'You tried to delete a field "{field_name}" from "{model.name}" '
            f'in "{version_change_name}" but it doesn\'t have such a field.',
        )
    model.fields.pop(field_name)
    model.annotations.pop(field_name)
    for validator_name, validator in model.validators.copy().items():
        if isinstance(validator, _PerFieldValidatorWrapper) and field_name in validator.fields:
            validator.fields.remove(field_name)
            # TODO: This behavior doesn't feel natural
            if not validator.fields:
                model.validators[validator_name].is_deleted = True


class _DummyEnum(Enum):
    pass


@final
class _EnumWrapper(Generic[_T_ENUM]):
    __slots__ = "cls", "members", "name"

    def __init__(self, cls: type[_T_ENUM]):
        self.cls = _unwrap_model(cls)
        self.name = cls.__name__
        self.members = {member.name: member.value for member in cls}

    def __deepcopy__(self, memo: Any):
        result = _EnumWrapper(self.cls)
        result.members = self.members.copy()
        memo[id(self)] = result
        return result

    def generate_model_copy(self, generator: "SchemaGenerator") -> type[_T_ENUM]:
        enum_dict = Enum.__prepare__(self.name, self.cls.__bases__)

        raw_member_map = {k: v.value if isinstance(v, Enum) else v for k, v in self.members.items()}
        initialization_namespace = self._get_initialization_namespace_for_enum(self.cls) | raw_member_map
        for attr_name, attr in initialization_namespace.items():
            enum_dict[attr_name] = attr
        model_copy = cast(type[_T_ENUM], type(self.name, self.cls.__bases__, enum_dict))
        model_copy.__cadwyn_original_model__ = self.cls  # pyright: ignore[reportAttributeAccessIssue]
        return model_copy

    @staticmethod
    def _get_initialization_namespace_for_enum(enum_cls: type[Enum]):
        mro_without_the_class_itself = enum_cls.mro()[1:]

        mro_dict = {}
        for cls in reversed(mro_without_the_class_itself):
            mro_dict.update(cls.__dict__)

        return {
            k: v
            for k, v in enum_cls.__dict__.items()
            if k not in enum_cls._member_names_
            and k not in _DummyEnum.__dict__
            and (k not in mro_dict or mro_dict[k] is not v)
        }


def _try_eval_type(value: Any, globals: dict[str, Any]) -> Any:
    new_value, success = pydantic_try_eval_type(value, globals)
    if success:
        return new_value
    else:
        return value
