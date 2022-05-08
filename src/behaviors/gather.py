from __future__ import annotations
from sre_constants import SUCCESS
from typing import Optional, Set, Union, Iterable, Tuple, TYPE_CHECKING

from sc2.unit import Unit
from sc2.unit_command import UnitCommand
from src.resources.mineral_patch import MineralPatch
from src.resources.vespene_geyser import VespeneGeyser

from src.units.unit import CommandableUnit

from ..resources.resource_unit import ResourceUnit
from ..utils import *
from ..constants import *
if TYPE_CHECKING:
    from ..ai_base import AIBase

class GatherBehavior(CommandableUnit):

    def __init__(self, ai: AIBase, tag: int):
        super().__init__(ai, tag)
        self.gather_target: Optional[ResourceUnit] = None
        self.command_queue: Optional[Unit] = None

    def gather(self) -> Optional[UnitCommand]:
        
        if not self.gather_target:
            return None
        elif not self.gather_target.remaining:
            return None
        elif not self.unit:
            return None

        # elif not self.unit.is_carrying_resource:
        #     return self.unit.gather(self.target.gather_target)
        # else:
        #     return self.unit.return_resource()

        if not self.ai.townhalls.ready.exists:
            return None
        townhall = self.ai.townhalls.ready.closest_to(self.unit)
        
        if isinstance(self.gather_target, MineralPatch):
            target = self.gather_target.unit
        elif isinstance(self.gather_target, VespeneGeyser):
            target = self.gather_target.structure
        else:
            raise TypeError()

        if self.unit.is_idle:
            return self.unit.smart(target)
        elif self.unit.is_gathering and self.unit.order_target != target.tag:
            return self.unit.smart(target)
        elif self.unit.is_moving and self.command_queue:
            self.command_queue, target = None, self.command_queue
            return self.unit.smart(target, queue=True)
        elif len(self.unit.orders) == 1:
            if self.unit.is_returning:
                townhall = self.ai.townhalls.ready.closest_to(self.unit)
                move_target = townhall.position.towards(self.unit, townhall.radius + self.unit.radius)
                if 0.75 < self.unit.position.distance_to(move_target) < 1.5:
                    self.command_queue = townhall
                    return self.unit.move(move_target)
                    # 
                    # self.unit(AbilityId.SMART, townhall, True)
            else:
                move_target = self.ai.resource_manager.speedmining_positions.get(self.gather_target)
                if not move_target:
                    move_target = target.position.towards(self.unit, target.radius + self.unit.radius)
                if 0.75 < self.unit.position.distance_to(move_target) < 1.75:
                    self.command_queue = target
                    return self.unit.move(move_target)
                    # self.unit.move(move_target)
                    # self.unit(AbilityId.SMART, target, True)