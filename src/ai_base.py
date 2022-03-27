
from abc import ABC
from asyncio import gather
import cProfile, pstats
from collections import defaultdict
from enum import Enum
from dataclasses import dataclass
from functools import cache, cmp_to_key
from importlib.resources import Resource
import math
from pickle import BUILD
import re
import random
from re import S
from typing import Any, DefaultDict, Iterable, Optional, Tuple, Type, Union, Coroutine, Set, List, Callable, Dict
import numpy as np
import os
import json
import MapAnalyzer
import matplotlib.pyplot as plt

from MapAnalyzer import MapData
from sc2.game_data import GameData

from sc2.data import Alliance
from sc2.game_state import ActionRawUnitCommand
from sc2.ids.effect_id import EffectId
from sc2 import game_info, game_state
from sc2.position import Point2, Point3
from sc2.bot_ai import BotAI
from sc2.constants import IS_DETECTOR, SPEED_INCREASE_ON_CREEP_DICT, IS_STRUCTURE, TARGET_AIR, TARGET_GROUND
from sc2.ids.buff_id import BuffId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.effect_id import EffectId
from sc2.ids.ability_id import AbilityId
from sc2.ids.upgrade_id import UpgradeId
from sc2.dicts.upgrade_researched_from import UPGRADE_RESEARCHED_FROM
from sc2.dicts.unit_trained_from import UNIT_TRAINED_FROM
from sc2.dicts.unit_research_abilities import RESEARCH_INFO
from sc2.dicts.unit_train_build_abilities import TRAIN_INFO
from sc2.dicts.unit_tech_alias import UNIT_TECH_ALIAS
from sc2.data import Result, race_townhalls, race_worker, ActionResult
from sc2.unit import Unit
from sc2.unit_command import UnitCommand
from sc2.units import Units

from .modules.chat import Chat
from .modules.module import AIModule
from .modules.creep import Creep
from .modules.drop_manager import DropManager
from .resources.mineral_patch import MineralPatch
from .resources.vespene_geyser import VespeneGeyser
from .modules.scout_manager import ScoutManager
from .modules.unit_manager import IGNORED_UNIT_TYPES, UnitManager
from .simulation.simulation import Simulation
from .value_map import ValueMap
from .resources.base import Base
from .resources.resource_group import BalancingMode, ResourceGroup
from .behaviors.dodge import *
from .constants import *
from .macro_plan import MacroPlan
from .cost import Cost
from .utils import *
from .behaviors.dodge import *
from .enums import PerformanceMode

VERSION_PATH = 'version.txt'

class PlacementNotFoundError(Exception):
    pass

class VersionConflictError(Exception):
    pass

@dataclass
class MapStaticData:

    version: np.ndarray
    distance: np.ndarray

    def flip(self):
        self.distance = 1 - self.distance

class AIBase(ABC, BotAI):

    def __init__(self):

        self.raw_affects_selection = True

        self.version: str = ''
        self.game_step: int = 3
        self.performance: PerformanceMode = PerformanceMode.DEFAULT
        self.debug: bool = False
        self.destroy_destructables: bool = True

        self.macro_plans: List[MacroPlan] = list()
        self.composition: Dict[UnitTypeId, int] = dict()
        self.cost: Dict[MacroId, Cost] = dict()
        self.enemy_positions: Optional[Dict[int, Point2]] = dict()
        self.weapons: Dict[UnitTypeId, List] = dict()
        self.dps: Dict[UnitTypeId, float] = dict()
        self.resource_by_position: Dict[Point2, Unit] = dict()
        self.townhall_by_position: Dict[Point2, Unit] = dict()
        self.gas_building_by_position: Dict[Point2, Unit] = dict()
        self.unit_by_tag: Dict[int, Unit] = dict()
        self.enemies: Dict[int, Unit] = dict()
        self.enemies_by_type: DefaultDict[UnitTypeId, Set[Unit]] = defaultdict(lambda:set())
        self.actual_by_type: DefaultDict[MacroId, Set[Unit]] = defaultdict(lambda:set())
        self.pending_by_type: DefaultDict[MacroId, Set[Unit]] = defaultdict(lambda:set())
        self.planned_by_type: DefaultDict[MacroId, Set[MacroPlan]] = defaultdict(lambda:set())
        self.destructables_fixed: Set[Unit] = set()
        self.damage_taken: Dict[int] = dict()
        self.dodge: List[DodgeElement] = list()
        self.dodge_delayed: List[DodgeEffectDelayed] = list()

        self.threat_level: float = 0.0
        self.opponent_name: Optional[str] = None
        self.advantage_map: np.ndarray = None
        self.map_data: MapStaticData = None
        self.map_analyzer: MapData = None
        self.enemy_vs_ground_map: np.ndarray = None
        self.enemy_vs_air_map: np.ndarray = None
        self.army_vs_ground_map: np.ndarray = None
        self.army_vs_air_map: np.ndarray = None
        self.army_projection: np.ndarray = None
        self.enemy_projection: np.ndarray = None
        self.extractor_trick_enabled: bool = False

        super().__init__()

    @property
    def is_speedmining_enabled(self) -> bool:
        if self.performance is PerformanceMode.DEFAULT:
            return True
        elif self.performance is PerformanceMode.HIGH_PERFORMANCE:
            return False
        raise Exception

    def estimate_enemy_velocity(self, unit: Unit) -> Point2:
        previous_position = self.enemy_positions.get(unit.tag, unit.position)
        velocity = (unit.position - previous_position) * 22.4 / self.client.game_step
        return velocity

    async def on_before_start(self):

        with open(VERSION_PATH, 'r') as file:
            self.version = file.readline().replace('\n', '')

        for unit in UnitTypeId:
            data = self.game_data.units.get(unit.value)
            if not data:
                continue
            weapons = list(data._proto.weapons)
            self.weapons[unit] = weapons
            dps = 0
            for weapon in weapons:
                damage = weapon.damage
                speed = weapon.speed
                dps = max(dps, damage / speed)
            self.dps[unit] = dps

        self.client.game_step = self.game_step
        self.cost = dict()
        for unit in UnitTypeId:
            try:
                cost = self.calculate_cost(unit)
                food = int(self.calculate_supply_cost(unit))
                self.cost[unit] = Cost(cost.minerals, cost.vespene, food)
            except:
                pass
        for upgrade in UpgradeId:
            try:
                cost = self.calculate_cost(upgrade)
                self.cost[upgrade] = Cost(cost.minerals, cost.vespene, 0)
            except:
                pass

    async def on_start(self):

        self.map_analyzer = MapData(self)
        self.map_data = await self.load_map_data()
        
        await self.initialize_bases()

        self.scout_manager: ScoutManager = ScoutManager(self)
        self.drop_manager: DropManager = DropManager(self)
        self.unit_manager: UnitManager = UnitManager(self)
        self.chat: Chat = Chat(self)
        self.creep: Creep = Creep(self)

        self.modules: List[AIModule] = [
            value
            for value in self.__dict__.values()
            if isinstance(value, AIModule)
        ]

        # await self.client.debug_create_unit([
        #     [UnitTypeId.ZERGLINGBURROWED, 1, self.bases[1].position, 2],
        # ])

    def handle_errors(self):
        for error in self.state.action_errors:
            if error.result == ActionResult.CantBuildLocationInvalid.value:
                if unit := self.unit_by_tag.get(error.unit_tag):
                    self.scout_manager.blocked_positions[unit.position] = self.time

    def units_detecting(self, unit: Unit) -> Iterable[Unit]:
        for detector_type in IS_DETECTOR:
            for detector in self.actual_by_type[detector_type]:
                distance = detector.position.distance_to(unit.position)
                if distance <= detector.radius + detector.detect_range + unit.radius:
                    yield detector
        pass

    @cache
    def can_attack_ground(self, unit: UnitTypeId) -> bool:
        if unit in { UnitTypeId.BATTLECRUISER, UnitTypeId.ORACLE }:
            return True
        weapons = self.weapons.get(unit)
        if weapons:
            return any(weapon.type in TARGET_GROUND for weapon in weapons)
        return False

    @cache
    def can_attack_air(self, unit: UnitTypeId) -> bool:
        if unit == UnitTypeId.BATTLECRUISER:
            return True
        weapons = self.weapons.get(unit)
        if weapons:
            return any(weapon.type in TARGET_AIR for weapon in weapons)
        return False

    def can_attack(self, unit: Unit, target: Unit) -> bool:
        if target.is_cloaked and not target.is_revealed:
            return False
        elif target.is_burrowed and not any(self.units_detecting(target)):
            return False
        elif target._proto.is_flying:
            return unit.can_attack_air
        else:
            return unit.can_attack_ground

    def handle_actions(self):
        plan_by_unit = {
            plan.unit: plan
            for plan in self.macro_plans
            if plan.unit
        }
        for action in self.state.actions_unit_commands:
            if item := ITEM_BY_ABILITY.get(action.exact_id):
                for unit_tag in action.unit_tags:
                    if plan := plan_by_unit.get(unit_tag):
                        if plan.item == item:
                            self.remove_macro_plan(plan)
            #     self.remove_macro_plan_by_item(item)

    def handle_delayed_effects(self):

        self.dodge_delayed = [
            d
            for d in self.dodge_delayed
            if self.time <= d.time_of_impact
        ]

    def enumerate_army(self) -> Iterable[Unit]:
        for unit in self.units:
            if unit.type_id not in CIVILIANS:
                yield unit
            elif unit.tag in self.unit_manager.drafted_civilians:
                yield unit

    async def on_step(self, iteration: int):

        profiler = None
        if self.debug and iteration % 100 == 0:
            profiler = cProfile.Profile()
            profiler.enable()

        self.update_tables()
        self.handle_errors()
        self.handle_actions()
        self.update_maps()
        self.handle_delayed_effects()
        self.update_bases()
        self.update_gas()
        self.make_composition()
        self.assess_threat_level()
        self.save_enemy_positions()

        await self.macro()

        if self.debug:
            await self.draw_debug()

        for module in self.modules:
            await module.on_step()

        if profiler:
            profiler.disable()
            stats = pstats.Stats(profiler)
            stats.sort_stats(pstats.SortKey.TIME)
            stats.dump_stats(filename='profiling.prof')

    async def on_end(self, game_result: Result):
        pass

    async def on_building_construction_started(self, unit: Unit):
        pass

    async def on_building_construction_complete(self, unit: Unit):
        pass

    async def on_enemy_unit_entered_vision(self, unit: Unit):
        pass

    async def on_enemy_unit_left_vision(self, unit_tag: int):
        pass

    async def on_unit_created(self, unit: Unit):
        pass

    async def on_unit_destroyed(self, unit_tag: int):
        self.enemies.pop(unit_tag, None)
        self.bases.try_remove(unit_tag)
        pass

    async def on_unit_took_damage(self, unit: Unit, amount_damage_taken: float):
        if unit.is_structure and not unit.is_ready:
            should_cancel = False
            if unit.shield_health_percentage < 0.1:
                should_cancel = True
            if should_cancel:
                unit(AbilityId.CANCEL)
        self.damage_taken[unit.tag] = self.time
        pass
        
    async def on_unit_type_changed(self, unit: Unit, previous_type: UnitTypeId):
        pass

    async def on_upgrade_complete(self, upgrade: UpgradeId):
        pass

    async def kill_random_unit(self):
        chance = self.supply_used / 200
        chance = pow(chance, 3)
        exclude = { self.townhalls[0].tag } if self.townhalls else set()
        if chance < random.random():
            unit = self.all_own_units.tags_not_in(exclude).random
            await self.client.debug_kill_unit(unit)

    def count(self,
        item: MacroId,
        include_pending: bool = True,
        include_planned: bool = True,
        include_actual: bool = True
    ) -> int:

        factor = 2 if item == UnitTypeId.ZERGLING else 1
        
        sum = 0
        if include_actual:
            if item in WORKERS:
                sum += self.supply_workers_fixed
            else:
                sum += len(self.actual_by_type[item])
        if include_pending:
            sum += factor * len(self.pending_by_type[item])
        if include_planned:
            sum += factor * len(self.planned_by_type[item])

        return sum

    def update_tables(self):

        enemies_remembered = self.enemies.copy()
        self.enemies = {
            enemy.tag: enemy
            for enemy in self.all_enemy_units
            if not enemy.is_snapshot
        }
        for tag, enemy in enemies_remembered.items():
            if tag in self.enemies:
                # can see
                continue
            elif self.is_visible(enemy.position):
                # could see, but not there
                continue
            # cannot see, maybe still there
            self.enemies[tag] = enemy

        self.enemies_by_type.clear()
        for enemy in self.enemies.values():
            self.enemies_by_type[enemy.type_id].add(enemy)
        
        self.resource_by_position.clear()
        self.gas_building_by_position.clear()
        self.unit_by_tag.clear()
        self.townhall_by_position.clear()
        self.actual_by_type.clear()
        self.pending_by_type.clear()
        self.destructables_fixed.clear()

        for townhall in self.townhalls.ready:
            self.townhall_by_position[townhall.position] = townhall
        
        for unit in self.all_own_units:
            self.unit_by_tag[unit.tag] = unit
            if unit.is_ready:
                self.actual_by_type[unit.type_id].add(unit)
            else:
                self.pending_by_type[unit.type_id].add(unit)
            for order in unit.orders:
                ability = order.ability.exact_id
                if item := ITEM_BY_ABILITY.get(ability):
                    self.pending_by_type[item].add(unit)

        for unit in self.resources:
            self.resource_by_position[unit.position] = unit
        for gas in self.gas_buildings:
            self.gas_building_by_position[gas.position] = gas

        for unit in self.destructables:
            if 0 < unit.armor:
                self.destructables_fixed.add(unit)

        for upgrade in self.state.upgrades:
            self.actual_by_type[upgrade].add(upgrade)

    @property
    def supply_workers_fixed(self) -> int:
        return self.supply_used - self.supply_army
        
    @property
    def gas_harvesters(self) -> Iterable[int]:
        for base in self.bases:
            for gas in base.vespene_geysers:
                for harvester in gas.harvesters:
                    yield harvester

    @property
    def gas_harvester_count(self) -> int:
        return sum(1 for _ in self.gas_harvesters)

    @property
    def gas_harvester_target(self) -> int:
        return sum(b.vespene_geysers.harvester_target for b in self.bases)

    @property
    def gas_harvester_balance(self) -> int:
        return self.gas_harvester_count - self.gas_harvester_target

    def save_enemy_positions(self):
        self.enemy_positions.clear()
        for enemy in self.enemies.values():
            self.enemy_positions[enemy.tag] = enemy.position

    def make_composition(self):
        if 200 <= self.supply_used:
            return
        composition_have = {
            unit: self.count(unit)
            for unit in self.composition.keys()
        }
        for unit, count in self.composition.items():
            if count < 1:
                continue
            elif count <= composition_have[unit]:
                continue
            if any(self.get_missing_requirements(unit, include_pending=False, include_planned=False)):
                continue
            priority = -self.count(unit, include_planned=False) /  count
            plans = self.planned_by_type[unit]
            if not plans:
                self.add_macro_plan(MacroPlan(unit, priority=priority))
            else:
                for plan in plans:
                    if BUILD_ORDER_PRIORITY <= plan.priority:
                        continue
                    plan.priority = priority

    def update_gas(self):
        gas_target = self.get_gas_target()
        self.transfer_to_and_from_gas(gas_target)
        self.build_gasses(gas_target)

    def build_gasses(self, gas_target: float):
        gas_depleted = self.gas_buildings.filter(lambda g : not g.has_vespene).amount
        gas_pending = self.count(UnitTypeId.EXTRACTOR, include_actual=False)
        gas_have = self.count(UnitTypeId.EXTRACTOR, include_pending=False, include_planned=False)
        gas_max = sum(1 for g in self.get_owned_geysers())
        gas_want = min(gas_max, gas_depleted + math.ceil(gas_target / 3))
        if gas_have + gas_pending < gas_want:
            self.add_macro_plan(MacroPlan(UnitTypeId.EXTRACTOR))
        else:
            for _, plan in zip(range(gas_have + gas_pending - gas_want), list(self.planned_by_type[UnitTypeId.EXTRACTOR])):
                if plan.priority < BUILD_ORDER_PRIORITY:
                    self.remove_macro_plan(plan)

    def get_gas_target(self) -> float:

        cost_zero = Cost(0, 0, 0)
        cost_sum = sum((self.cost[plan.item] for plan in self.macro_plans), cost_zero)
        cost_sum += sum(
            (self.cost[unit] * max(0, count - self.count(unit))
            for unit, count in self.composition.items()),
            cost_zero)
        minerals = max(0, cost_sum.minerals - self.minerals)
        vespene = max(0, cost_sum.vespene - self.vespene)
        if minerals + vespene == 0:
            minerals = sum(b.mineral_patches.remaining for b in self.bases if b.townhall)
            vespene = sum(b.vespene_geysers.remaining for b in self.bases if b.townhall)

        gas_ratio = vespene / max(1, vespene + minerals)
        worker_type = race_worker[self.race]
        gas_target = gas_ratio * self.count(worker_type, include_pending=False)

        return gas_target

    def transfer_to_and_from_gas(self, gas_target: float):

        effective_gas_target = min(self.vespene_geysers.harvester_target, gas_target)
        effective_gas_balance = self.vespene_geysers.harvester_count - effective_gas_target

        # if self.gas_harvester_count + 1 <= gas_target and self.vespene_geysers.harvester_balance < 0:
        if 0 < self.mineral_patches.harvester_count and (effective_gas_balance < 0 or 0 < self.mineral_patches.harvester_balance):

            if not self.mineral_patches.try_transfer_to(self.vespene_geysers):
                print('transfer to gas failure')

        # elif gas_target <= self.gas_harvester_count - 1 or 0 < self.vespene_geysers.harvester_balance:
        elif 0 < self.vespene_geysers.harvester_count and (1 <= effective_gas_balance and self.mineral_patches.harvester_balance < 0):

            if not self.vespene_geysers.try_transfer_to(self.mineral_patches):
                print('transfer from gas failure')
        
    def update_bases(self):

        for base in self.bases:
            base.defensive_units.clear()
            base.defensive_units_planned.clear()

        for unit_type in STATIC_DEFENSE[self.race]:
            for unit in chain(self.actual_by_type[unit_type], self.pending_by_type[unit_type]):
                base = min(self.bases, key=lambda b:b.position.distance_to(unit.position))
                base.defensive_units.append(unit)
            for plan in self.planned_by_type[unit_type]:
                if not isinstance(plan.target, Point2):
                    continue
                base = min(self.bases, key=lambda b:b.position.distance_to(plan.target))
                base.defensive_units_planned.append(plan)

        self.bases.update()
        self.mineral_patches.update()
        self.vespene_geysers.update()

    async def initialize_bases(self):

        exclude_bases = {
            self.start_location,
            *self.enemy_start_locations
        }
        positions = dict()
        for b in self.expansion_locations_list:
            if b in exclude_bases:
                continue
            if await self.can_place_single(UnitTypeId.HATCHERY, b):
                continue
            positions[b] = await self.find_placement(UnitTypeId.HATCHERY, b, placement_step=1)


        bases = sorted((
            Base(self, positions.get(position, position), (m.position for m in resources.mineral_field), (g.position for g in resources.vespene_geyser))
            for position, resources in self.expansion_locations_dict.items()
        ), key = lambda b : self.map_data.distance[b.position.rounded] - .5 * b.position.distance_to(self.enemy_start_locations[0]) / self.game_info.map_size.length)

        self.bases = ResourceGroup(self, bases)
        self.bases.balancing_mode = BalancingMode.NONE
        self.bases[0].split_initial_workers(set(self.workers))

        self.vespene_geysers = ResourceGroup(self, [b.vespene_geysers for b in self.bases])
        self.mineral_patches = ResourceGroup(self, [b.mineral_patches for b in self.bases])

    async def load_map_data(self) -> Coroutine[Any, Any, MapStaticData]:

        path = os.path.join('data', f'{self.game_info.map_name}.npz')
        try:
            map_data_files = np.load(path)
            map_data = MapStaticData(**map_data_files)
            map_data_version = str(map_data.version)
            # if map_data_version != self.version:
            #     raise VersionConflictError()
            if 0.5 < map_data.distance[self.start_location.rounded]:
                map_data.flip()
        except (FileNotFoundError, VersionConflictError, TypeError):
            map_data = await self.create_map_data()
            np.savez_compressed(path, **map_data.__dict__)
        return map_data

    async def create_map_data(self) -> Coroutine[Any, Any, MapStaticData]:
        print('creating map data ...')
        distance_map = await self.create_distance_map()
        return MapStaticData(self.version, distance_map)

    async def create_distance_map(self) -> Coroutine[Any, Any, np.ndarray]:

        boundary = np.transpose(self.game_info.pathing_grid.data_numpy == 0)
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                p = self.start_location + Point2((dx, dy))
                boundary[p.rounded] = False

        distance_ground_self = flood_fill(boundary, [self.start_location.rounded])
        distance_ground_enemy = flood_fill(boundary, [p.rounded for p in self.enemy_start_locations])
        distance_ground = distance_ground_self / (distance_ground_self + distance_ground_enemy)
        distance_air = np.zeros_like(distance_ground)
        for p, _ in np.ndenumerate(distance_ground):
            position = Point2(p)
            distance_self = position.distance_to(self.start_location)
            distance_enemy = min(position.distance_to(p) for p in self.enemy_start_locations)
            distance_air[p] = distance_self / (distance_self + distance_enemy)
        distance_map = np.where(np.isnan(distance_ground), distance_air, distance_ground)
        
        return distance_map

    async def draw_debug(self):

        font_color = (255, 255, 255)
        font_size = 12

        for i, target in enumerate(self.macro_plans):

            positions = []

            if not target.target:
                pass
            elif isinstance(target.target, Unit):
                positions.append(target.target)
            elif isinstance(target.target, Point3):
                positions.append(target.target)
            elif isinstance(target.target, Point2):
                z = self.get_terrain_z_height(target.target)
                positions.append(Point3((target.target.x, target.target.y, z)))

            if target.unit:
                unit = self.unit_by_tag.get(target.unit)
                if unit:
                    positions.append(unit)

            text = f"{str(i+1)} {str(target.item.name)}"

            for position in positions:
                self.client.debug_text_world(
                    text,
                    position,
                    color=font_color,
                    size=font_size)

        self.client.debug_text_screen(f'Threat Level: {round(100 * self.threat_level)}%', (0.01, 0.01))
        self.client.debug_text_screen(f'Enemy Bases: {len(self.scout_manager.enemy_bases)}', (0.01, 0.02))
        self.client.debug_text_screen(f'Gas Target: {round(self.get_gas_target(), 3)}', (0.01, 0.03))
        self.client.debug_text_screen(f'Creep Coverage: {round(100 * self.creep.coverage)}%', (0.01, 0.06))
        for i, plan in enumerate(self.macro_plans):
            self.client.debug_text_screen(f'{1+i} {plan.item.name}', (0.01, 0.1 + 0.01 * i))

    def add_macro_plan(self, plan: MacroPlan):
        self.macro_plans.append(plan)
        self.planned_by_type[plan.item].add(plan)

    def remove_macro_plan(self, plan: MacroPlan):
        self.macro_plans.remove(plan)
        self.planned_by_type[plan.item].remove(plan)

    def get_missing_requirements(self, item: Union[UnitTypeId, UpgradeId], **kwargs) -> Set[Union[UnitTypeId, UpgradeId]]:

        if item not in REQUIREMENTS_KEYS:
            return set()

        requirements = list()

        if type(item) is UnitTypeId:
            trainers = UNIT_TRAINED_FROM[item]
            trainer = min(trainers, key=lambda v:v.value)
            requirements.append(trainer)
            info = TRAIN_INFO[trainer][item]
        elif type(item) is UpgradeId:
            researcher = UPGRADE_RESEARCHED_FROM[item]
            requirements.append(researcher)
            info = RESEARCH_INFO[researcher][item]
        else:
            raise TypeError()

        requirements.append(info.get('required_building'))
        requirements.append(info.get('required_upgrade'))
        requirements = [r for r in requirements if r is not UnitTypeId.LARVA]
        
        missing = set()
        i = 0
        while i < len(requirements):
            requirement = requirements[i]
            i += 1
            if not requirement:
                continue
            if type(requirement) is UnitTypeId:
                equivalents = WITH_TECH_EQUIVALENTS[requirement]
            elif type(requirement) is UpgradeId:
                equivalents = { requirement }
            else:
                raise TypeError()
            if any(self.count(e, **kwargs) for e in equivalents):
                continue
            missing.add(requirement)
            requirements.extend(self.get_missing_requirements(requirement, **kwargs))

        return missing

    async def macro(self):

        reserve = Cost(0, 0, 0)
        exclude = { o.unit for o in self.macro_plans }
        exclude.update(unit.tag for units in self.pending_by_type.values() for unit in units)
        exclude.update(self.unit_manager.drafted_civilians)
        self.macro_plans.sort(key = lambda t : t.priority, reverse=True)

        for i, plan in enumerate(list(self.macro_plans)):

            # if (
            #     any(self.get_missing_requirements(plan.item, include_pending=False, include_planned=False))
            #     and plan.priority < BUILD_ORDER_PRIORITY
            # ):
            #     continue

            # if (2 if self.extractor_trick_enabled else 1) <= i and plan.priority == BUILD_ORDER_PRIORITY:
            #     break

            unit = None
            if plan.unit:
                unit = self.unit_by_tag.get(plan.unit)
            if unit == None or unit.type_id == UnitTypeId.EGG:
                unit, plan.ability = self.search_trainer(plan.item, exclude=exclude)
            if unit and plan.ability and unit.is_using_ability(plan.ability['ability']):
                continue
            if unit == None:
                continue
            if any(self.get_missing_requirements(plan.item, include_pending=False, include_planned=False)):
                continue

            cost = self.cost[plan.item]
            reserve += cost

            plan.unit = unit.tag
            exclude.add(plan.unit)

            if plan.target == None:
                try:
                    plan.target = await self.get_target(unit, plan)
                except PlacementNotFoundError as p: 
                    continue

            if (
                plan.priority < BUILD_ORDER_PRIORITY
                and self.is_structure(plan.item)
                and isinstance(plan.target, Point2)
                and not await self.can_place_single(plan.item, plan.target)
            ):
                self.remove_macro_plan(plan)
                continue

            eta = 0
            if 0 < cost.minerals:
                eta = max(eta, 60 * (reserve.minerals - self.minerals) / max(1, self.state.score.collection_rate_minerals))
            if 0 < cost.vespene:
                eta = max(eta, 60 * (reserve.vespene - self.vespene) / max(1, self.state.score.collection_rate_vespene))
            if 0 < cost.food:
                if self.supply_left < cost.food:
                    eta = None
            plan.eta = eta


    def get_owned_geysers(self):
        for base in self.bases:
            if base.position not in self.townhall_by_position.keys():
                continue
            for gas in base.vespene_geysers:
                geyser = self.resource_by_position.get(gas.position)
                if not geyser:
                    continue
                yield geyser

    async def get_target(self, unit: Unit, objective: MacroPlan) -> Coroutine[any, any, Union[Unit, Point2]]:
        gas_type = GAS_BY_RACE[self.race]
        if objective.item == gas_type:
            exclude_positions = {
                geyser.position
                for geyser in self.gas_buildings
            }
            exclude_tags = {
                order.target
                for trainer in self.pending_by_type[gas_type]
                for order in trainer.orders
                if isinstance(order.target, int)
            }
            exclude_tags.update({
                step.target.tag
                for step in self.planned_by_type[gas_type]
                if step.target
            })
            geysers = [
                geyser
                for geyser in self.get_owned_geysers()
                if (
                    geyser.position not in exclude_positions
                    and geyser.tag not in exclude_tags
                )
            ]
            if not any(geysers):
                raise PlacementNotFoundError()
            else:
                return random.choice(geysers)
                
        elif "requires_placement_position" in objective.ability:
            position = await self.get_target_position(objective.item, unit)
            withAddon = objective in { UnitTypeId.BARRACKS, UnitTypeId.FACTORY, UnitTypeId.STARPORT }
            
            if objective.max_distance is None:
                max_distance = 20
            else:
                max_distance = objective.max_distance
            position = await self.find_placement(objective.ability["ability"], position, max_distance=max_distance, placement_step=1, addon_place=withAddon)
            if position is None:
                raise PlacementNotFoundError()
            else:
                return position
        else:
            return None

    def search_trainer(self, item: Union[UnitTypeId, UpgradeId], exclude: Set[int]) -> Tuple[Unit, any]:

        if type(item) == UnitTypeId:
            trainer_types = {
                equivalent
                for trainer in UNIT_TRAINED_FROM[item]
                for equivalent in WITH_TECH_EQUIVALENTS[trainer]
            }
        elif type(item) == UpgradeId:
            trainer_types = WITH_TECH_EQUIVALENTS[UPGRADE_RESEARCHED_FROM[item]]

        def enumerate_trainers(trainer_type: UnitTypeId) -> Iterable[Unit]:
            if trainer_type == race_worker[self.race]:
                if tag := self.bases.try_remove_any():
                    if tag not in exclude:
                        if unit := self.unit_by_tag.get(tag):
                            if unit.type_id == trainer_type:
                                return [unit]
                    else:
                        self.bases.try_add(tag)
            return self.actual_by_type[trainer_type]

        trainers = sorted((
            trainer
            for trainer_type in trainer_types
            for trainer in enumerate_trainers(trainer_type)
        ), key=lambda t:t.tag)
            
        for trainer in trainers:

            if not trainer:
                continue

            if not trainer.is_ready:
                continue

            if trainer.tag in exclude:
                continue

            if not self.has_capacity(trainer):
                continue

            already_training = False
            for order in trainer.orders:
                order_unit = UNIT_BY_TRAIN_ABILITY.get(order.ability.id)
                if order_unit:
                    already_training = True
                    break
            if already_training:
                continue

            if type(item) is UnitTypeId:
                table = TRAIN_INFO
            elif type(item) is UpgradeId:
                table = RESEARCH_INFO

            element = table.get(trainer.type_id)
            if not element:
                continue

            ability = element.get(item)

            if not ability:
                continue

            if "requires_techlab" in ability and not trainer.has_techlab:
                continue
                
            return trainer, ability

        return None, None

    def get_unit_range(self, unit: Unit, ground: bool = True, air: bool = True) -> float:
        unit_range = 0
        if ground:
            unit_range = max(unit_range, unit.ground_range)
        if air:
            unit_range = max(unit_range, unit.air_range)
        unit_range += unit_boni(unit, RANGE_UPGRADES)
        return unit_range

    def enumerate_enemies(self) -> Iterable[Unit]:
        enemies = (
            e
            for e in self.enemies.values()
            if e.type_id not in IGNORED_UNIT_TYPES
        )
        if self.destroy_destructables:
            enemies = chain(enemies, self.destructables_fixed)
        return enemies

    async def get_target_position(self, target: UnitTypeId, trainer: Unit) -> Point2:
        if self.is_structure(target):
            data = self.game_data.units.get(target.value)
            if target in race_townhalls[self.race]:
                for b in self.bases:
                    if b.townhall:
                        continue
                    if b.position in self.scout_manager.blocked_positions:
                        continue
                    if not b.remaining:
                        continue
                    # if not (await self.can_place_single(target, b.position)):
                    #     continue
                    return b.position
                raise PlacementNotFoundError()
            elif self.townhalls.exists:
                position = self.townhalls.closest_to(self.start_location).position
                position = position.towards(self.game_info.map_center, 5.7)
                if data:
                    position = position.rounded
                    offset = data.footprint_radius % 1
                    position = position.offset((offset, offset))
                return position
            else:
                raise PlacementNotFoundError()
        else:
            return trainer.position

    def has_capacity(self, unit: Unit) -> bool:
        if self.is_structure(unit.type_id):
            if unit.has_reactor:
                return len(unit.orders) < 2
            else:
                return unit.is_idle
        else:
            return True

    def is_structure(self, unit: UnitTypeId) -> bool:
        if data := self.game_data.units.get(unit.value):
            return IS_STRUCTURE in data.attributes
        return False

    def get_unit_value(self, unit: Unit) -> float:
        return (unit.health + unit.shield) * max(unit.ground_dps, unit.air_dps)

    def get_unit_cost(self, unit_type: UnitTypeId) -> int:
        cost = self.calculate_unit_value(unit_type)
        return cost.minerals + cost.vespene

    def update_maps(self):

        enemy_map = ValueMap(self)

        for enemy in self.enemies.values():
            enemy_map.add(enemy, 0.0)
        self.enemy_vs_ground_map = np.maximum(1, enemy_map.get_map_vs_ground())
        self.enemy_vs_air_map = np.maximum(1, enemy_map.get_map_vs_air())

        army_map = ValueMap(self)
        for unit in self.enumerate_army():
            army_map.add(unit, 0.0)
        self.army_vs_ground_map = np.maximum(1, army_map.get_map_vs_ground())
        self.army_vs_air_map = np.maximum(1, army_map.get_map_vs_air())

        self.dodge.clear()
        delayed_positions = { e.position for e in self.dodge_delayed }
        for effect in self.state.effects:
            if effect.id in DODGE_DELAYED_EFFECTS:
                dodge_effect = DodgeEffectDelayed(effect, self.time)
                if dodge_effect.position in delayed_positions:
                    continue
                self.dodge_delayed.append(dodge_effect)
            elif effect.id in DODGE_EFFECTS:
                self.dodge.append(DodgeEffect(effect))
        for type in DODGE_UNITS:
            for enemy in self.enemies_by_type[type]:
                self.dodge.append(DodgeUnit(enemy))
        self.dodge.extend(self.dodge_delayed)

        enemy_map = np.maximum(self.enemy_vs_air_map, self.enemy_vs_ground_map)
        army_map = np.maximum(self.army_vs_air_map, self.army_vs_ground_map)
        self.advantage_map = army_map / enemy_map

        # EXPERIMENTAL FIGHTING

        army_health = self.map_analyzer.get_clean_air_grid(0)
        enemy_health = self.map_analyzer.get_clean_air_grid(0)

        for unit in self.enumerate_army():
            army_health[unit.position.rounded] += unit.health + unit.shield
        for unit in self.enemies.values():
            enemy_health[unit.position.rounded] += unit.health + unit.shield

        def add_unit_to_map(unit: Unit, map: np.ndarray, t: float) -> np.ndarray:
            range = 1 + unit.radius + max(unit.ground_range, unit.air_range) + 1.4 * t * unit.movement_speed
            dps = max(unit.ground_dps, unit.air_dps)
            if dps < 1:
                return map
            return self.map_analyzer.add_cost(
                position = unit.position,
                radius = range,
                grid = map,
                weight = dps)

        dt = 2/3
        for t in np.arange(0, 5, dt):

            army_dps = self.map_analyzer.get_clean_air_grid(0)
            for unit in self.enumerate_army():
                if 0 < army_health[unit.position.rounded]:
                    army_dps = add_unit_to_map(unit, army_dps, t)
                    
            enemy_dps = self.map_analyzer.get_clean_air_grid(0)
            for unit in self.enemies.values():
                if 0 < enemy_health[unit.position.rounded]:
                    enemy_dps = add_unit_to_map(unit, enemy_dps, t)

            discount = pow(.9, t)
            army_health -= discount * enemy_dps * dt
            enemy_health -= discount * army_dps * dt

        self.army_projection = army_health
        self.enemy_projection = enemy_health

        # EXPERIMENTAL SIMULATION

        # simulation = Simulation(self, self.enumerate_army(), self.enemies.values())
        # simulation.run(10)

    def assess_threat_level(self):
        
        value_self = sum(self.get_unit_value(u) for u in self.enumerate_army())
        # value_self += sum(len(self.pending_by_type[t]) * self.get_unit_value(t) for t in self.composition)
        value_enemy = sum(self.get_unit_value(e) for e in self.enemies.values())
        value_enemy_threats = sum(self.get_unit_value(e) * (1 - self.map_data.distance[e.position.rounded]) for e in self.enemies.values())

        self.threat_level = value_enemy_threats / max(1, value_self + value_enemy_threats)

    def can_afford_with_reserve(self, cost: Cost, reserve: Cost) -> bool:
        if max(0, self.minerals - reserve.minerals) < cost.minerals:
            return False
        elif max(0, self.vespene - reserve.vespene) < cost.vespene:
            return False
        elif max(0, self.supply_left - reserve.food) < cost.food:
            return False
        else:
            return True

    def get_max_harvester(self) -> int:
        workers = 0
        workers += sum((b.harvester_target for b in self.bases))
        workers += 16 * self.count(UnitTypeId.HATCHERY, include_actual=False, include_planned=False)
        workers += 3 * self.count(GAS_BY_RACE[self.race], include_actual=False, include_planned=False)
        return workers

    def blocked_base(self, position: Point2) -> Optional[Base]:
        px, py = position
        radius = 3
        for base in self.bases:
            bx, by = base.position
            if abs(px - bx) < radius and abs(py - by) < radius:
                return base
        return None