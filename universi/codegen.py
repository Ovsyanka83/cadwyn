import ast
import importlib
import inspect
import os
import shutil
import sys
import textwrap
from collections.abc import Callable, Generator, Sequence
from copy import deepcopy
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import date
from enum import Enum, auto
from pathlib import Path
from types import GenericAlias, LambdaType, ModuleType, NoneType
from typing import (
    Any,
    TypeAlias,
    _BaseGenericAlias,  # pyright: ignore[reportGeneralTypeIssues]
    get_args,
    get_origin,
)

from pydantic import BaseModel
from pydantic.fields import FieldInfo, ModelField
from typing_extensions import assert_never

from universi.structure.enums import (
    AlterEnumSubInstruction,
    EnumDidntHaveMembersInstruction,
    EnumHadMembersInstruction,
)
from universi.structure.schemas import (
    AlterSchemaInstruction,
    AlterSchemaSubInstruction,
    OldSchemaFieldDidntExist,
    OldSchemaFieldExistedWith,
    OldSchemaFieldHad,
    SchemaPropertyDefinitionInstruction,
    SchemaPropertyDidntExistInstruction,
)
from universi.structure.versions import Version, VersionBundle

from ._utils import Sentinel, UnionType, get_index_of_base_schema_dir_in_pythonpath
from .exceptions import CodeGenerationError, InvalidGenerationInstructionError

_LambdaFunctionName = (lambda: None).__name__  # pragma: no branch
_FieldName: TypeAlias = str
_PropertyName: TypeAlias = str
_empty_field_info = FieldInfo()
_dict_of_empty_field_info = {k: getattr(_empty_field_info, k) for k in FieldInfo.__slots__}
_AUTO_GENERATION_WARNING = "# THIS FILE WAS AUTO-GENERATED BY UNIVERSI. DO NOT EVER TRY TO EDIT IT BY HAND\n\n"


@dataclass(slots=True)
class ModelFieldLike:
    name: str
    original_type: Any
    annotation: Any
    field_info: FieldInfo
    import_from: str | None
    import_as: str | None


@dataclass(slots=True)
class ModelInfo:
    name: str
    fields: dict[_FieldName, tuple[type[BaseModel], ModelField | ModelFieldLike]]
    properties: dict[_PropertyName, Callable[[Any], Any]] = dataclass_field(default_factory=dict)


class ImportedModule:
    __slots__ = ("path", "name", "alias", "absolute_python_path_to_origin")

    def __init__(
        self,
        version_dir: str,
        import_pythonpath_template: str,
        package_name: str,
        absolute_python_path_template: str,
    ) -> None:
        self.path = import_pythonpath_template.format(version_dir)
        self.name = package_name.format(version_dir)
        if self.path == "":
            self.alias = self.name
        else:
            self.alias = f"{self.path.replace('.', '_')}_{self.name}"
        self.absolute_python_path_to_origin = absolute_python_path_template.format("latest")


def regenerate_dir_to_all_versions(template_module: ModuleType, versions: VersionBundle):
    schemas = {
        k: ModelInfo(v.__name__, _get_fields_for_model(v)) for k, v in deepcopy(versions.versioned_schemas).items()
    }
    enums = {k: (v, {member.name: member.value for member in v}) for k, v in deepcopy(versions.versioned_enums).items()}
    schemas_per_version: list[dict[str, ModelInfo]] = []
    for version in versions:
        schemas_per_version.append(schemas)
        schemas = deepcopy(schemas)
        _generate_versioned_directory(template_module, schemas, enums, version.value)
        _apply_migrations(version, schemas, enums)
    _generate_union_directory(template_module, versions, schemas_per_version)

    current_package = template_module.__name__
    while current_package != "":
        importlib.reload(sys.modules[current_package])
        current_package = ".".join(current_package.split(".")[:-1])


def _generate_union_directory(
    template_module: ModuleType,
    versions: VersionBundle,
    schemas_per_version: list[dict[str, ModelInfo]],
):
    template_dir = _get_package_path_from_module(template_module)
    union_dir = template_dir.with_name("unions")
    index_of_latest_schema_dir_in_pythonpath = get_index_of_base_schema_dir_in_pythonpath(
        template_module,
        union_dir,
    )
    for _, original_module, parallel_file in _generate_parallel_directory(
        template_module,
        union_dir,
    ):
        new_module_text = _get_unionized_version_of_module(
            original_module,
            versions,
            index_of_latest_schema_dir_in_pythonpath,
            schemas_per_version,
        )
        parallel_file.write_text(_AUTO_GENERATION_WARNING + new_module_text)


def _get_unionized_version_of_module(
    original_module: ModuleType,
    versions: VersionBundle,
    index_of_latest_schema_dir_in_pythonpath: int,
    schemas_per_version: list[dict[str, ModelInfo]],
):
    original_module_parts = original_module.__name__.split(".")
    original_module_parts[index_of_latest_schema_dir_in_pythonpath] = "{}"
    how_far_up_is_base_schema_dir_from_current_module = (
        len(original_module_parts) - index_of_latest_schema_dir_in_pythonpath
    )
    if original_module_parts[-1] == "__init__":
        original_module_parts.pop(-1)
    imported_modules = _prepare_unionized_imports(
        versions,
        index_of_latest_schema_dir_in_pythonpath,
        original_module_parts,
    )
    imports = [
        ast.ImportFrom(module="pydantic", names=[ast.alias(name="Field")], level=0),
        ast.Import(names=[ast.alias(name="typing")], level=0),
        *[
            ast.ImportFrom(
                level=how_far_up_is_base_schema_dir_from_current_module,
                module=module.path,
                names=[
                    ast.Name(module.name)
                    if module.alias == module.name
                    else ast.alias(
                        name=module.name,
                        asname=module.alias,
                    ),
                ],
            )
            for module in imported_modules
        ],
    ]
    parsed_file = _parse_python_module(original_module)
    body = ast.Module(
        imports + [_get_union_of_node(node, imported_modules, schemas_per_version) for node in parsed_file.body],
        [],
    )

    return ast.unparse(body)


def _get_union_of_node(
    node: ast.stmt,
    imported_modules: list[ImportedModule],
    schemas_per_version: list[dict[str, ModelInfo]],
):
    if isinstance(node, ast.ClassDef):
        # We add [schemas_per_version[0]] because imported_modules include "latest" and schemas_per_version do not

        return ast.Name(
            f"\n{node.name}: typing.TypeAlias = "
            f"{_generate_union_of_subnode(node, imported_modules, schemas_per_version)}",
        )
    else:
        return node


def _generate_union_of_subnode(
    node: ast.ClassDef,
    imported_modules: list[ImportedModule],
    schemas_per_version: list[dict[str, ModelInfo]],
):
    return " | ".join(
        f"{module.alias}.{_get_mod_name(node, module, schemas)}"
        for module, schemas in zip(imported_modules, [schemas_per_version[0], *schemas_per_version])
    )


def _get_mod_name(node: ast.ClassDef, module: ImportedModule, schemas: dict[str, ModelInfo]):
    node_python_path = f"{module.absolute_python_path_to_origin}.{node.name}"
    if node_python_path in schemas:
        return schemas[node_python_path].name
    else:
        return node.name


def _prepare_unionized_imports(
    versions: VersionBundle,
    index_of_latest_schema_dir_in_pythonpath: int,
    original_module_parts: list[str],
) -> list[ImportedModule]:
    # package.latest                     -> from .. import latest
    # package.latest.module              -> from ...latest import module
    # package.latest.subpackage          -> from ...latest import subpackage
    # package.latest.subpackage.module   -> from ....subpackage import module

    package_name = original_module_parts[-1]
    package_path = original_module_parts[index_of_latest_schema_dir_in_pythonpath:-1]
    import_pythonpath_template = ".".join(package_path)
    version_dirs = ["latest"] + [_get_version_dir_name(version.value) for version in versions]
    absolute_python_path_template = ".".join(original_module_parts)
    return [
        ImportedModule(
            version_dir,
            import_pythonpath_template,
            package_name,
            absolute_python_path_template=absolute_python_path_template,
        )
        for version_dir in version_dirs
    ]


def _apply_migrations(
    version: Version,
    schemas: dict[
        str,
        ModelInfo,
    ],
    enums: dict[str, tuple[type[Enum], dict[str, Any]]],
):
    for version_change in version.version_changes:
        _apply_alter_schema_instructions(
            schemas,
            version_change.alter_schema_instructions,
            version_change.__name__,
        )
        _apply_alter_enum_instructions(
            enums,
            version_change.alter_enum_instructions,
            version_change.__name__,
        )


def _apply_alter_schema_instructions(  # noqa: C901
    schema_infos: dict[str, ModelInfo],
    alter_schema_instructions: Sequence[AlterSchemaSubInstruction | AlterSchemaInstruction],
    version_change_name: str,
):
    for alter_schema_instruction in alter_schema_instructions:
        schema = alter_schema_instruction.schema
        schema_path = f"{schema.__module__}.{schema.__name__}"
        mutable_schema_info = schema_infos[schema_path]
        field_name_to_field_model = mutable_schema_info.fields
        if isinstance(alter_schema_instruction, OldSchemaFieldDidntExist):
            if alter_schema_instruction.field_name not in field_name_to_field_model:
                raise InvalidGenerationInstructionError(
                    f'You tried to delete a field "{alter_schema_instruction.field_name}" from "{schema.__name__}" '
                    f'in "{version_change_name}" but it doesn\'t have such a field.',
                )
            field_name_to_field_model.pop(alter_schema_instruction.field_name)

        elif isinstance(alter_schema_instruction, OldSchemaFieldHad):
            if alter_schema_instruction.field_name not in field_name_to_field_model:
                raise InvalidGenerationInstructionError(
                    f'You tried to change the type of field "{alter_schema_instruction.field_name}" from'
                    f' "{schema.__name__}" in "{version_change_name}" but it doesn\'t have such a field.',
                )
            model_field = field_name_to_field_model[alter_schema_instruction.field_name][1]
            if alter_schema_instruction.type is not Sentinel:
                if model_field.annotation == alter_schema_instruction.type:
                    raise InvalidGenerationInstructionError(
                        f'You tried to change the type of field "{alter_schema_instruction.field_name}" to'
                        f' "{alter_schema_instruction.type}" from "{schema.__name__}" in "{version_change_name}"'
                        f' but it already has type "{model_field.annotation}"',
                    )
                model_field.annotation = alter_schema_instruction.type
            field_info = model_field.field_info

            dict_of_field_info = {k: getattr(field_info, k) for k in field_info.__slots__}
            if dict_of_field_info == _dict_of_empty_field_info:
                field_info = FieldInfo()
                model_field.field_info = field_info
            for attr_name in alter_schema_instruction.field_changes.__dataclass_fields__:
                attr_value = getattr(alter_schema_instruction.field_changes, attr_name)
                if attr_value is not Sentinel:
                    if getattr(field_info, attr_name) == attr_value:
                        raise InvalidGenerationInstructionError(
                            f'You tried to change the attribute "{attr_name}" of field '
                            f'"{alter_schema_instruction.field_name}" '
                            f'from "{schema.__name__}" to {attr_value!r} in "{version_change_name}" '
                            "but it already has that value.",
                        )
                    setattr(field_info, attr_name, attr_value)
        elif isinstance(alter_schema_instruction, OldSchemaFieldExistedWith):
            if alter_schema_instruction.field_name in field_name_to_field_model:
                raise InvalidGenerationInstructionError(
                    f'You tried to add a field "{alter_schema_instruction.field_name}" to "{schema.__name__}" '
                    f'in "{version_change_name}" but there is already a field with that name.',
                )
            if alter_schema_instruction.import_as is not None:
                annotation = alter_schema_instruction.import_as
            else:
                annotation = alter_schema_instruction.type
            field_name_to_field_model[alter_schema_instruction.field_name] = (
                schema,
                ModelFieldLike(
                    name=alter_schema_instruction.field_name,
                    original_type=alter_schema_instruction.type,
                    annotation=annotation,
                    field_info=alter_schema_instruction.field,
                    import_from=alter_schema_instruction.import_from,
                    import_as=alter_schema_instruction.import_as,
                ),
            )
        elif isinstance(alter_schema_instruction, SchemaPropertyDefinitionInstruction):
            if alter_schema_instruction.name in field_name_to_field_model:
                raise InvalidGenerationInstructionError(
                    f'You tried to define a property "{alter_schema_instruction.name}" inside "{schema.__name__}" '
                    f'in "{version_change_name}" but there is already a field with that name.',
                )
            schema_infos[schema_path].properties[alter_schema_instruction.name] = alter_schema_instruction.function
        elif isinstance(alter_schema_instruction, SchemaPropertyDidntExistInstruction):
            if alter_schema_instruction.name not in schema_infos[schema_path].properties:
                raise InvalidGenerationInstructionError(
                    f'You tried to delete a property "{alter_schema_instruction.name}" from "{schema.__name__}" '
                    f'in "{version_change_name}" but there is no such property defined in any of the migrations.',
                )
            schema_infos[schema_path].properties.pop(alter_schema_instruction.name)
        elif isinstance(alter_schema_instruction, AlterSchemaInstruction):
            if alter_schema_instruction.name == mutable_schema_info.name:
                raise InvalidGenerationInstructionError(
                    f'You tried to change the name of "{schema.__name__}" in "{version_change_name}" '
                    "but it already has the name you tried to assign.",
                )
            mutable_schema_info.name = alter_schema_instruction.name
        else:
            assert_never(alter_schema_instruction)


def _apply_alter_enum_instructions(
    enums: dict[str, tuple[type[Enum], dict[str, Any]]],
    alter_enum_instructions: Sequence[AlterEnumSubInstruction],
    version_change_name: str,
):
    for alter_enum_instruction in alter_enum_instructions:
        enum = alter_enum_instruction.enum
        enum_path = f"{enum.__module__}.{enum.__name__}"
        enum_member_to_value = enums[enum_path]
        if isinstance(alter_enum_instruction, EnumDidntHaveMembersInstruction):
            for member in alter_enum_instruction.members:
                if member not in enum_member_to_value[1]:
                    raise InvalidGenerationInstructionError(
                        f'You tried to delete a member "{member}" from "{enum.__name__}" '
                        f'in "{version_change_name}" but it doesn\'t have such a member.',
                    )
                enum_member_to_value[1].pop(member)
        elif isinstance(alter_enum_instruction, EnumHadMembersInstruction):
            for member, member_value in alter_enum_instruction.members.items():
                if member in enum_member_to_value[1] and enum_member_to_value[1][member] == member_value:
                    raise InvalidGenerationInstructionError(
                        f'You tried to add a member "{member}" to "{enum.__name__}" '
                        f'in "{version_change_name}" but there is already a member with that name and value.',
                    )
                enum_member_to_value[1][member] = member_value
        else:
            assert_never(alter_enum_instruction)


def _get_version_dir_path(template_module: ModuleType, version: date) -> Path:
    template_dir = _get_package_path_from_module(template_module)
    return template_dir.with_name(_get_version_dir_name(version))


def _get_version_dir_name(version: date):
    return "v" + version.isoformat().replace("-", "_")


def _get_package_path_from_module(template_module: ModuleType) -> Path:
    file = inspect.getsourcefile(template_module)

    # I am too lazy to reproduce this error correctly
    if file is None:  # pragma: no cover
        raise CodeGenerationError(f"Module {template_module} has no source file")
    file = Path(file)
    if not file.name == "__init__.py":
        raise CodeGenerationError(f"Module {template_module} is not a package")
    return file.parent


def _generate_versioned_directory(
    template_module: ModuleType,
    schemas: dict[str, ModelInfo],
    enums: dict[str, tuple[type[Enum], dict[str, Any]]],
    version: date,
):
    version_dir = _get_version_dir_path(template_module, version)
    for (
        _relative_path_to_file,
        original_module,
        parallel_file,
    ) in _generate_parallel_directory(
        template_module,
        version_dir,
    ):
        new_module_text = _migrate_module_to_another_version(
            original_module,
            schemas,
            enums,
        )
        parallel_file.write_text(_AUTO_GENERATION_WARNING + new_module_text)


def _generate_parallel_directory(
    template_module: ModuleType,
    parallel_dir: Path,
) -> Generator[tuple[Path, ModuleType, Path], Any, None]:
    if template_module.__file__ is None:  # pragma: no cover
        raise ValueError(
            f"You passed a {template_module=} but it doesn't have a file "
            "so it is impossible to generate its counterpart.",
        )
    dir = _get_package_path_from_module(template_module)
    parallel_dir.mkdir(exist_ok=True)
    # >>> [universi, structure, schemas]
    template_module_python_path_parts = template_module.__name__.split(".")
    # >>> [home, foo, bar, universi, structure, schemas]
    template_module_path_parts = Path(template_module.__file__).parent.parts
    # >>> [home, foo, bar] = [home, foo, bar, universi, structure, schemas][:-3]
    root_module_path = Path(
        *template_module_path_parts[: -len(template_module_python_path_parts)],
    )
    for subroot, dirnames, filenames in os.walk(dir):
        original_subroot = Path(subroot)
        parallel_subroot = parallel_dir / original_subroot.relative_to(dir)
        if "__pycache__" in dirnames:
            dirnames.remove("__pycache__")
        for dirname in dirnames:
            (parallel_subroot / dirname).mkdir(exist_ok=True)
        for filename in filenames:
            original_file = (original_subroot / filename).absolute()
            parallel_file = (parallel_subroot / filename).absolute()

            if filename.endswith(".py"):
                original_module_path = ".".join(
                    original_file.relative_to(root_module_path).with_suffix("").parts,
                )
                original_module = importlib.import_module(original_module_path)
                yield original_subroot.relative_to(dir), original_module, parallel_file
            else:
                shutil.copyfile(original_file, parallel_file)


def _get_fields_for_model(
    model: type[BaseModel],
) -> dict[_FieldName, tuple[type[BaseModel], ModelField | ModelFieldLike]]:
    actual_fields: dict[_FieldName, tuple[type[BaseModel], ModelField | ModelFieldLike]] = {}
    for cls in model.__mro__:
        if cls is BaseModel:
            return actual_fields
        if not issubclass(cls, BaseModel):
            continue
        for field_name, field in cls.__fields__.items():
            if field_name not in actual_fields and field_name in cls.__annotations__:
                actual_fields[field_name] = (cls, field)
    raise CodeGenerationError(f"Model {model} is not a subclass of BaseModel")


def _parse_python_module(module: ModuleType) -> ast.Module:
    try:
        return ast.parse(inspect.getsource(module))
    except OSError as e:
        if module.__file__ is None:  # pragma: no cover
            raise CodeGenerationError("Failed to get file path to the module") from e

        path = Path(module.__file__)
        if path.is_file() and path.read_text() == "":
            return ast.Module([])
        # Not sure how to get here so this is just a precaution
        raise CodeGenerationError(
            "Failed to get source code for module",
        ) from e  # pragma: no cover


def _migrate_module_to_another_version(
    module: ModuleType,
    modified_schemas: dict[str, ModelInfo],
    modified_enums: dict[str, tuple[type[Enum], dict[str, Any]]],
) -> str:
    transformer = _AnnotationTransformer()
    parsed_file = _parse_python_module(module)
    if module.__name__.endswith(".__init__"):
        module_name = module.__name__.removesuffix(".__init__")
    else:
        module_name = module.__name__
    all_names_in_file = _get_all_names_defined_in_module(parsed_file, module_name)

    # TODO: Does this play well with renaming?
    extra_field_imports = [
        ast.ImportFrom(
            module=field.import_from,
            names=[ast.alias(name=transformer.visit(field.original_type).strip("'"), asname=field.import_as)],
            level=0,
        )
        for val in modified_schemas.values()
        for _, field in val.fields.values()
        if isinstance(field, ModelFieldLike) and field.import_from is not None
    ]

    body = ast.Module(
        [
            ast.ImportFrom(module="pydantic", names=[ast.alias(name="Field")], level=0),
            ast.Import(names=[ast.alias(name="typing")], level=0),
            ast.ImportFrom(module="typing", names=[ast.alias(name="Any")], level=0),
        ]
        + extra_field_imports
        + [
            migrate_ast_node_to_another_version(
                all_names_in_file,
                n,
                module_name,
                modified_schemas,
                modified_enums,
            )
            for n in parsed_file.body
        ],
        [],
    )

    return ast.unparse(body)


def migrate_ast_node_to_another_version(
    all_names_in_module: dict[str, str],
    node: ast.stmt,
    module_python_path: str,
    modified_schemas: dict[str, ModelInfo],
    modified_enums: dict[str, tuple[type[Enum], dict[str, Any]]],
):
    if isinstance(node, ast.ClassDef):
        return _migrate_cls_to_another_version(
            all_names_in_module,
            node,
            module_python_path,
            modified_schemas,
            modified_enums,
        )
    elif isinstance(node, ast.ImportFrom):
        python_path = _get_absolute_python_path_of_import(node, module_python_path)
        node.names = [
            name
            if (name_path := f"{python_path}.{name.name}") not in modified_schemas
            else ast.alias(name=modified_schemas[name_path].name, asname=name.asname)
            for name in node.names
        ]

    return node


def _get_absolute_python_path_of_import(node: ast.ImportFrom, module_python_path: str):
    python_path = ".".join(module_python_path.split(".")[0 : -node.level])
    result = []
    if node.module:
        result.append(node.module)
    if python_path:
        result.append(python_path)
    return ".".join(result)


def _migrate_cls_to_another_version(
    all_names_in_module: dict[str, str],
    cls_node: ast.ClassDef,
    module_python_path: str,
    modified_schemas: dict[str, ModelInfo],
    modified_enums: dict[str, tuple[type[Enum], dict[str, Any]]],
) -> ast.ClassDef:
    cls_python_path = f"{module_python_path}.{cls_node.name}"
    try:
        cls_node = _modify_schema_cls(
            all_names_in_module,
            cls_node,
            modified_schemas,
            module_python_path,
            cls_python_path,
        )
        if cls_python_path in modified_enums:
            cls_node = _modify_enum_cls(cls_node, modified_enums[cls_python_path][1])
    except CodeGenerationError as e:
        raise CodeGenerationError(f'Failed to migrate class "{cls_node.name}" to an older version because: {e}') from e

    if not cls_node.body:
        cls_node.body = [ast.Pass()]
    return cls_node


def _modify_schema_cls(
    all_names_in_module: dict[str, str],
    cls_node: ast.ClassDef,
    modified_schemas: dict[str, ModelInfo],
    module_python_path: str,
    cls_python_path: str,
) -> ast.ClassDef:
    annotation_transformer = _AnnotationTransformerWithSchemaRenaming(
        modified_schemas,
        module_python_path,
        all_names_in_module,
    )
    ast_transformer = _AnnotationASTNodeTransformer(modified_schemas, all_names_in_module, module_python_path)
    if cls_python_path in modified_schemas:
        model_info = modified_schemas[cls_python_path]
        property_definitions = [_make_property_ast(name, func) for name, func in model_info.properties.items()]
        cls_node.name = model_info.name
        field_definitions = [
            ast.AnnAssign(
                target=ast.Name(name, ctx=ast.Store()),
                annotation=ast.Name(annotation_transformer.visit(field.annotation)),
                value=ast.Call(
                    func=ast.Name("Field"),
                    args=[],
                    keywords=[
                        ast.keyword(
                            arg=attr,
                            value=ast.parse(
                                annotation_transformer.visit(_get_attribute_from_field_info(field, attr)),
                                mode="eval",
                            ).body,
                        )
                        for attr in _get_passed_attributes(field.field_info)
                    ],
                ),
                simple=1,
            )
            for name, (_, field) in model_info.fields.items()
        ]
    else:
        property_definitions = []
        field_definitions = [field for field in cls_node.body if isinstance(field, ast.AnnAssign)]

    old_body = [n for n in cls_node.body if not isinstance(n, ast.AnnAssign | ast.Pass | ast.Ellipsis)]
    docstring = _pop_docstring_from_cls_body(old_body)
    cls_node.body = docstring + field_definitions + old_body + property_definitions
    return ast_transformer.visit(cls_node)


def _get_attribute_from_field_info(field: ModelField | ModelFieldLike, attr: str) -> Any:
    field_value = getattr(field.field_info, attr, Sentinel)
    if field_value is Sentinel:
        field_value = field.field_info.extra.get(attr, Sentinel)
    if field_value is Sentinel:  # pragma: no cover # This is just a safeguard that will most likely never be triggered
        raise CodeGenerationError(f'Field "{attr}" is not present in "{field.name}"')
    return field_value


def _make_property_ast(name: str, func: Callable):
    func_source = textwrap.dedent(inspect.getsource(func))
    func_ast = ast.parse(func_source).body[0]
    if not isinstance(func_ast, ast.FunctionDef):
        raise CodeGenerationError(
            "You passed a lambda as a schema property. It is not supported yet. "
            f"Please, use a regular function instead. The lambda you have passed: {func_source}",
        )
    func_ast.decorator_list = [ast.Name("property")]
    func_ast.name = name
    func_ast.args.args[0].annotation = None
    return func_ast


def _modify_enum_cls(cls_node: ast.ClassDef, enum_info: dict[str, Any]) -> ast.ClassDef:
    transformer = _AnnotationTransformer()
    new_body = [
        ast.Assign(
            targets=[ast.Name(member, ctx=ast.Store())],
            value=ast.Name(transformer.visit(member_value)),
            lineno=0,
        )
        for member, member_value in enum_info.items()
    ]

    old_body = [n for n in cls_node.body if not isinstance(n, ast.AnnAssign | ast.Assign | ast.Pass | ast.Ellipsis)]
    docstring = _pop_docstring_from_cls_body(old_body)

    cls_node.body = docstring + new_body + old_body
    return cls_node


def _pop_docstring_from_cls_body(old_body: list[ast.stmt]) -> list[ast.stmt]:
    if (
        len(old_body) > 0
        and isinstance(old_body[0], ast.Expr)
        and isinstance(old_body[0].value, ast.Constant)
        and isinstance(old_body[0].value.value, str)
    ):
        return [old_body.pop(0)]
    else:
        return []


def _get_passed_attributes(field_info: FieldInfo):
    for attr_name, attr_val in _dict_of_empty_field_info.items():
        if attr_name == "extra":
            continue
        if getattr(field_info, attr_name) != attr_val:
            yield attr_name
    yield from field_info.extra


class _AnnotationASTNodeTransformer(ast.NodeTransformer):
    def __init__(
        self,
        modified_schemas: dict[str, ModelInfo],
        all_names_in_module: dict[str, str],
        module_python_path: str,
    ):
        self.modified_schemas = modified_schemas
        self.module_python_path = module_python_path
        self.all_names_in_module = all_names_in_module

    def visit_Name(self, node: ast.Name) -> Any:  # noqa: N802
        return self._get_name(node, node.id)

    def _get_name(self, node: ast.AST, name: str):
        model_info = self.modified_schemas.get(f"{self.all_names_in_module.get(name, self.module_python_path)}.{name}")
        if model_info is not None:
            return ast.Name(model_info.name)
        return node


class _AnnotationTransformer:
    def visit(self, value: Any):  # noqa: C901
        if isinstance(value, list | tuple | set | frozenset):
            return self.transform_collection(value)
        if isinstance(value, dict):
            return self.transform_dict(value)
        if isinstance(value, _BaseGenericAlias | GenericAlias):
            return self.transform_generic_alias(value)
        if value is None or value is NoneType:
            return self.transform_none(value)
        if isinstance(value, type):
            return self.transform_type(value)
        if isinstance(value, Enum):
            return self.transform_enum(value)
        if isinstance(value, auto):
            return self.transform_auto(value)
        if isinstance(value, UnionType):
            return self.transform_union(value)
        if isinstance(value, LambdaType) and _LambdaFunctionName == value.__name__:
            return self.transform_lambda(value)
        if inspect.isfunction(value):
            return self.transform_function(value)
        else:
            return self.transform_other(value)

    def transform_collection(self, value: list | tuple | set | frozenset) -> Any:
        return PlainRepr(value.__class__(map(self.visit, value)))

    def transform_dict(self, value: dict) -> Any:
        return PlainRepr(
            value.__class__((self.visit(k), self.visit(v)) for k, v in value.items()),
        )

    def transform_generic_alias(self, value: _BaseGenericAlias | GenericAlias) -> Any:
        return f"{self.visit(get_origin(value))}[{', '.join(self.visit(a) for a in get_args(value))}]"

    def transform_none(self, value: NoneType) -> Any:
        return "None"

    def transform_type(self, value: type) -> Any:
        # NOTE: Be wary of this hack when migrating to pydantic v2
        # This is a hack for pydantic's Constrained types
        if value.__name__.startswith("Constrained"):
            # No, get_origin and get_args don't work here. No idea why
            origin, args = value.__origin__, value.__args__  # pyright: ignore[reportGeneralTypeIssues]
            return self.visit(origin[args])
        return value.__name__

    def transform_enum(self, value: Enum) -> Any:
        return PlainRepr(f"{value.__class__.__name__}.{value.name}")

    def transform_auto(self, value: auto) -> Any:
        return PlainRepr("auto()")

    def transform_union(self, value: UnionType) -> Any:
        return "typing.Union[" + (", ".join(self.visit(a) for a in get_args(value))) + "]"

    def transform_lambda(self, value: LambdaType) -> Any:
        # We clean source because getsource() can return only a part of the expression which
        # on its own is not a valid expression such as: "\n  .had(default_factory=lambda: 91)"
        return _find_a_lambda(inspect.getsource(value).strip(" \n\t."))

    def transform_function(self, value: Callable) -> Any:
        return PlainRepr(value.__name__)

    def transform_other(self, value: Any) -> Any:
        return PlainRepr(repr(value))


class PlainRepr(str):
    """
    String class where repr doesn't include quotes.
    """

    def __repr__(self) -> str:
        return str(self)


class _AnnotationTransformerWithSchemaRenaming(_AnnotationTransformer):
    def __init__(
        self,
        modified_schemas: dict[str, ModelInfo],
        module_python_path: str,
        all_names_in_module: dict[str, str],
    ):
        self.modified_schemas = modified_schemas
        self.module_python_path = module_python_path
        self.all_names_in_module = all_names_in_module

    def transform_type(self, value: type) -> Any:
        model_info = self.modified_schemas.get(
            f"{self.all_names_in_module.get(value.__name__, self.module_python_path)}.{value.__name__}",
        )
        if model_info is not None:
            return model_info.name
        else:
            return super().transform_type(value)


def _find_a_lambda(source: str) -> str:
    found_lambdas: list[ast.Lambda] = []

    ast.parse(source)
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.keyword) and node.arg == "default_factory" and isinstance(node.value, ast.Lambda):
            found_lambdas.append(node.value)
    if len(found_lambdas) == 1:
        return ast.unparse(found_lambdas[0])
    # These two errors are really hard to cover. Not sure if even possible, honestly :)
    elif len(found_lambdas) == 0:  # pragma: no cover
        raise InvalidGenerationInstructionError(
            f"No lambda found in default_factory even though one was passed: {source}",
        )
    else:  # pragma: no cover
        raise InvalidGenerationInstructionError(
            "More than one lambda found in default_factory. This is not supported.",
        )


# Some day we will want to use this to auto-add imports for new symbols in versions. Some day...
def _get_all_names_defined_in_module(body: ast.Module, module_python_path: str) -> dict[str, str]:
    defined_names = {}
    for node in body.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            defined_names[node.name] = module_python_path
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    defined_names[target.id] = module_python_path
        elif isinstance(node, ast.ImportFrom):
            for name in node.names:
                defined_names[name.name] = _get_absolute_python_path_of_import(node, module_python_path)
    return defined_names
