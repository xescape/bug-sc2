
from collections import defaultdict
from functools import reduce
import math
import random
from typing import Iterable, Optional, Tuple, Union, Coroutine, Set, List, Callable, Dict

from numpy.lib.function_base import select
from s2clientprotocol.error_pb2 import Error
from sc2 import constants
from sc2 import unit

from sc2.position import Point2, Point3
from sc2.bot_ai import BotAI
from sc2.constants import ALL_GAS, SPEED_INCREASE_ON_CREEP_DICT, IS_STRUCTURE
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.ability_id import AbilityId
from sc2.ids.upgrade_id import UpgradeId
from sc2.dicts.upgrade_researched_from import UPGRADE_RESEARCHED_FROM
from sc2.dicts.unit_trained_from import UNIT_TRAINED_FROM
from sc2.dicts.unit_research_abilities import RESEARCH_INFO
from sc2.dicts.unit_train_build_abilities import TRAIN_INFO
from sc2.dicts.unit_tech_alias import UNIT_TECH_ALIAS
from sc2.data import Result, race_townhalls, race_worker
from sc2.unit import Unit
from sc2.units import Units
from suntzu.gas import Gas
from suntzu.base import Base
from suntzu.minerals import Minerals
from suntzu.observation import Observation
from suntzu.resource import Resource
from suntzu.resource_group import ResourceGroup

from .constants import CHANGELINGS, REQUIREMENTS_KEYS, WITH_TECH_EQUIVALENTS, CIVILIANS, GAS_BY_RACE, REQUIREMENTS, REQUIREMENTS_EXCLUDE, STATIC_DEFENSE, SUPPLY, SUPPLY_PROVIDED, TOWNHALL, TRAIN_ABILITIES, UNIT_BY_TRAIN_ABILITY, UPGRADE_BY_RESEARCH_ABILITY
from .macro_target import MacroTarget
from .cost import Cost
from .utils import can_attack, get_requirements, armyValue, unitPriority, canAttack, center, dot, unitValue

import suntzu.base

RESOURCE_DISTANCE_THRESHOLD = 10

class PlacementNotFound(Exception):
    pass

class CommonAI(BotAI):

    def __init__(self,
        game_step: int = 1,
        debug: bool = False,
        version_path: str = './version.txt',
        greetings_path: str = './greetings.txt',
    ):


        self.tags: List[str] = []

        with open(version_path, 'r') as file:
            version = file.readline()
            self.tags.append(version)

        with open(greetings_path, 'r') as file:
            self.quotes = [
                line.replace('\n', '')
                for line in file.readlines()
            ]

        self.game_step = game_step
        self.debug = debug
        self.raw_affects_selection = True
        self.gas_target = 0
        self.greet_enabled = True
        self.macro_targets = list()
        self.gas_ratio = 6 / 22
        self.observation: Observation = Observation()
        self.print_first_iteration = True
        self.worker_split: Dict[int, int] = None
        self.destroy_destructables = True
        self.tag = None
        self.bases: ResourceGroup = None
        self.base_distance_matrix: Dict[Point2, Dict[Point2, float]] = dict()

    def update_bases(self):

        self.bases.update(self.observation)

        # while self.gas_resources.harvester_count + 1 <= min(self.gas_resources.harvester_target, self.gas_target):
        #     harvester = self.mineral_resources.try_transfer_to(self.gas_resources)
        #     if not harvester:
        #         break
        #     print('transfer minerals to gas external')

        # while self.gas_target <= self.gas_resources.harvester_count - 1:
        #     harvester = self.gas_resources.try_transfer_to(self.mineral_resources)
        #     if not harvester:
        #         break
        #     print('transfer gas to minerals external')

    async def init_bases(self):

        main = self.townhalls[0]
        main(AbilityId.RALLY_WORKERS, target=main)

        self.base_distance_matrix = dict()
        for a in self.expansion_locations_list:
            self.base_distance_matrix[a] = dict()
            for b in self.expansion_locations_list:
                self.base_distance_matrix[a][b] = await self.client.query_pathing(a, b) or a.distance_to(b)

        townhall_positions = sorted(
            (p for p in self.expansion_locations_list),
            key = lambda p : self.base_distance_matrix[main.position][p]
        )

        bases = []
        for townhall_position in townhall_positions:
            resources = self.expansion_locations_dict[townhall_position]
            minerals = (m.position for m in resources.mineral_field)
            gasses = (g.position for g in resources.vespene_geyser)
            base = Base(townhall_position, minerals, gasses)
            bases.append(base)

        self.bases = ResourceGroup(bases)
        self.bases.resources[0].minerals.do_worker_split(self.workers)

    async def on_before_start(self):
        self.client.game_step = self.game_step
        pass

    async def on_start(self):

        self.geysers_by_expanion = {
            base: {
                geyser.position
                for geyser in resources.vespene_geyser
            }
            for base, resources in self.expansion_locations_dict.items()
        }

        self.minerals_by_expansion = {
            base: {
                mineral.position
                for mineral in resources.mineral_field
            }
            for base, resources in self.expansion_locations_dict.items()
        }

        await self.init_bases()

    def assign_idle_workers(self):

        harvesters = list(self.bases.harvesters)

        for worker in self.workers:

            if worker.tag in harvesters:
                continue
            if any(step.unit == worker.tag for step in self.macro_targets):
                continue
            if not worker.is_idle:
                continue

            self.bases.try_add(worker.tag)
            # print('assigning idle worker: ' + str(worker.tag))

    def update_observation(self):
        self.observation = self.create_observation()

    async def on_step(self, iteration: int):

        self.iteration = iteration
        self.destructables_filtered = self.destructables.filter(lambda d : 0 < d.armor)

        # if 8 * 60 < self.time:
        #     await self.client.debug_leave()

        if 1 < self.time and self.greet_enabled:
            for tag in self.tags:
                await self.client.chat_send('Tag:' + tag, True)
            quote = random.choice(self.quotes)
            await self.client.chat_send(quote, False)
            self.greet_enabled = False

        if self.debug:
            self.draw_debug()

    def draw_debug(self):

        font_color = (255, 255, 255)
        font_size = 12

        for i, target in enumerate(self.macro_targets):

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
                unit = self.observation.unit_by_tag.get(target.unit)
                if unit:
                    positions.append(unit)

            text = f"{str(i+1)} {str(target.item.name)}"

            for position in positions:
                self.client.debug_text_world(
                    text,
                    position,
                    color=font_color,
                    size=font_size)

    async def on_end(self, game_result: Result):
        pass

    async def on_building_construction_started(self, unit: Unit):
        if unit.type_id in race_townhalls[self.race]:
            base = next((b for b in self.bases.resources if b.townhall_position == unit.position), None)
            if base:
                base.townhall = unit.tag
                base.townhall_ready = False
        pass

    async def on_building_construction_complete(self, unit: Unit):
        if unit.type_id in race_townhalls[self.race]:
            base = next((b for b in self.bases.resources if b.townhall_position == unit.position), None)
            if base:
                base.townhall = unit.tag
                base.townhall_ready = True
        # if unit.type_id in race_townhalls[self.race] and self.mineral_field.exists:
        #     unit.smart(self.mineral_field.closest_to(unit))
        pass

    async def on_enemy_unit_entered_vision(self, unit: Unit):
        pass

    async def on_enemy_unit_left_vision(self, unit_tag: int):
        pass

    async def on_unit_created(self, unit: Unit):
        if self.time == 0:
            pass
        # elif unit.type_id == race_worker[self.race]:
        #     base = min(self.bases.values(), key = lambda b : b.townhall_position.distance_to(unit))
        #     base.add_mineral_harvester(unit.tag)
        pass

    async def on_unit_destroyed(self, unit_tag: int):
        base = next((b for b in self.bases.resources if b.townhall == unit_tag), None)
        if base:
            base.townhall = None
            base.townhall_ready = False
        pass

    async def on_unit_took_damage(self, unit: Unit, amount_damage_taken: float):
        pass
        
    async def on_unit_type_changed(self, unit: Unit, previous_type: UnitTypeId):
        pass

    async def on_upgrade_complete(self, upgrade: UpgradeId):
        pass
    
    def create_observation(self):
        observation = Observation()
        for unit in self.all_own_units:
            observation.add_unit(unit)
        for upgrade in self.state.upgrades:
            observation.add_upgrade(upgrade)
        for resource in self.resources:
            observation.add_unit(resource)
        for plan in self.macro_targets:
            observation.add_plan(plan)
        worker_type = race_worker[self.race]
        worker_pending = observation.count(worker_type, include_actual=False, include_pending=False, include_planned=False)
        observation.worker_supply_fixed = self.supply_used - self.supply_army - worker_pending
        return observation

    def estimate_income(self):
        harvesters_on_minerals = sum(t.assigned_harvesters for t in self.townhalls)
        harvesters_on_vespene = sum(g.assigned_harvesters for g in self.gas_buildings)
        income_minerals = 48 * harvesters_on_minerals / 60
        income_vespene = 54 * harvesters_on_vespene / 60
        return income_minerals, income_vespene

    def time_to_harvest(self, minerals, vespene):
        income_minerals, income_vespene = self.estimate_income()
        time_minerals = minerals / max(1, income_minerals)
        time_vespene = vespene / max(1, income_vespene)
        return max(0, time_minerals, time_vespene)

    async def time_to_reach(self, unit, target):
        if isinstance(target, Unit):
            position = target.position
        elif isinstance(target, Point2):
            position = target
        else:
            raise TypeError()
        path = await self.client.query_pathing(unit, position)
        if not path:
            path = unit.position.distance_to(position)
        if not unit.movement_speed:
            raise Exception()
        return path / unit.movement_speed

    def add_macro_target(self, target: MacroTarget):
        self.macro_targets.append(target)
        self.observation.add_plan(target)

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
            if any(self.observation.count(e, **kwargs) for e in equivalents):
                continue
            missing.add(requirement)
            requirements.extend(self.get_missing_requirements(requirement, **kwargs))

        return missing

        # for r in requirements:
        #     yield r
        #     for r2 in get_requirements(r):
        #         yield r2

    async def macro(self):

        reserve = Cost(0, 0, 0)
        exclude = { o.unit for o in self.macro_targets }
        # macro_targets_new = list(self.macro_targets)

        took_action = False

        income_minerals, income_vespene = self.estimate_income()

        self.macro_targets.sort(key = lambda t : t.priority, reverse=True)

        i = 0
        while i < len(self.macro_targets):
        # for objective in self.macro_targets:

            objective = self.macro_targets[i]
            i += 1

            if not objective.cost:
                objective.cost = self.getCost(objective.item)

            can_afford = self.canAffordWithReserve(objective.cost, reserve)
            reserve += objective.cost

            if objective.condition and not objective.condition(self):
                continue

            if objective.unit:
                unit = self.observation.unit_by_tag.get(objective.unit)
            else:
                unit, objective.ability = self.search_trainer(objective.item, exclude=exclude)

            if unit is None:
                continue

            if unit.type_id != UnitTypeId.LARVA:
                objective.unit = unit.tag
            exclude.add(objective.unit)

            if objective.target is None:
                try:
                    objective.target = await self.get_target(unit, objective)
                except PlacementNotFound as p: 
                    continue

            # requirement_missing = False
            # for requirement in REQUIREMENTS[objective.item]:
            #     if not sum(
            #         self.observation.count(equivalent, include_pending=False, include_planned=False)
            #         for equivalent in WITH_TECH_EQUIVALENTS[requirement]
            #     ):
            #         requirement_missing = True
            #         break

            # if requirement_missing:
            #     continue

            if any(self.get_missing_requirements(objective.item, include_pending=False, include_planned=False)):
                continue

            # if not objective.ability["ability"] in await self.get_available_abilities(unit, ignore_resource_requirements=True):
            #     continue


                
            # base = next((b for b in self.bases if any(unit.tag in hs for hs in b.mineral_harvesters.values())), None)
            # if base:
            #     base.remove_mineral_harvester(unit.tag)

            if (
                objective.target
                and not unit.is_moving
                and unit.movement_speed
            ):
            
                time = await self.time_to_reach(unit, objective.target)
                
                minerals_needed = reserve.minerals - self.minerals
                vespene_needed = reserve.vespene - self.vespene
                time_minerals = minerals_needed / max(1, income_minerals)
                time_vespene = vespene_needed / max(1, income_vespene)
                time_to_harvest =  max(0, time_minerals, time_vespene)

                if time_to_harvest < time:
                    
                    if type(objective.target) is Unit:
                        move_to = objective.target.position
                    else:
                        move_to = objective.target

                    if unit.is_carrying_resource:
                        unit.return_resource()
                        unit.move(move_to, queue=True)
                    else:
                        unit.move(move_to)

                    if unit.type_id == race_worker[self.race]:
                        self.bases.try_remove(unit.tag)

            if not can_afford:
                continue
            
            # abilities = await self.get_available_abilities(unit)
            # if not objective.ability["ability"] in abilities:
            #     print("ability with cost failed:" + str(objective))
            #     raise Error()

            if unit.type_id == race_worker[self.race]:
                self.bases.try_remove(unit.tag)

            if self.isStructure(objective.item) and isinstance(objective.target, Point2):
                if not await self.can_place_single(objective.item, objective.target):
                    objective.target = None
                    continue

            if took_action:
                continue

            # if unit.type_id == race_worker[self.race]:
            #     removed = False
            #     for base in self.bases.values():
            #         for minerals in base.minerals:
            #             if unit.tag in minerals.harvesters:
            #                 minerals.harvesters.remove(unit.tag)
            #                 removed = True
            #         for gas in base.gasses:
            #             if unit.tag in gas.harvesters:
            #                 gas.harvesters.remove(unit.tag)
            #                 removed = True
            #         if removed:
            #             break

            #     if not removed:
            #         raise Error()

            queue = False
            if unit.is_carrying_resource:
                unit.return_resource()
                queue = True

            if objective.item == UnitTypeId.RAVAGER:
                queue = queue

            if not unit(objective.ability["ability"], target=objective.target, queue=queue):
                print("objective failed:" + str(objective))
                raise Exception()

            took_action = True

            self.observation.add_pending(objective.item, unit)

            i -= 1
            self.macro_targets.pop(i)


    def get_owned_geysers(self):
        for townhall in self.townhalls.ready:
            for geyser_position in self.geysers_by_expanion.get(townhall.position, []):
                geyser = self.observation.resource_by_position.get(geyser_position)
                if geyser:
                    yield geyser

    async def get_target(self, unit: Unit, objective: MacroTarget) -> Coroutine[any, any, Union[Unit, Point2]]:
        gas_type = GAS_BY_RACE[self.race]
        if objective.item == gas_type:
            exclude_positions = {
                geyser.position
                for geyser in self.gas_buildings
            }
            exclude_tags = {
                order.target
                for trainer in self.observation.pending_by_type[gas_type]
                for order in trainer.orders
                if isinstance(order.target, int)
            }
            exclude_tags.update({
                step.target.tag
                for step in self.observation.planned_by_type[gas_type]
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
                raise PlacementNotFound()
            else:
                return geysers[0]
                
        elif "requires_placement_position" in objective.ability:
            position = await self.get_target_position(objective.item, unit)
            withAddon = objective in { UnitTypeId.BARRACKS, UnitTypeId.FACTORY, UnitTypeId.STARPORT }
            if objective.item == TOWNHALL[self.race]:
                max_distance = 0
            else:
                max_distance = 8
            position = await self.find_placement(objective.ability["ability"], position, max_distance=max_distance, placement_step=1, addon_place=withAddon)
            if position is None:
                raise PlacementNotFound()
            else:
                return position
        else:
            return None


    def getCost(self, item: Union[UnitTypeId, UpgradeId]) -> Cost:
        cost = self.calculate_cost(item)
        food = 0
        if type(item) is UnitTypeId:
            food = int(self.calculate_supply_cost(item))
        return Cost(cost.minerals, cost.vespene, food)

    def search_trainer(self, item: Union[UnitTypeId, UpgradeId], exclude: Set[int]) -> Tuple[Unit, any]:

        if type(item) is UnitTypeId:
            trainer_types = {
                equivalent
                for trainer in UNIT_TRAINED_FROM[item]
                for equivalent in WITH_TECH_EQUIVALENTS[trainer]
            }
        elif type(item) is UpgradeId:
            trainer_types = WITH_TECH_EQUIVALENTS[UPGRADE_RESEARCHED_FROM[item]]

        # if trainer_types == { race_worker[self.race] }:
        #     trainers = { self.}

        trainers = (
            unit
            for trainer_type in trainer_types
            for unit in self.observation.actual_by_type[trainer_type]
        )
            
        for trainer in trainers:

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
                ability = TRAIN_INFO[trainer.type_id][item]
            elif type(item) is UpgradeId:
                ability = RESEARCH_INFO[trainer.type_id][item]

            if "requires_techlab" in ability and not trainer.has_techlab:
                continue

            # requiredBuilding = ability.get("required_building", None)
            # if requiredBuilding is not None:
            #     requiredBuilding = withEquivalents(requiredBuilding)
            #     if not self.structures(requiredBuilding).ready.exists:
            #         continue

            # requiredUpgrade = ability.get("required_upgrade", None)
            # if requiredUpgrade is not None:
            #     if not requiredUpgrade in self.state.upgrades:
            #         continue

            # if ability["ability"] not in abilities:
            #     continue
                
            return trainer, ability

        return None, None

    def unit_cost(self, unit: Unit) -> int:
        cost = self.game_data.units[unit.type_id.value].cost
        return cost.minerals + cost.vespene

    async def micro(self):

        friends = list(self.enumerate_army())

        enemies = self.enemy_units
        enemies = enemies.exclude_type(CIVILIANS)

        if not enemies.exists:
            enemies = self.enemy_units
        if not enemies.exists:
            enemies = self.enemy_structures

        for unit in friends:

            if unit.type_id == UnitTypeId.OVERSEER:
                enemies_valid = enemies
            else:
                enemies_valid = enemies.filter(lambda e : (
                    can_attack(unit, e)
                    and not unit.is_hallucination
                    and not unit.type_id in CHANGELINGS
                ))

            if enemies_valid.exists:

                target = enemies_valid.closest_to(unit)
                range = unit.air_range if target.is_flying else unit.ground_range

                friends_rating = sum(unitValue(f) / max(8, target.distance_to(f)) for f in friends)
                enemies_rating = sum(unitValue(e) / max(8, unit.distance_to(e)) for e in enemies)

                distance_ref = self.game_info.map_size.length
                distance_to_base = min((unit.distance_to(t) for t in self.townhalls), default=0)

                advantage = 1
                advantage_value = friends_rating / max(1, enemies_rating)
                advantage_defender = distance_ref / (distance_ref + distance_to_base)
                # advantage_defender = (distance_bias + target.distance_to(enemyBaseCenter)) / (distance_bias + target.distance_to(baseCenter))
                
                advantage_creep = 1
                creep_bonus = SPEED_INCREASE_ON_CREEP_DICT.get(unit.type_id)
                if creep_bonus and self.state.creep.is_empty(unit.position.rounded):
                    advantage_creep = 1 / creep_bonus

                advantage *= advantage_value
                advantage *= advantage_defender
                advantage *= advantage_creep
                advantage_threshold = 1

                if advantage < advantage_threshold / 3:

                    if not unit.is_moving:
                        unit.move(unit.position.towards(target, -12))

                elif advantage < advantage_threshold:

                    if (
                        unit.weapon_cooldown
                        and unit.target_in_range(target, unit.distance_to_weapon_ready)
                        # and unit.distance_to(target) < unit.radius + range + unit.distance_to_weapon_ready + target.radius
                    ):
                        unit.move(unit.position.towards(target, -12))
                    else:
                        unit.attack(target.position)
                    
                elif advantage < advantage_threshold * 3:

                    unit.attack(target.position)

                else:

                    if unit.weapon_cooldown:
                        unit.move(target.position)
                    else:
                        unit.attack(target.position)

    
            elif self.destroy_destructables and self.destructables_filtered.exists:

                if unit.type_id == UnitTypeId.BANELING:
                    continue

                unit.attack(self.destructables_filtered.closest_to(unit))

            elif not unit.is_attacking:

                if self.time < 8 * 60:
                    target = random.choice(self.enemy_start_locations)
                elif self.all_enemy_units.exists:
                    target = self.all_enemy_units.closest_to(unit)
                else:
                    target = random.choice(self.expansion_locations_list)

                unit.attack(target)

    def getTechDistanceForTrainer(self, unit: UnitTypeId, trainer: UnitTypeId) -> int:
        info = TRAIN_INFO[trainer][unit]
        structure = info.get("required_building")
        if structure is None:
            return 0
        elif self.structures(structure).ready.exists:
            return 0
        else:
            return 1 + self.getTechDistance(structure)

    def getTechDistance(self, unit: UnitTypeId) -> int:
        trainers = UNIT_TRAINED_FROM.get(unit)
        if not trainers:
            return 0
        return min((self.getTechDistanceForTrainer(unit, t) for t in trainers))

    async def get_target_position(self, target: UnitTypeId, trainer: Unit) -> Point2:
        if target in STATIC_DEFENSE[self.race]:
            townhalls = self.townhalls.ready.sorted_by_distance_to(self.start_location)
            if townhalls.exists:
                i = (self.observation.count(target) - 1) % townhalls.amount
                return townhalls[i].position.towards(self.game_info.map_center, -5)
            else:
                return self.start_location
        elif self.isStructure(target):
            if target in race_townhalls[self.race]:
                base = await self.get_next_expansion()
                if not base:
                    raise PlacementNotFound()
                return base
            elif self.townhalls.exists:
                position = self.townhalls.closest_to(self.start_location).position
                return position.towards(self.game_info.map_center, 5)
            else:
                raise PlacementNotFound()
        else:
            return trainer.position

    async def get_base_distance(self, base):

        distances_self = []
        for b in self.owned_expansions.keys():
            distance = await self.client.query_pathing(b, base) or b.distance_to(base)
            distances_self.append(distance)

        distances_enemy = []
        for b in self.enemy_start_locations:
            distance = await self.client.query_pathing(b, base) or b.distance_to(base)
            distances_enemy.append(distance)

        return max(distances_self) - min(distances_enemy)

    def has_capacity(self, unit: Unit) -> bool:
        if self.isStructure(unit.type_id):
            if unit.has_reactor:
                return len(unit.orders) < 2
            else:
                return unit.is_idle
        else:
            return True

    def isStructure(self, unit: UnitTypeId) -> bool:
        unitData = self.game_data.units.get(unit.value)
        if unitData is None:
            return False
        return IS_STRUCTURE in unitData.attributes

    def get_supply_buffer(self) -> int:

        supplyBuffer = 4

        # supplyBuffer += sum(
        #     target.cost.food
        #     for target in self.macro_targets
        #     if target.cost
        # )

        supplyBuffer += 1 * self.townhalls.amount
        # supplyBuffer += 2 * self.larva.amount
        supplyBuffer += 3 * self.observation.count(UnitTypeId.QUEEN, include_pending=False, include_planned=False)

        # supplyBuffer += 2 * self.observation.count(UnitTypeId.BARRACKS)
        # supplyBuffer += 2 * self.observation.count(UnitTypeId.FACTORY)
        # supplyBuffer += 2 * self.observation.count(UnitTypeId.STARPORT)
        # supplyBuffer += 2 * self.observation.count(UnitTypeId.GATEWAY)
        # supplyBuffer += 2 * self.observation.count(UnitTypeId.WARPGATE)
        # supplyBuffer += 2 * self.observation.count(UnitTypeId.ROBOTICSFACILITY)
        # supplyBuffer += 2 * self.observation.count(UnitTypeId.STARGATE)
        return supplyBuffer

    def getSupplyTarget(self) -> int:

        supply_have = self.observation.count(SUPPLY[self.race], include_pending=False, include_planned=False)
        if self.supply_cap == 200:
            return supply_have

        supply_pending = sum(
            provided * self.observation.count(unit)
            for unit, provided in SUPPLY_PROVIDED.items()
        )
            
        supply_buffer = self.get_supply_buffer()
        supplyNeeded = 1 + math.floor((supply_buffer - self.supply_left - supply_pending) / 8)

        return supply_have + supplyNeeded

    def createCost(self, unit: UnitTypeId):
        cost = self.calculate_cost(unit)
        food = self.calculate_supply_cost(unit)
        return Cost(cost.minerals, cost.vespene, food)

    def createCost(self, upgrade: UpgradeId):
        cost = self.calculate_cost(upgrade)
        return Cost(cost.minerals, cost.vespene, 0)

    def unitValue(self, unit: UnitTypeId) -> int:
        value = self.calculate_unit_value(unit)
        return value.minerals + 2 * value.vespene

    def enumerate_army(self):
        for unit in self.units:
            if not unit.type_id in CIVILIANS:
                yield unit

    def assignWorker(self):

        # mineral_field_to_position = dict()
        # expansion_to_mineral_fields = dict()
        # for mineral_field in self.resources.mineral_field:
        #     mineral_field_to_position[mineral_field.tag] = mineral_field.position
        #     expansion = self.position_to_expansion[mineral_field.position]
        #     expansion_to_mineral_fields.setdefault(expansion, list()).append(mineral_field)


        # expansion_to_townhall = dict()
        # for expansion in self.expansion_locations_list:
        #     townhall = self.townhalls.ready.closest_to(expansion)
        #     if expansion.distance_to(townhall) < RESOURCE_DISTANCE_THRESHOLD:
        #         expansion_to_townhall[expansion] = townhall

        gasActual = sum(g.assigned_harvesters for g in self.gas_buildings)

        exclude = { o.unit for o in self.macro_targets if o.unit }

        worker = None
        target = None

        workers = self.workers.tags_not_in(exclude)
        if workers.idle.exists:
            worker = workers.idle.random

        if worker is None:
            for gas in self.gas_buildings.ready:
                workers_gas = workers.filter(lambda w : w.order_target == gas.tag)
                if workers_gas.exists and (0 < gas.surplus_harvesters or self.gas_target + 1 <= gasActual):
                    worker = workers_gas.furthest_to(gas)
                elif gas.surplus_harvesters < 0 and gasActual + 1 <= self.gas_target:
                    target = gas

        if worker is None:

            # for w in workers:
            #     position = mineral_field_to_position.get(w.order_target)
            #     expansion = self.position_to_expansion.get(position)
            #     townhall = expansion_to_townhall.get(expansion)
            #     if (
            #         townhall is not None
            #         and (0 < townhall.surplus_harvesters or target)
            #     ):
            #         worker = w
            #         break

            for townhall in self.townhalls.ready:
                if 0 < townhall.surplus_harvesters or target is not None:
                    workers = self.workers.tags_not_in(exclude).closer_than(5, townhall)
                    if workers.exists:
                        worker = workers.random
                        break

        if worker is None:
            return

        if target is None:
            
            for townhall in self.townhalls.ready:
                if townhall.surplus_harvesters < 0:
                    minerals = (
                        self.observation.resource_by_position[position]
                        for position in self.minerals_by_expansion.get(townhall.position, [])
                        if position in self.observation.resource_by_position
                    )
                    target = min(minerals, key=lambda m : m.distance_to(worker), default=None) or self.mineral_field.closest_to(townhall)
                    break

        if target is None:
            for gas in self.gas_buildings.ready:
                if gas.surplus_harvesters < 0:
                    target = gas
                    break

        if target is None:
            return

        if worker.is_carrying_resource:
            worker.return_resource()
            worker.gather(target, queue=True)
        else:
            worker.gather(target)

    def canAffordWithReserve(self, cost: Cost, reserve: Cost) -> bool:
        if 0 < cost.minerals and self.minerals < reserve.minerals + cost.minerals:
            return False
        elif 0 < cost.vespene and self.vespene < reserve.vespene + cost.vespene:
            return False
        elif 0 < cost.food and self.supply_left < reserve.food + cost.food:
            return False
        else:
            return True

    def getMaxWorkers(self) -> int:
        workers = 0
        workers += sum((h.ideal_harvesters for h in self.townhalls.ready))
        workers += 16 * self.observation.count(UnitTypeId.HATCHERY, include_actual=False, include_planned=False)
        workers += self.gas_target
        # workers += sum((g.ideal_harvesters if g.build_progress == 1 else 3 * g.build_progress for g in self.gas_buildings))

        return int(workers)

    def createCost(self, item: Union[UnitTypeId, UpgradeId, AbilityId]) -> Cost:
        minerals = 0
        vespene = 0
        food = 0
        if item is not None:
            cost = self.calculate_cost(item)
            minerals = cost.minerals
            vespene = cost.vespene
        if item in UnitTypeId:
            food = int(self.calculate_supply_cost(item))
        return Cost(minerals, vespene, food)

    def is_blocking_base(self, position: Point2) -> bool:
        return any(
            base.distance_to(position) < 4.25
            for base in self.expansion_locations_list
        )