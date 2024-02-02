import ast
import inspect
from typing import Any

from cadwyn.codegen import DEFAULT_CODEGEN_PLUGINS, CodegenContext
from tests.conftest import CreateLocalSimpleVersionedPackages, LatestModuleFor


class VariableRenamerPlugin:
    node_type = ast.AnnAssign

    def __call__(self, node: ast.AnnAssign, context: CodegenContext) -> Any:
        if (  # pragma: no branch
            isinstance(node.target, ast.Name) and node.target.id in context.extra["variable_renaming_mapping"]
        ):
            node.target.id = context.extra["variable_renaming_mapping"][node.target.id]
        return node


def test__ccustom_per_assignment_codegen_plugin(
    latest_module_for: LatestModuleFor,
    create_local_simple_versioned_packages: CreateLocalSimpleVersionedPackages,
):
    latest_module_for("hello: int =2\ndarkness: int =3; my = 4\nold: int=5")
    v1 = create_local_simple_versioned_packages(
        codegen_plugins=(*DEFAULT_CODEGEN_PLUGINS, VariableRenamerPlugin()),
        extra_context={"variable_renaming_mapping": {"hello": "pew", "old": "doo", "darkness": "zoo", "my": "boo"}},
    )
    assert inspect.getsource(v1) == (
        "# THIS FILE WAS AUTO-GENERATED BY CADWYN. DO NOT EVER TRY TO EDIT IT BY HAND\n\n"
        "pew: int = 2\nzoo: int = 3\nmy = 4\ndoo: int = 5\n"
    )


def test__ccustom_per_assignment_codegen_plugin__with_nested_nodes__should_not_work(
    latest_module_for: LatestModuleFor,
    create_local_simple_versioned_packages: CreateLocalSimpleVersionedPackages,
):
    latest_module_for("if 1 == 0 + 1: hello: int = 11")
    v1 = create_local_simple_versioned_packages(
        codegen_plugins=(*DEFAULT_CODEGEN_PLUGINS, VariableRenamerPlugin()),
        extra_context={"variable_renaming_mapping": {"hello": "pew"}},
    )
    assert inspect.getsource(v1) == (
        "# THIS FILE WAS AUTO-GENERATED BY CADWYN. DO NOT EVER TRY TO EDIT IT BY HAND\n\n"
        "if 1 == 0 + 1:\n    hello: int = 11\n"
    )
