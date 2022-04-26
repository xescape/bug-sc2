from __future__ import annotations
from typing import Any, Optional, Set, Union, Iterable, Tuple, TYPE_CHECKING
import numpy as np
import random
from abc import ABC
from sc2.constants import SPEED_INCREASE_ON_CREEP_DICT

from sc2.position import Point2
from sc2.unit import Unit
from sc2.unit_command import UnitCommand
from sc2.data import race_worker
from abc import ABC, abstractmethod

from ..utils import *
from ..constants import *
from .behavior import Behavior
from ..ai_component import AIComponent
if TYPE_CHECKING:
    from ..ai_base import AIBase

class BurrowBehavior(ABC, Behavior):

    def burrow(self) -> Optional[UnitCommand]:

        if self.unit.type_id not in { UnitTypeId.ROACH, UnitTypeId.ROACHBURROWED }:
            return None

        if UpgradeId.BURROW not in self.ai.state.upgrades:
            return None

        if UpgradeId.TUNNELINGCLAWS in self.ai.state.upgrades:
            return None

        if self.unit.is_burrowed:
            if self.unit.health_percentage == 1 or self.unit.is_revealed:
                return self.unit(AbilityId.BURROWUP)
        else:
            if (
                self.unit.health_percentage < 1/3
                and self.unit.weapon_cooldown
                and not self.unit.is_revealed
            ):
                return unit(AbilityId.BURROWDOWN)