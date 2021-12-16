
from typing import List, Optional

import numpy as np
import math

from sc2.bot_ai import BotAI
from sc2.position import Point2
from sc2.ids.ability_id import AbilityId
from sc2.ids.upgrade_id import UpgradeId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.buff_id import BuffId
from sc2.unit import Unit
from sc2.units import Units

class Pool12AllIn(BotAI):

    def __init__(self, game_step: int = 2):
        self.game_step: int = game_step
        self.mine_gas: bool = True
        self.pull_all: bool = False
        self.pool_position: Point2 = None
        super().__init__()

    async def on_before_start(self):
        self.client.game_step = self.game_step

    async def on_start(self):
        minerals: Units = self.expansion_locations_dict[self.start_location].mineral_field.sorted_by_distance_to(self.start_location)
        assigned = set()
        for i in range(self.workers.amount):
            patch = minerals[i % minerals.amount]
            if i < minerals.amount:
                worker = self.workers.tags_not_in(assigned).closest_to(patch)
            else:
                worker = self.workers.tags_not_in(assigned).furthest_to(patch)
            worker.gather(patch)
            assigned.add(worker.tag)
        self.pool_position = self.start_location.towards(self.main_base_ramp.top_center, -9)

    async def on_step(self, iteration: int):

        if not self.townhalls:
            await self.client.chat_send('(gg)', False)
            await self.client.quit()
            return

        if 96 <= self.vespene:
            self.mine_gas = False
        if self.pull_all:
            army_types = { UnitTypeId.ZERGLING, UnitTypeId.QUEEN, UnitTypeId.OVERLORD }
        else:
            army_types = { UnitTypeId.ZERGLING }

        pool: Optional[Unit] = None
        transfer_from: Optional[Unit] = None
        transfer_to: Optional[Unit] = None
        transfer_off_gas: Optional[Unit] = None
        transfer_to_gas: Optional[Unit] = None
        pool_pending: Optional[Unit] = None
        drone: Optional[Unit] = None
        idle_hatch: Optional[Unit] = None
        hatches: List[Unit] = list()
        queens: List[Unit] = list()
        drone_morphing_count: int = 0
        overlord_morphing_count: int = 0
        extractor_count: int = 0
        drone_max: int = 0
        queen_morphing_count: int = 0
        hatch_pending_count: int = 0

        if self.enemy_structures.flying and not self.enemy_structures.not_flying:
            self.pull_all = True

        for unit in self.structures:
            if unit.is_vespene_geyser:
                extractor_count += 1
                if self.mine_gas and unit.surplus_harvesters < 0:
                    transfer_to_gas = unit
                elif not self.mine_gas and 0 < unit.assigned_harvesters:
                    transfer_off_gas = unit
            elif unit.type_id is UnitTypeId.HATCHERY:
                if unit.is_ready:
                    hatches.append(unit)
                    drone_max += unit.ideal_harvesters
                    if unit.is_idle:
                        idle_hatch = unit
                    elif unit.is_using_ability(AbilityId.TRAINQUEEN_QUEEN):
                        queen_morphing_count += 1
                    if 0 < unit.surplus_harvesters:
                        transfer_from = unit
                    elif unit.surplus_harvesters < 0:
                        transfer_to = unit
            elif unit.type_id is UnitTypeId.SPAWNINGPOOL:
                pool = unit
                if unit.is_using_ability(AbilityId.RESEARCH_ZERGLINGMETABOLICBOOST):
                    self.mine_gas = False

        for unit in self.units:
            if unit.type_id is UnitTypeId.EGG:
                if unit.is_using_ability(AbilityId.LARVATRAIN_DRONE):
                    drone_morphing_count += 1
                elif unit.is_using_ability(AbilityId.LARVATRAIN_OVERLORD):
                    overlord_morphing_count += 1
            elif unit.type_id is UnitTypeId.DRONE:
                if unit.is_idle:
                    if self.mineral_field:
                        patch = self.mineral_field.closest_to(unit)
                        unit.gather(patch)
                elif unit.is_using_ability(AbilityId.ZERGBUILD_SPAWNINGPOOL):
                    pool_pending = unit
                elif unit.is_using_ability(AbilityId.ZERGBUILD_EXTRACTOR):
                    extractor_count = unit
                elif unit.is_using_ability(AbilityId.ZERGBUILD_HATCHERY):
                    hatch_pending_count += 1
                elif transfer_from and transfer_to and unit.order_target == transfer_from.tag:
                    if self.mineral_field:
                        patch = self.mineral_field.closest_to(transfer_to)
                        unit.gather(patch)
                    transfer_to = None
                elif transfer_off_gas and unit.order_target == transfer_off_gas.tag:
                    unit.stop()
                elif transfer_to_gas and unit.order_target != transfer_to_gas and not unit.is_carrying_minerals:
                    unit.gather(transfer_to_gas)
                    transfer_to_gas = None
                elif not unit.is_carrying_resource:
                    drone = unit
            elif unit.type_id in army_types:
                if unit.is_idle:
                    if self.enemy_structures:
                        unit.attack(self.enemy_structures.random.position)
                    elif not self.is_visible(self.enemy_start_locations[0]):
                        unit.attack(self.enemy_start_locations[0])
                    else:
                        self.pull_all = True
                        a = self.game_info.playable_area
                        target = np.random.uniform((a.x, a.y), (a.right, a.top))
                        target = Point2(target)
                        if self.in_pathing_grid(target) and not self.is_visible(target):
                            unit.attack(target)
            elif unit.type_id is UnitTypeId.QUEEN:
                queens.append(unit)

        hatches.sort(key = lambda u : u.tag)
        queens.sort(key = lambda u : u.tag)
        for hatch, queen in zip(hatches, queens):
            if 25 <= queen.energy:
                queen(AbilityId.EFFECT_INJECTLARVA, hatch)
            elif not queen.is_moving and queen.radius + 1 + hatch.radius < queen.distance_to(hatch):
                queen.move(hatch)

        larva_per_second = 1/11 * len(hatches) + 3/29 * min(len(queens), len(hatches))
        drone_target = min(drone_max, 1 + larva_per_second * 50 * 60/55)
        queen_target = len(hatches)
        
        if self.state.game_loop % 2 == 1:
            pass

        # 12 Pool

        elif not pool and not pool_pending:
            target = await self.find_placement(UnitTypeId.SPAWNINGPOOL, self.pool_position, placement_step=1)
            if drone:
                drone.build(UnitTypeId.SPAWNINGPOOL, target)
        elif self.supply_used < 12:
            self.train(UnitTypeId.DRONE)
        elif not extractor_count and not pool_pending:
            geyser = self.vespene_geyser.closest_to(self.start_location)
            if drone:
                drone.build_gas(geyser)
        elif self.supply_used < 13:
            self.train(UnitTypeId.DRONE)
        elif self.supply_cap == 14 and overlord_morphing_count < 1:
            self.train(UnitTypeId.OVERLORD)

        # Hatch First into Lingflood WIP

        # elif self.supply_used < 13:
        #     self.train(UnitTypeId.DRONE)
        # elif self.supply_cap == 14 and overlord_morphing_count < 1:
        #     self.train(UnitTypeId.OVERLORD)
        # elif self.supply_used < 16:
        #     self.train(UnitTypeId.DRONE)
        # elif self.townhalls.amount + hatch_pending_count < 2:
        #     target = await self.get_next_expansion()
        #     if drone and target:
        #         drone.build(UnitTypeId.HATCHERY, target)
        # elif self.supply_used < 19 and not extractor_count:
        #     self.train(UnitTypeId.DRONE)
        # elif not extractor_count:
        #     geyser = self.vespene_geyser.closest_to(self.start_location)
        #     if drone:
        #         drone.build_gas(geyser)
        # elif not pool and not pool_pending:
        #     target = self.start_location.towards(self.game_info.map_center, -10)
        #     if drone:
        #         drone.build(UnitTypeId.SPAWNINGPOOL, target)
        # elif self.supply_used < 19:
        #     self.train(UnitTypeId.DRONE)
        # elif self.supply_cap == 22 and overlord_morphing_count < 1:
        #     self.train(UnitTypeId.OVERLORD)

        elif not pool or not pool.is_ready:
            pass
        elif self.supply_left <= 0 and overlord_morphing_count < 1:
            self.train(UnitTypeId.OVERLORD)
        elif drone_morphing_count < 1 and self.workers.amount + drone_morphing_count < drone_target:
            self.train(UnitTypeId.DRONE)
        elif self.can_afford(UnitTypeId.QUEEN) and drone_target <= self.workers.amount and not self.larva and idle_hatch and len(queens) + queen_morphing_count < queen_target:
            idle_hatch.train(UnitTypeId.QUEEN)
        elif pool.is_idle and 19 <= self.supply_used and UpgradeId.ZERGLINGMOVEMENTSPEED not in self.state.upgrades:
            pool.research(UpgradeId.ZERGLINGMOVEMENTSPEED)
        elif self.larva:
            if self.can_afford(UnitTypeId.HATCHERY) and drone_target <= self.workers.amount and queen_target <= len(queens) and len(hatches) == self.townhalls.amount and hatch_pending_count < 1:
                target = await self.get_next_expansion()
                if drone and target:
                    drone.build(UnitTypeId.HATCHERY, target)
            self.train(UnitTypeId.ZERGLING)