from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Optional

from sc2.unit import Unit, UnitCommand
from sc2.position import Point2

from ..ai_component import AIComponent

if TYPE_CHECKING:
    from ..ai_base import AIBase


class AIUnit(ABC, AIComponent):

    def __init__(self, ai: AIBase, unit: Unit):
        super().__init__(ai)
        self.unit = unit

    def on_step(self) -> None:
        pass

    @property
    def value(self) -> float:
        health = self.unit.health + self.unit.shield
        dps = max(self.unit.ground_dps, self.unit.air_dps)
        return health * dps

    @property
    def is_snapshot(self) -> bool:
        return self.unit.game_loop != self.ai.state.game_loop


class CommandableUnit(AIUnit):

    def __init__(self, ai: AIBase, unit: Unit):
        super().__init__(ai, unit)

    def on_step(self) -> None:
        if (
            (command := self.get_command())
            and not any(self.ai.order_matches_command(o, command) for o in command.unit.orders)
            and not self.ai.do(command, subtract_cost=False, subtract_supply=False)
        ):
            logging.error("command failed: %s", command)

    @abstractmethod
    def get_command(self) -> Optional[UnitCommand]:
        raise NotImplementedError()


class IdleBehavior(CommandableUnit):

    def __init__(self, ai: AIBase, unit: Unit):
        super().__init__(ai, unit)

    def get_command(self) -> Optional[UnitCommand]:
        return None
