from __future__ import annotations

import ast
import inspect
import textwrap
from enum import Enum
from typing import TYPE_CHECKING

import typer
from issubclass import issubclass as lenient_issubclass
from pydantic import BaseModel

from cadwyn._asts import get_fancy_repr, pop_docstring_from_cls_body
from cadwyn.exceptions import CadwynRenderError
from cadwyn.schema_generation import (
    PydanticFieldWrapper,
    _EnumWrapper,
    _PydanticModelWrapper,
    generate_versioned_models,
)
from cadwyn.structure.versions import VersionBundle, get_cls_pythonpath

from ._importer import import_attribute_from_string, import_module_from_string

if TYPE_CHECKING:
    from cadwyn.applications import Cadwyn


def render_module_by_path(module_path: str, app_path: str, version: str):
    module = import_module_from_string(module_path)
    app: Cadwyn = import_attribute_from_string(app_path)
    attributes_to_alter = [
        name
        for name, value in module.__dict__.items()
        if lenient_issubclass(value, (Enum, BaseModel)) and value.__module__ == module.__name__
    ]

    try:
        module_ast = ast.parse(inspect.getsource(module))
    except (OSError, SyntaxError, ValueError) as e:  # pragma: no cover
        raise CadwynRenderError(f"Failed to find the source for module {module.__name__}") from e

    return ast.unparse(
        ast.Module(
            body=[
                _render_model_from_ast(node, getattr(module, node.name), app.versions, version)
                if isinstance(node, ast.ClassDef) and node.name in attributes_to_alter
                else node
                for node in module_ast.body
            ],
            type_ignores=module_ast.type_ignores,
        )
    )


def render_model_by_path(model_path: str, app_path: str, version: str) -> str:
    # cadwyn render model schemas:MySchema --app=run:app --version=2000-01-01
    model: type[BaseModel | Enum] = import_attribute_from_string(model_path)
    app: Cadwyn = import_attribute_from_string(app_path)
    return render_model(model, app.versions, version)


def render_model(model: type[BaseModel | Enum], versions: VersionBundle, version: str) -> str:
    try:
        original_cls_node = ast.parse(textwrap.dedent(inspect.getsource(model))).body[0]
    except (OSError, SyntaxError, ValueError):  # pragma: no cover
        typer.echo(f"Failed to find the source for model {get_cls_pythonpath(model)}")
        return f"class {model.__name__}: 'failed to find the original class source'"
    if not isinstance(original_cls_node, ast.ClassDef):
        raise TypeError(f"{get_cls_pythonpath(model)} is not a class")

    return ast.unparse(_render_model_from_ast(original_cls_node, model, versions, version))


def _render_model_from_ast(
    model_ast: ast.ClassDef, model: type[BaseModel | Enum], versions: VersionBundle, version: str
):
    versioned_models = generate_versioned_models(versions)
    generator = versioned_models[version]
    wrapper = generator._get_wrapper_for_model(model)

    if isinstance(wrapper, _EnumWrapper):
        return _render_enum_model(wrapper, model_ast)
    else:
        return _render_pydantic_model(wrapper, model_ast)


def _render_enum_model(wrapper: _EnumWrapper, original_cls_node: ast.ClassDef):
    # This is for possible schema renaming
    original_cls_node.name = wrapper.cls.__name__

    new_body = [
        ast.Assign(
            targets=[ast.Name(member, ctx=ast.Store())],
            value=ast.Name(get_fancy_repr(member_value)),
            lineno=0,
        )
        for member, member_value in wrapper.members.items()
    ]

    old_body = [
        n for n in original_cls_node.body if not isinstance(n, (ast.AnnAssign, ast.Assign, ast.Pass, ast.Constant))
    ]
    docstring = pop_docstring_from_cls_body(old_body)

    original_cls_node.body = docstring + new_body + old_body
    if not original_cls_node.body:
        original_cls_node.body = [ast.Pass()]
    return original_cls_node


def _render_pydantic_model(wrapper: _PydanticModelWrapper, original_cls_node: ast.ClassDef):
    # This is for possible schema renaming
    original_cls_node.name = wrapper.name

    field_definitions = [
        ast.AnnAssign(
            target=ast.Name(name),
            annotation=ast.Name(get_fancy_repr(field.annotation)),
            value=_generate_field_ast(field),
            simple=1,
        )
        for name, field in wrapper.fields.items()
    ]
    validator_definitions = [
        ast.parse(textwrap.dedent(inspect.getsource(validator.func))).body[0]
        for validator in wrapper.validators.values()
        if not validator.is_deleted
    ]

    old_body = [
        n
        for n in original_cls_node.body
        if not (
            isinstance(n, (ast.AnnAssign, ast.Assign, ast.Pass, ast.Constant))
            or (isinstance(n, ast.FunctionDef) and n.name in wrapper.validators)
        )
    ]
    docstring = pop_docstring_from_cls_body(old_body)
    original_cls_node.body = docstring + field_definitions + validator_definitions + old_body
    if not original_cls_node.body:
        original_cls_node.body = [ast.Pass()]
    return original_cls_node


def _generate_field_ast(field: PydanticFieldWrapper) -> ast.Call:
    return ast.Call(
        func=ast.Name("Field"),
        args=[],
        keywords=[
            ast.keyword(arg=attr, value=ast.parse(get_fancy_repr(attr_value), mode="eval").body)
            for attr, attr_value in field.passed_field_attributes.items()
        ],
    )
