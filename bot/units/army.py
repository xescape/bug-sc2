from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sc2.unit import Unit, UnitCommand

from ..behaviors.bile import BileBehavior
from ..behaviors.burrow import BurrowBehavior
from ..behaviors.overlord_drop import OverlordDropMemberBehavior
from ..behaviors.search import SearchBehavior
from ..modules.combat import CombatBehavior
from ..modules.dodge import DodgeBehavior
from ..modules.macro import MacroBehavior

if TYPE_CHECKING:
    from ..ai_base import AIBase


class Army(
    DodgeBehavior,
    MacroBehavior,
    BurrowBehavior,
    BileBehavior,
    OverlordDropMemberBehavior,
    CombatBehavior,
    SearchBehavior,
):
    def __init__(self, ai: AIBase, unit: Unit):
        super().__init__(ai, unit)

    def get_command(self) -> Optional[UnitCommand]:
        if command := self.dodge():
            return command
        elif command := self.macro():
            return command
        elif command := self.burrow():
            return command
        elif command := self.bile():
            return command
        elif command := self.execute_overlord_drop():
            return command
        elif command := self.fight():
            return command
        elif command := self.search():
            return command
        else:
            return None
