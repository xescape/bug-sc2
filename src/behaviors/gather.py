from __future__ import annotations
from sre_constants import SUCCESS
from typing import Optional, Set, Union, Iterable, Tuple, TYPE_CHECKING

from sc2.unit import Unit
from sc2.unit_command import UnitCommand
from src.units.structure import Structure

from ..resources.mineral_patch import MineralPatch
from ..resources.vespene_geyser import VespeneGeyser
from ..units.unit import CommandableUnit
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
        self.return_target: Optional[Structure] = None

        gather_target = min(
            self.ai.resource_manager.bases.flatten(),
            key = lambda r : r.harvester_balance
        )
        self.set_gather_target(gather_target)

    def set_gather_target(self, gather_target: ResourceUnit) -> None:
        self.gather_target = gather_target
        self.return_target = min(
            self.ai.unit_manager.townhalls,
            key = lambda th : th.unit.distance_to(gather_target.position)
        )

    def gather(self) -> Optional[UnitCommand]:
        
        if not self.gather_target:
            return None
        elif not self.return_target:
            return None
        elif not self.return_target.unit:
            self.set_gather_target(self.gather_target)
            return None
        elif not self.gather_target.remaining:
            return None
        elif not self.unit:
            return None
        
        if isinstance(self.gather_target, MineralPatch):
            target = self.gather_target.unit
        elif isinstance(self.gather_target, VespeneGeyser):
            target = self.gather_target.structure.unit
        else:
            raise TypeError()

        if not target:
            self.gather_target = None
            return None
        if self.unit.is_idle:
            return self.unit.smart(target)
        elif self.unit.is_gathering and self.unit.order_target != target.tag:
            return self.unit.smart(target)
        elif self.unit.is_moving and self.command_queue:
            self.command_queue, target = None, self.command_queue
            return self.unit.smart(target, queue=True)
        elif len(self.unit.orders) == 1:
            if self.unit.is_returning:
                townhall = self.return_target.unit
                # townhall = self.ai.townhalls.ready.closest_to(self.unit)
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