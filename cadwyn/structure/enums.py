from collections.abc import Mapping
from enum import Enum
from typing import Any, Union

import attrs

from .common import _HiddenAttributeMixin


@attrs.define(slots=True)
class EnumHadMembersInstruction(_HiddenAttributeMixin):
    enum: type[Enum]
    members: Mapping[str, Any]


@attrs.define(slots=True)
class EnumDidntHaveMembersInstruction(_HiddenAttributeMixin):
    enum: type[Enum]
    members: tuple[str, ...]


@attrs.define(slots=True)
class EnumInstructionFactory:
    enum_class: type[Enum]

    def had(self, **enum_member_to_value_mapping: Any) -> EnumHadMembersInstruction:
        return EnumHadMembersInstruction(self.enum_class, enum_member_to_value_mapping)

    def didnt_have(self, *enum_members: str) -> EnumDidntHaveMembersInstruction:
        return EnumDidntHaveMembersInstruction(self.enum_class, enum_members)


def enum(enum_class: type[Enum], /) -> EnumInstructionFactory:
    return EnumInstructionFactory(enum_class)


AlterEnumSubInstruction = Union[EnumHadMembersInstruction, EnumDidntHaveMembersInstruction]
