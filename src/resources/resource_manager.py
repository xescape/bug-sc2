

from __future__ import annotations
from collections import defaultdict
from typing import TYPE_CHECKING, Counter, DefaultDict, Dict, Iterable, Set, Type, Optional, List
from itertools import chain
import numpy as np
import math

from functools import cached_property

from sc2.position import Point2
from src.cost import Cost
from src.resources.resource_unit import ResourceUnit
from sc2.data import race_townhalls, race_gas
from sc2.ids.buff_id import BuffId

from ..utils import dot
from .resource_base import ResourceBase
from .mineral_patch import MineralPatch
from .vespene_geyser import VespeneGeyser
from .base import Base
from ..behaviors.gather import GatherBehavior
from ..modules.macro import MacroBehavior, MacroPlan
from ..constants import GAS_BY_RACE
from .resource_group import ResourceGroup
from ..modules.module import AIModule
if TYPE_CHECKING:
    from ..ai_base import AIBase

MINING_RADIUS = 1.325
# MINING_RADIUS = 1.4

MINERAL_RADIUS = 1.125
HARVESTER_RADIUS = 0.375

def project_point_onto_line(p: Point2, d: Point2, x: Point2) -> float:
    n = Point2((d[1], -d[0]))
    return x - dot(x - p, n) / dot(n, n) * n

def get_intersections(p0: Point2, r0: float, p1: Point2, r1: float) -> Iterable[Point2]:
    p01 = p1 - p0
    d = np.linalg.norm(p01)
    if 0 < d and abs(r0 - r1) <= d <= r0 + r1:
        a = (r0 ** 2 - r1 ** 2 + d ** 2) / (2 * d)
        h = math.sqrt(r0 ** 2 - a ** 2)
        pm = p0 + (a / d) * p01
        po = (h / d) * np.array([p01.y, -p01.x])
        yield pm + po
        yield pm - po

class ResourceManager(AIModule):

    def __init__(self, ai: AIBase, bases: Iterable[Base]) -> None:
        super().__init__(ai)
        self.do_split = True
        self.bases = ResourceGroup(ai, list(bases))
        self.speedmining_positions = self.get_speedmining_positions()
        self.resource_by_position: Dict[Point2, ResourceUnit] = {
            resource.position: resource
            for resource in self.bases.flatten()
        }
        self.harvesters_by_resource: Counter[ResourceUnit] = Counter()
        self.income = Cost(0, 0, 0, 0)

    @property
    def bases_taken(self) -> Iterable[Base]:
        return (
            b
            for b in self.bases
            if b.townhall and b.townhall.unit.is_ready
        )

    @property
    def mineral_patches(self) -> Iterable[MineralPatch]:
        return (
            r
            for b in self.bases_taken
            for r in b.mineral_patches
        )

    @property
    def vespene_geysers(self) -> Iterable[VespeneGeyser]:
        return (
            r
            for b in self.bases_taken
            for r in b.vespene_geysers
        )

    def add_harvester(self, harvester: GatherBehavior) -> None:
        if gather_target := min(
            (x for b in self.bases_taken for x in b.flatten()),
            key = lambda r : r.harvester_balance,
            default = None
        ):
            harvester.set_gather_target(gather_target)

    def update_bases(self) -> None:

        townhalls_by_position = {
            townhall.unit.position: townhall
            for townhall_type in race_townhalls[self.ai.race]
            for townhall in chain(self.ai.unit_manager.actual_by_type[townhall_type], self.ai.unit_manager.pending_by_type[townhall_type])
        }
        for base in self.bases:
            base.townhall = townhalls_by_position.get(base.position)

    def update_patches_and_geysers(self) -> None:

        gas_buildings_by_position = {
            gas.unit.position: gas
            for gas in self.ai.unit_manager.actual_by_type[race_gas[self.ai.race]]
        }

        resource_by_position = {
            unit.position: unit
            for unit in self.ai.resources
        }

        for base in self.bases:
            for patch in base.mineral_patches:
                patch.unit = resource_by_position.get(patch.position)
            for geyser in base.vespene_geysers:
                geyser.unit = resource_by_position.get(geyser.position)
                geyser.structure = gas_buildings_by_position.get(geyser.position)

        # for resource in self.bases.flatten():
        #     resource.unit = resource_by_position.get(resource.position)
        #     if isinstance(resource, VespeneGeyser):
        #         resource.structure = gas_buildings_by_position.get(resource.position)

    def balance_harvesters(self) -> None:
        harvester = next((
            h
            for h in self.ai.unit_manager.units.values()
            if (
                isinstance(h, GatherBehavior)
                and h.gather_target
                and isinstance(h.gather_target, MineralPatch)
                and 0 < h.gather_target.harvester_balance
            )
        ), None)
        if not harvester:
            return

        transfer_to = next((
            resource
            for resource in self.mineral_patches
            if resource.harvester_balance < 0
        ), None)
        if not transfer_to:
            return
            
        harvester.gather_target = transfer_to

    async def on_step(self) -> None:


        if self.ai.iteration % 16 == 0:
            self.update_patches_and_geysers()

        self.update_bases()

        if self.ai.iteration % 4 == 0:
            self.update_gas()

        self.update_income()

        if self.do_split:
            harvesters = (
                u
                for u in self.ai.unit_manager.units.values()
                if isinstance(u, GatherBehavior)
            )
            self.bases[0].split_initial_workers(harvesters)
            self.do_split = False

        self.harvesters_by_resource = Counter(
            (unit.gather_target
            for unit in self.ai.unit_manager.units.values()
            if isinstance(unit, GatherBehavior) and unit.gather_target))

        self.balance_harvesters()

    def get_speedmining_positions(self) -> Dict[MineralPatch, Point2]:
        positions = dict()
        for base in self.bases:
            for patch in base.mineral_patches:
                target = patch.position.towards(base.position, MINING_RADIUS)
                for patch2 in base.mineral_patches:
                    if patch.position == patch2.position:
                        continue
                    p = project_point_onto_line(target, target - base.position, patch2.position)
                    if patch.position.distance_to(base.position) < patch2.position.distance_to(base.position):
                        continue
                    if MINING_RADIUS <= patch2.position.distance_to(p):
                        continue
                    if target := min(
                        get_intersections(patch.position, MINING_RADIUS, patch2.position, MINING_RADIUS),
                        key = lambda p : p.distance_to(base.position),
                        default = None):
                        break
                positions[patch] = target
        return positions

    def update_gas(self):
        gas_target = self.get_gas_target()
        self.transfer_to_and_from_gas(gas_target)
        self.build_gasses(gas_target)

    def get_gas_target(self) -> float:
        minerals = max(0, self.ai.macro.future_spending.minerals - self.ai.minerals)
        vespene = max(0, self.ai.macro.future_spending.vespene - self.ai.vespene)
        if minerals + vespene == 0:
            minerals = sum(b.mineral_patches.remaining for b in self.bases if b.townhall)
            vespene = sum(b.vespene_geysers.remaining for b in self.bases if b.townhall)

        # gas_ratio = vespene / max(1, vespene + minerals)
        # worker_type = race_worker[self.race]
        # gas_target = gas_ratio * self.count(worker_type, include_pending=False)

        gas_ratio = 1 - 1 / (1 + vespene / max(1, minerals))
        gas_target = self.ai.state.score.food_used_economy * gas_ratio

        # print(minerals, vespene)

        return gas_target

    def build_gasses(self, gas_target: float):
        gas_type = GAS_BY_RACE[self.ai.race]
        gas_depleted = self.ai.gas_buildings.filter(lambda g : not g.has_vespene).amount
        gas_pending = self.ai.count(gas_type, include_actual=False)
        gas_have = self.ai.count(gas_type, include_pending=False, include_planned=False)
        gas_max = sum(1 for g in self.ai.get_owned_geysers())
        gas_want = min(gas_max, gas_depleted + math.ceil((gas_target - 1) / 3))
        if gas_have + gas_pending < gas_want:
            self.ai.macro.add_plan(gas_type)
        elif gas_want < gas_have + gas_pending:
            gas_plans = sorted(self.ai.macro.planned_by_type(gas_type), key = lambda p : p.priority)
            for i, plan in zip(range(gas_have + gas_pending - gas_want), gas_plans):
                if plan.priority < math.inf:
                    self.ai.macro.try_remove_plan(plan)

    def transfer_to_and_from_gas(self, gas_target: float):


        gas_harvester_count = self.harvester_count(VespeneGeyser)
        mineral_harvester_count = self.harvester_count(MineralPatch)
        gas_max = sum(g.harvester_target for g in self.vespene_geysers)
        effective_gas_target = min(gas_max, gas_target)
        effective_gas_balance = gas_harvester_count - effective_gas_target
        mineral_balance = mineral_harvester_count - sum(b.mineral_patches.harvester_target for b in self.bases)

        if (
            0 < mineral_harvester_count
            and (effective_gas_balance < 0 or 0 < mineral_balance)
            and (geyser := self.pick_resource(self.vespene_geysers))
            and (harvester := self.pick_harvester(MineralPatch, geyser.position))
        ):
            harvester.set_gather_target(geyser)
        elif (
            0 < gas_harvester_count
            and (1 <= effective_gas_balance and mineral_balance < 0)
            and (patch := self.pick_resource(self.mineral_patches))
            and (harvester := self.pick_harvester(VespeneGeyser, patch.position))
        ):
            harvester.set_gather_target(patch)

    def harvester_count(self, of_type: Type[ResourceUnit]) -> int:
        return sum(
            1
            for b in self.ai.unit_manager.units.values()
            if isinstance(b, GatherBehavior) and isinstance(b.gather_target, of_type)
        )

    def pick_resource(self, resources: Iterable[ResourceBase]) -> Optional[ResourceUnit]:
        return min(
            (
                r
                for r in resources
                if r.harvester_balance < 0
            ),
            key = lambda r: r.harvester_balance,
            default = None
        )

    def pick_harvester(self, from_type: Type[ResourceUnit], close_to: Point2) -> Optional[GatherBehavior]:
        return min(
            (
                b
                for b in self.ai.unit_manager.units.values()
                if isinstance(b, GatherBehavior) and isinstance(b.gather_target, from_type)
            ),
            key = lambda h : math.inf if not h.unit else h.unit.position.distance_to(close_to),
            default = None
        )

    def update_income(self) -> None:

        self.income.minerals = self.ai.state.score.collection_rate_minerals
        self.income.vespene = self.ai.state.score.collection_rate_vespene
        
        larva_per_second = 0.0
        for hatchery in self.ai.townhalls:
            if hatchery.is_ready:
                larva_per_second += 1/11
                if hatchery.has_buff(BuffId.QUEENSPAWNLARVATIMER):
                    larva_per_second += 3/29
        self.income.larva = 60.0 * larva_per_second