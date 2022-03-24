
from __future__ import annotations
import math

from typing import TYPE_CHECKING
from sc2.unit import Unit
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.ability_id import AbilityId

from ..constants import CHANGELINGS, ENERGY_COST
from ..utils import sample
from ..resources.base import Base
from .behavior import Behavior, BehaviorResult, UnitBehavior
if TYPE_CHECKING:
    from ..ai_base import AIBase

class ChangelingSpawnBehavior(UnitBehavior):
    
    ABILITY = AbilityId.SPAWNCHANGELING_SPAWNCHANGELING

    def __init__(self, ai: AIBase, unit_tag: int):
        super().__init__(ai, unit_tag)
        
    def execute_single(self, unit: Unit) -> BehaviorResult:

        if ENERGY_COST[self.ABILITY] <= unit.energy:
            unit(self.ABILITY)
            return BehaviorResult.ONGOING

        return BehaviorResult.SUCCESS