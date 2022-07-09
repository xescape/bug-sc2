from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from sc2.ids.unit_typeid import UnitTypeId
from sc2.unit import Unit, UnitCommand

from ..modules.module import AIModule
from ..behaviors.gather import GatherBehavior
from ..modules.combat import CombatBehavior
from ..modules.dodge import DodgeBehavior
from ..modules.macro import MacroBehavior

if TYPE_CHECKING:
    from ..ai_base import AIBase


class WorkerManager(AIModule):

    def __init__(self, ai: AIBase) -> None:
        super().__init__(ai)

    async def on_step(self) -> None:
        # self.draft_civilians()
        pass

    def draft_civilians(self) -> None:

        if (
            1 == self.ai.count(UnitTypeId.SPAWNINGPOOL, include_pending=False, include_planned=False)
            and 2 / 3 < self.ai.combat.threat_level
        ):
            worker = next(
                (w
                 for w in self.ai.unit_manager.units.values()
                 if isinstance(w, Worker) and not w.fight_enabled
                 ),
                None
            )
            if worker:
                worker.fight_enabled = True
        elif self.ai.combat.threat_level < 1 / 2:
            worker = min(
                (
                    w
                    for w in self.ai.unit_manager.units.values()
                    if isinstance(w, Worker) and w.fight_enabled
                ),
                key=lambda w : w.unit.shield_health_percentage if w.unit else 1,
                default=None
            )
            if worker:
                worker.fight_enabled = False


class Worker(DodgeBehavior, CombatBehavior, MacroBehavior, GatherBehavior):

    def __init__(self, ai: AIBase, unit: Unit):
        super().__init__(ai, unit)
        self.fight_enabled = False

    def get_command(self) -> Optional[UnitCommand]:
        return self.dodge() or self.fight() or self.macro() or self.gather()
