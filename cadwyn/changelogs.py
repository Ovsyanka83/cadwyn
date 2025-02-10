from __future__ import annotations

import copy
import datetime
import sys
from enum import auto
from logging import getLogger
from typing import Any, Literal, TypeVar, Union, cast, get_args

from fastapi._compat import (
    GenerateJsonSchema,
    ModelField,
    get_compat_model_name_map,
    get_definitions,
)
from fastapi.openapi.constants import REF_TEMPLATE
from fastapi.openapi.utils import (
    get_fields_from_routes,
    get_openapi,
)
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field, RootModel

from cadwyn._asts import GenericAliasUnion
from cadwyn._utils import Sentinel
from cadwyn.route_generation import _get_routes
from cadwyn.routing import _RootHeaderAPIRouter
from cadwyn.schema_generation import SchemaGenerator, _change_field_in_model, generate_versioned_models
from cadwyn.structure.versions import PossibleInstructions, VersionBundle, VersionChange, VersionChangeWithSideEffects

from .structure.endpoints import (
    EndpointDidntExistInstruction,
    EndpointExistedInstruction,
    EndpointHadInstruction,
)
from .structure.enums import EnumDidntHaveMembersInstruction, EnumHadMembersInstruction
from .structure.schemas import (
    FieldDidntExistInstruction,
    FieldDidntHaveInstruction,
    FieldExistedAsInstruction,
    FieldHadInstruction,
    SchemaHadInstruction,
    ValidatorDidntExistInstruction,
    ValidatorExistedInstruction,
)

if sys.version_info >= (3, 11):  # pragma: no cover
    from enum import StrEnum
else:  # pragma: no cover
    from backports.strenum import StrEnum

_logger = getLogger(__name__)

T = TypeVar("T", bound=Union[PossibleInstructions, type[VersionChange]])


def hidden(instruction_or_version_change: T) -> T:
    if isinstance(
        instruction_or_version_change, (staticmethod, ValidatorDidntExistInstruction, ValidatorExistedInstruction)
    ):
        return instruction_or_version_change

    instruction_or_version_change.is_hidden_from_changelog = True
    return instruction_or_version_change


def _generate_changelog(versions: VersionBundle, router: _RootHeaderAPIRouter) -> CadwynChangelogResource:
    changelog = CadwynChangelogResource()
    schema_generators = generate_versioned_models(versions)
    for version, older_version in zip(versions, versions.versions[1:]):
        routes_from_newer_version = router.versioned_routers[version.value].routes
        schemas_from_older_version = get_fields_from_routes(router.versioned_routers[older_version.value].routes)
        version_changelog = CadwynVersion(value=version.value)
        generator_from_newer_version = schema_generators[version.value.isoformat()]
        generator_from_older_version = schema_generators[older_version.value.isoformat()]
        for version_change in version.changes:
            if version_change.is_hidden_from_changelog:
                continue
            version_change_changelog = CadwynVersionChange(
                description=version_change.description,
                side_effects=isinstance(version_change, VersionChangeWithSideEffects),
            )
            for instruction in [
                *version_change.alter_endpoint_instructions,
                *version_change.alter_enum_instructions,
                *version_change.alter_schema_instructions,
            ]:
                if (
                    isinstance(instruction, (ValidatorDidntExistInstruction, ValidatorExistedInstruction))
                    or instruction.is_hidden_from_changelog
                ):
                    continue
                changelog_entry = _convert_version_change_instruction_to_changelog_entry(
                    instruction,
                    version_change,
                    generator_from_newer_version,
                    generator_from_older_version,
                    schemas_from_older_version,
                    cast(list[APIRoute], routes_from_newer_version),
                )
                if changelog_entry is not None:  # pragma: no branch # This should never happen
                    version_change_changelog.instructions.append(CadwynVersionChangeInstruction(changelog_entry))
            version_changelog.changes.append(version_change_changelog)
        changelog.versions.append(version_changelog)
    return changelog


def _get_older_field_name(
    schema: type[BaseModel], new_field_name: str, generator_from_older_version: SchemaGenerator
) -> str:
    older_model_wrapper = generator_from_older_version._get_wrapper_for_model(schema)
    newer_names_mapping = {
        field.name_from_newer_version: old_name for old_name, field in older_model_wrapper.fields.items()
    }
    return newer_names_mapping[new_field_name]


def _get_affected_model_names(
    instruction: FieldExistedAsInstruction
    | FieldDidntExistInstruction
    | FieldHadInstruction
    | FieldDidntHaveInstruction,
    generator_from_newer_version: SchemaGenerator,
    schemas_from_last_version: list[ModelField],
):
    changed_model = generator_from_newer_version._get_wrapper_for_model(instruction.schema)
    annotations = [model.field_info.annotation for model in schemas_from_last_version]
    basemodel_annotations: list[type[BaseModel]] = []
    for annotation in annotations:
        basemodel_annotations.extend(_get_all_pydantic_models_from_generic(annotation))
    models = {generator_from_newer_version._get_wrapper_for_model(annotation) for annotation in basemodel_annotations}
    return [
        model.name
        for model in models
        if changed_model == model
        or (
            instruction.name not in model.fields
            and changed_model in (parents := model._get_parents(generator_from_newer_version.model_bundle.schemas))
            and all(instruction.name not in parent.fields for parent in parents[: parents.index(changed_model)])
        )
    ]


def _get_all_pydantic_models_from_generic(annotation: Any) -> list[type[BaseModel]]:
    if not isinstance(annotation, GenericAliasUnion):
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return [annotation]
        else:
            return []
    sub_annotations = get_args(annotation)
    models = []

    for sub_annotation in sub_annotations:
        models.extend(_get_all_pydantic_models_from_generic(sub_annotation))

    return models


def _get_openapi_representation_of_a_field(model: type[BaseModel], field_name: str) -> dict:
    class CadwynDummyModelForRepresentation(BaseModel):
        my_field: model

    model_name_map = get_compat_model_name_map([CadwynDummyModelForRepresentation.model_fields["my_field"]])
    schema_generator = GenerateJsonSchema(ref_template=REF_TEMPLATE)
    _, definitions = get_definitions(
        fields=[ModelField(CadwynDummyModelForRepresentation.model_fields["my_field"], "my_field")],
        schema_generator=schema_generator,
        model_name_map=model_name_map,
        separate_input_output_schemas=False,
    )

    return definitions[model.__name__]["properties"][field_name]


class ChangelogEntryType(StrEnum):
    endpoint_added = "endpoint.added"
    endpoint_removed = "endpoint.removed"
    endpoint_changed = "endpoint.changed"
    enum_members_added = "enum.members.added"
    enum_members_removed = "enum.members.removed"
    schema_changed = "schema.changed"
    schema_field_removed = "schema.field.removed"
    schema_field_added = "schema.field.added"
    schema_field_attributes_changed = "schema.field.attributes.changed"


class CadwynAttributeChangeStatus(StrEnum):
    added = auto()
    changed = auto()
    removed = auto()


class CadwynEndpointAttributeChange(BaseModel):
    name: str
    new_value: Any


class CadwynAttributeChange(BaseModel):
    name: str
    status: CadwynAttributeChangeStatus
    old_value: Any
    new_value: Any


class CadwynFieldAttributesWereChangedChangelogEntry(BaseModel):
    type: Literal[ChangelogEntryType.schema_field_attributes_changed] = (
        ChangelogEntryType.schema_field_attributes_changed
    )
    models: list[str]
    field: str
    attribute_changes: list[CadwynAttributeChange]


class CadwynModelModifiedAttributes(BaseModel):
    name: str | None


class CadwynSchemaWasChangedChangelogEntry(BaseModel):
    type: Literal[ChangelogEntryType.schema_changed] = ChangelogEntryType.schema_changed
    model: str
    modified_attributes: CadwynModelModifiedAttributes


class CadwynSchemaFieldWasAddedChangelogEntry(BaseModel):
    type: Literal[ChangelogEntryType.schema_field_added] = ChangelogEntryType.schema_field_added
    models: list[str]
    field: str
    field_info: dict[str, Any]


class CadwynSchemaFieldWasRemovedChangelogEntry(BaseModel):
    type: Literal[ChangelogEntryType.schema_field_removed] = ChangelogEntryType.schema_field_removed
    models: list[str]
    field: str


class CadwynEnumMember(BaseModel):
    name: str
    value: Any


class CadwynEnumMembersWereAddedChangelogEntry(BaseModel):
    type: Literal[ChangelogEntryType.enum_members_added] = ChangelogEntryType.enum_members_added
    enum: str
    members: list[CadwynEnumMember]


class CadwynEnumMembersWereChangedChangelogEntry(BaseModel):
    type: Literal[ChangelogEntryType.enum_members_removed] = ChangelogEntryType.enum_members_removed
    enum: str
    member_changes: list[CadwynAttributeChange]


class HTTPMethod(StrEnum):
    GET = "GET"
    PUT = "PUT"
    POST = "POST"
    DELETE = "DELETE"
    OPTIONS = "OPTIONS"
    HEAD = "HEAD"
    PATCH = "PATCH"
    TRACE = "TRACE"


class CadwynEndpointHadChangelogEntry(BaseModel):
    type: Literal[ChangelogEntryType.endpoint_changed] = ChangelogEntryType.endpoint_changed
    path: str
    methods: list[HTTPMethod]
    changes: list[CadwynEndpointAttributeChange]


class CadwynEndpointWasAddedChangelogEntry(BaseModel):
    type: Literal[ChangelogEntryType.endpoint_added] = ChangelogEntryType.endpoint_added
    path: str
    methods: list[HTTPMethod]


class CadwynEndpointWasRemovedChangelogEntry(BaseModel):
    type: Literal[ChangelogEntryType.endpoint_removed] = ChangelogEntryType.endpoint_removed
    path: str
    methods: list[HTTPMethod]


class CadwynChangelogResource(BaseModel):
    versions: list[CadwynVersion] = Field(default_factory=list)


class CadwynVersion(BaseModel):
    value: datetime.date
    changes: list[CadwynVersionChange] = Field(default_factory=list)


class CadwynVersionChange(BaseModel):
    description: str
    side_effects: bool
    instructions: list[CadwynVersionChangeInstruction] = Field(default_factory=list)


CadwynVersionChangeInstruction = RootModel[
    Union[
        CadwynEnumMembersWereAddedChangelogEntry,
        CadwynEnumMembersWereChangedChangelogEntry,
        CadwynEndpointWasAddedChangelogEntry,
        CadwynEndpointWasRemovedChangelogEntry,
        CadwynSchemaFieldWasRemovedChangelogEntry,
        CadwynSchemaFieldWasAddedChangelogEntry,
        CadwynFieldAttributesWereChangedChangelogEntry,
        CadwynEndpointHadChangelogEntry,
        CadwynSchemaWasChangedChangelogEntry,
    ]
]


def _convert_version_change_instruction_to_changelog_entry(  # noqa: C901
    instruction: PossibleInstructions,
    version_change: type[VersionChange],
    generator_from_newer_version: SchemaGenerator,
    generator_from_older_version: SchemaGenerator,
    schemas_from_older_version: list[ModelField],
    routes_from_newer_version: list[APIRoute],
):
    try:
        if isinstance(instruction, EndpointDidntExistInstruction):
            return CadwynEndpointWasAddedChangelogEntry(
                path=instruction.endpoint_path,
                methods=cast(Any, instruction.endpoint_methods),
            )
        if isinstance(instruction, EndpointExistedInstruction):
            return CadwynEndpointWasRemovedChangelogEntry(
                path=instruction.endpoint_path,
                methods=cast(Any, instruction.endpoint_methods),
            )
        if isinstance(instruction, EndpointHadInstruction):
            if instruction.attributes.include_in_schema is not Sentinel:
                return CadwynEndpointWasRemovedChangelogEntry(
                    path=instruction.endpoint_path,
                    methods=cast(Any, instruction.endpoint_methods),
                )

            renaming_map = {"operation_id": "operationId"}

            attribute_changes = []

            for attr in ["path", "methods", "summary", "description", "tags", "deprecated", "operation_id"]:
                attr_value = getattr(instruction.attributes, attr)
                if attr_value is not Sentinel:
                    attribute_changes.append(
                        CadwynEndpointAttributeChange(name=renaming_map.get(attr, attr), new_value=attr_value)
                    )
            if instruction.attributes.name is not Sentinel and instruction.attributes.summary is Sentinel:
                attribute_changes.append(
                    CadwynEndpointAttributeChange(name="summary", new_value=instruction.attributes.name)
                )

            if any(
                getattr(instruction.attributes, attr) is not Sentinel
                for attr in ["path", "methods", "summary", "description", "tags", "deprecated"]
            ):
                pass
            if any(
                attr is not Sentinel
                for attr in [
                    instruction.attributes.response_model,
                    instruction.attributes.response_class,
                    instruction.attributes.responses,
                    instruction.attributes.status_code,
                ]
            ):
                newer_routes = _get_routes(
                    routes_from_newer_version,
                    instruction.endpoint_path,
                    instruction.endpoint_methods,
                    instruction.endpoint_func_name,
                    is_deleted=False,
                )
                newer_openapi = get_openapi(title="", version="", routes=newer_routes)
                changed_responses = {
                    method: route_openapi["responses"]
                    for method, route_openapi in newer_openapi["paths"][instruction.endpoint_path].items()
                }
                attribute_changes.append(CadwynEndpointAttributeChange(name="responses", new_value=changed_responses))
            return CadwynEndpointHadChangelogEntry(
                path=instruction.endpoint_path,
                methods=cast(Any, instruction.endpoint_methods),
                changes=attribute_changes,
            )

        if isinstance(instruction, (FieldHadInstruction, FieldDidntHaveInstruction)):
            old_field_name = _get_older_field_name(instruction.schema, instruction.name, generator_from_older_version)

            if isinstance(instruction, FieldHadInstruction) and instruction.new_name is not Sentinel:
                old_field_name_from_this_instruction = instruction.new_name
                attribute_changes = [
                    CadwynAttributeChange(
                        name="name",
                        status=CadwynAttributeChangeStatus.changed,
                        old_value=old_field_name,
                        new_value=instruction.name,
                    )
                ]
            else:
                old_field_name_from_this_instruction = instruction.name
                attribute_changes = []
            newer_model_wrapper = generator_from_newer_version._get_wrapper_for_model(instruction.schema)
            newer_model_wrapper_with_migrated_field = copy.deepcopy(newer_model_wrapper)
            _change_field_in_model(
                newer_model_wrapper_with_migrated_field,
                generator_from_newer_version.model_bundle.schemas,
                alter_schema_instruction=instruction,
                version_change_name=version_change.__name__,
            )

            older_model = newer_model_wrapper_with_migrated_field.generate_model_copy(generator_from_newer_version)
            newer_model = newer_model_wrapper.generate_model_copy(generator_from_newer_version)

            newer_field_openapi = _get_openapi_representation_of_a_field(newer_model, instruction.name)
            older_field_openapi = _get_openapi_representation_of_a_field(
                older_model, old_field_name_from_this_instruction
            )

            attribute_changes += [
                CadwynAttributeChange(
                    name=key,
                    status=CadwynAttributeChangeStatus.changed
                    if key in newer_field_openapi
                    else CadwynAttributeChangeStatus.removed,
                    old_value=old_value,
                    new_value=newer_field_openapi.get(key),
                )
                for key, old_value in older_field_openapi.items()
                if old_value != newer_field_openapi.get(key)
            ]
            attribute_changes += [
                CadwynAttributeChange(
                    name=key,
                    status=CadwynAttributeChangeStatus.added,
                    old_value=None,
                    new_value=new_value,
                )
                for key, new_value in newer_field_openapi.items()
                if key not in older_field_openapi
            ]

            return CadwynFieldAttributesWereChangedChangelogEntry(
                models=_get_affected_model_names(instruction, generator_from_newer_version, schemas_from_older_version),
                field=old_field_name,
                attribute_changes=attribute_changes,
            )
        if isinstance(instruction, EnumDidntHaveMembersInstruction):
            enum = generator_from_newer_version._get_wrapper_for_model(instruction.enum)

            return CadwynEnumMembersWereAddedChangelogEntry(
                enum=enum.name,
                members=[CadwynEnumMember(name=name, value=value) for name, value in enum.members.items()],
            )
        if isinstance(instruction, EnumHadMembersInstruction):
            new_enum = generator_from_newer_version[instruction.enum]
            old_enum = generator_from_older_version[instruction.enum]

            return CadwynEnumMembersWereChangedChangelogEntry(
                enum=new_enum.__name__,
                member_changes=[
                    CadwynAttributeChange(
                        name=name,
                        old_value=old_enum.__members__.get(name),
                        new_value=new_enum.__members__.get(name),
                        status=CadwynAttributeChangeStatus.changed
                        if name in new_enum.__members__
                        else CadwynAttributeChangeStatus.removed,
                    )
                    for name in instruction.members
                ],
            )
        if isinstance(instruction, SchemaHadInstruction):
            model = generator_from_newer_version._get_wrapper_for_model(instruction.schema)

            return CadwynSchemaWasChangedChangelogEntry(
                model=instruction.name, modified_attributes=CadwynModelModifiedAttributes(name=model.name)
            )
        if isinstance(instruction, FieldExistedAsInstruction):
            affected_model_names = _get_affected_model_names(
                instruction, generator_from_newer_version, schemas_from_older_version
            )
            return CadwynSchemaFieldWasRemovedChangelogEntry(models=affected_model_names, field=instruction.name)
        if isinstance(instruction, FieldDidntExistInstruction):
            model = generator_from_newer_version[instruction.schema]
            affected_model_names = _get_affected_model_names(
                instruction, generator_from_newer_version, schemas_from_older_version
            )

            return CadwynSchemaFieldWasAddedChangelogEntry(
                models=affected_model_names,
                field=instruction.name,
                field_info=_get_openapi_representation_of_a_field(model, instruction.name),
            )
        else:  # pragma: no cover
            _logger.warning(
                "Encountered an unknown instruction. "
                "This should not have happened. "
                "Please, contact the author and show him this message and your version bundle: %s.",
                instruction,
            )
            return None
    finally:
        # Part of 3.9 convertion, to keep indent of above block the same when converting from match to if/else
        pass
