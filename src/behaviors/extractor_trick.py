
from __future__ import annotations
from typing import Optional, Set, Union, Iterable, Tuple, TYPE_CHECKING
import numpy as np
import random

from s2clientprotocol.common_pb2 import Point
from sc2.constants import SPEED_INCREASE_ON_CREEP_DICT

from sc2.position import Point2
from sc2.unit import Unit
from sc2.ids.buff_id import BuffId
from sc2.unit_command import UnitCommand
from sc2.data import race_worker
from abc import ABC, abstractmethod

from ..utils import *
from ..constants import *
from .behavior import Behavior, BehaviorResult, UnitBehavior
from ..ai_component import AIComponent
if TYPE_CHECKING:
    from ..ai_base import AIBase

class ExtractorTrickBehavior(UnitBehavior):

    ABILITY = AbilityId.TRANSFUSION_TRANSFUSION

    def __init__(self, ai: AIBase, unit_tag: int):
        super().__init__(ai, unit_tag)

    def execute_single(self, unit: Unit) -> BehaviorResult:

        if unit.type_id != UnitTypeId.EXTRACTOR:
            return BehaviorResult.SUCCESS

        if unit.is_ready:
            return BehaviorResult.SUCCESS

        if not self.ai.extractor_trick_enabled:
            return BehaviorResult.SUCCESS

        if 0 < self.ai.supply_left:
            return BehaviorResult.SUCCESS

        self.ai.extractor_trick_enabled = False
        unit(AbilityId.CANCEL)
        return BehaviorResult.ONGOING