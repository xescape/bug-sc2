from __future__ import annotations
from typing import Optional, Set, Union, Iterable, Tuple, TYPE_CHECKING
import numpy as np
import random
from sc2.constants import SPEED_INCREASE_ON_CREEP_DICT

from sc2.position import Point2
from sc2.unit import Unit
from sc2.unit_command import UnitCommand
from sc2.data import race_worker
from abc import ABC, abstractmethod

from ..utils import *
from ..constants import *
from .behavior import BehaviorResult, UnitBehavior
from ..ai_component import AIComponent
if TYPE_CHECKING:
    from ..ai_base import AIBase

class BurrowBehavior(UnitBehavior):

    def __init__(self, ai: AIBase, unit_tag: int):
        super().__init__(ai, unit_tag)

    def execute_single(self, unit: Unit) -> BehaviorResult:

        if unit.type_id not in { UnitTypeId.ROACH, UnitTypeId.ROACHBURROWED }:
            return BehaviorResult.SUCCESS

        if UpgradeId.BURROW not in self.ai.state.upgrades:
            return BehaviorResult.SUCCESS

        if UpgradeId.TUNNELINGCLAWS in self.ai.state.upgrades:
            return BehaviorResult.SUCCESS

        if unit.is_burrowed:
            if unit.health_percentage == 1:
                unit(AbilityId.BURROWUP)
            return BehaviorResult.ONGOING
        elif (
            unit.health_percentage < 1/3
            and unit.weapon_cooldown
        ):
            unit(AbilityId.BURROWDOWN)
            return BehaviorResult.ONGOING

        return BehaviorResult.SUCCESS