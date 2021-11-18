
from collections import defaultdict
import inspect
import math
import itertools, random
import build
import numpy as np
from typing import Counter, Iterable, List, Coroutine, Dict, Set, Union, Tuple, Optional
from itertools import chain
from sc2 import unit

from sc2.ids.ability_id import AbilityId
from sc2.ids.buff_id import BuffId
from sc2.unit import Unit
from sc2.data import Race, race_townhalls, race_worker, Result
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.dicts.unit_train_build_abilities import TRAIN_INFO
from sc2.position import Point2
from suntzu.resources.resource_group import T
from suntzu.strategies.pool12_allin import Pool12AllIn
from suntzu.unit_counters import UNIT_COUNTERS

from .strategies.gasless import GasLess
from .strategies.roach_rush import RoachRush
from .strategies.hatch_first import HatchFirst
from .strategies.pool12 import Pool12
from .strategies.pool12_allin import Pool12AllIn
from .strategies.zerg_strategy import ZergStrategy
from .timer import run_timed
from .constants import CHANGELINGS, CREEP_ABILITIES, SUPPLY_PROVIDED
from .common import CommonAI, PerformanceMode
from .utils import armyValue, center, sample, unitValue
from .constants import BUILD_ORDER_PRIORITY, WITH_TECH_EQUIVALENTS, REQUIREMENTS, ZERG_ARMOR_UPGRADES, ZERG_MELEE_UPGRADES, ZERG_RANGED_UPGRADES, ZERG_FLYER_UPGRADES, ZERG_FLYER_ARMOR_UPGRADES
from .cost import Cost
from .macro_plan import MacroPlan

CREEP_RANGE = 10
CREEP_ENABLED = True

SPORE_TRIGGERS: Dict[Race, Set[UnitTypeId]] = {
    Race.Zerg: {
        UnitTypeId.DRONEBURROWED,
        UnitTypeId.QUEENBURROWED,
        UnitTypeId.ZERGLINGBURROWED,
        UnitTypeId.BANELINGBURROWED,
        UnitTypeId.ROACHBURROWED,
        UnitTypeId.RAVAGERBURROWED,
        UnitTypeId.HYDRALISKBURROWED,
        UnitTypeId.LURKERMP,
        UnitTypeId.LURKERMPBURROWED,
        UnitTypeId.INFESTORBURROWED,
        UnitTypeId.SWARMHOSTBURROWEDMP,
        UnitTypeId.ULTRALISKBURROWED,
        UnitTypeId.MUTALISK,
    },
    Race.Protoss: {
        UnitTypeId.STARGATE,
        UnitTypeId.ORACLE,
        UnitTypeId.VOIDRAY,
        UnitTypeId.CARRIER,
        UnitTypeId.TEMPEST,
        UnitTypeId.PHOENIX,
    },
    Race.Terran: {
        UnitTypeId.STARPORT,
        UnitTypeId.STARPORTFLYING,
        UnitTypeId.VIKING,
        UnitTypeId.MEDIVAC,
        UnitTypeId.LIBERATOR,
        UnitTypeId.RAVEN,
        UnitTypeId.BANSHEE,
        UnitTypeId.BATTLECRUISER,
    },
}
SPORE_TRIGGERS[Race.Random] = set((v for vs in SPORE_TRIGGERS.values() for v in vs))

TIMING_INTERVAL = 64

class ZergAI(CommonAI):

    def __init__(self, strategy: ZergStrategy = None, **kwargs):
        super(self.__class__, self).__init__(**kwargs)

        strategy = strategy or HatchFirst()

        self.extractor_trick_enabled = False
        self.strategy: ZergStrategy = strategy
        self.composition: Dict[UnitTypeId, int] = dict()
        self.timings_acc = dict()
        self.army_queens: Set[int] = set()
        self.creep_queens: Set[int] = set()
        self.inject_queens: Set[int] = set()
        self.creep_area_min: np.ndarray = None
        self.creep_area_max: np.ndarray = None
        self.inactive_tumors: Set[int] = set()
        self.creep_coverage: float = 0
        self.creep_tile_count: int = 1
        self.build_spores: bool = False
        self.blocked_base_detectors: Dict[Point2, int] = dict()
        self.scout_overlord: Optional[int] = None
        self.enemy_base_count: int = 1

    def counter_composition(self, enemies: Iterable[Unit]) -> Dict[UnitTypeId, int]:

        def value(unit: UnitTypeId):
            cost = self.cost[unit]
            return cost.minerals + cost.vespene

        if not any(enemies):
            return {
                UnitTypeId.ZERGLING: 1,
                UnitTypeId.OVERSEER: 1
            }

        enemies_by_type = defaultdict(lambda: set())
        for enemy in enemies:
            enemies_by_type[enemy.type_id].add(enemy)

        enemy_cost = sum(
            (self.cost[enemy_type] * len(n)
            for enemy_type, n in enemies_by_type.items()
            if enemy_type in self.cost)
        , Cost(0, 0, 0))
        enemy_value = enemy_cost.minerals + enemy_cost.vespene

        weights = {
            unit: sum(
                w * len(enemies_by_type[e])
                for e, w in UNIT_COUNTERS[unit].items()
            )
            for unit in UNIT_COUNTERS.keys()
        }

        weights = sorted(weights.items(),
            key = lambda p : p[1],
            reverse = True)

        best_unit, _ = weights[0]
        best_can_build = next(
            (u for u, _ in weights if not any(self.get_missing_requirements(u, include_pending=False, include_planned=False))),
            None)

        if best_unit == best_can_build:
            return {
                best_unit: math.ceil(enemy_value / value(best_unit))
            }
        else:
            return {
                best_unit: 0,
                best_can_build: math.ceil(enemy_value / value(best_can_build))
            }

    def destroy_destructables(self):
        return self.strategy.destroy_destructables(self)

    def extractor_trick(self):
        if not self.extractor_trick_enabled:
            return
        if self.supply_used < self.supply_cap:
            return
        extractor = next((
            e
            for e in self.pending_by_type[UnitTypeId.EXTRACTOR]
            if e.type_id == UnitTypeId.EXTRACTOR
        ), None)
        if not extractor:
            return
        extractor(AbilityId.CANCEL)
        self.extractor_trick_enabled = False
        

    async def on_start(self):

        self.update_tables()
        # if not self.strategy:
        #     strategy_types = [HatchFirst]
        #     if self.enemy_race == Race.Protoss:
        #         strategy_types.append(Pool12)
        #     strategy_type = sample(strategy_types)
        #     self.strategy = strategy_type()
        # self.tags.append(self.strategy.name())

        for step in self.strategy.build_order():
            self.add_macro_plan(MacroPlan(step, priority=BUILD_ORDER_PRIORITY))

        await super().on_start()

        self.creep_area_min = np.array([
            min(p[i] for p in self.expansion_locations_list)
            for i in range(2)
        ])
        self.creep_area_max = np.array([
            max(p[i] for p in self.expansion_locations_list)
            for i in range(2)
        ])

        self.creep_tile_count = np.sum(self.game_info.pathing_grid.data_numpy)

        # first_overlord = next(iter(self.actual_by_type[UnitTypeId.OVERLORD]))
        # enemy_natural = self.bases[-2].position
        # enemy_third = self.bases[-3].position
        # first_overlord.move(enemy_natural.towards(first_overlord.position, first_overlord.sight_range))
        # first_overlord.move(enemy_third, queue=True)


    async def on_unit_type_changed(self, unit: Unit, previous_type: UnitTypeId):
        if unit.type_id == UnitTypeId.LAIR:
            ability = AbilityId.BEHAVIOR_GENERATECREEPON
            overlords = self.actual_by_type[UnitTypeId.OVERLORD]
            for overlord in overlords:
                if not overlord:
                    continue
                if ability in await self.get_available_abilities(overlord):
                    overlord(ability)
        return await super().on_unit_type_changed(unit, previous_type)

    async def on_unit_created(self, unit: Unit):
        if unit.type_id is UnitTypeId.OVERLORD:
            if self.structures(WITH_TECH_EQUIVALENTS[UnitTypeId.LAIR]).exists:
                unit(AbilityId.BEHAVIOR_GENERATECREEPON)
        return await super(self.__class__, self).on_unit_created(unit)

    async def on_unit_took_damage(self, unit: Unit, amount_damage_taken: float):
        if unit.type_id == UnitTypeId.OVERLORD:
            enemies = self.enemy_units | self.enemy_structures
            if enemies.exists:
                enemy = enemies.closest_to(unit)
                unit.move(unit.position.towards(enemy.position, -20))
            else:
                unit.move(unit.position.towards(self.start_location, 20))
        return await super(self.__class__, self).on_unit_took_damage(unit, amount_damage_taken)

    async def transfuse(self):

        def priority(queen: Unit, target: Unit) -> float:
            if queen.tag == target.tag:
                return 0
            if not queen.in_ability_cast_range(ability, target):
                return 0
            if BuffId.TRANSFUSION in target.buffs:
                return 0
            if target.health_max <= target.health + 75:
                return 0
            priority = 1
            priority *= 10 + unitValue(target)
            return priority

        ability = AbilityId.TRANSFUSION_TRANSFUSION
        queens = [
            self.unit_by_tag[t]
            for t in self.army_queens
            if t in self.unit_by_tag
        ]
        if not queens:
            return
        queens_abilities = await self.get_available_abilities(queens)

        for queen, abilities in zip(queens, queens_abilities):

            if ability not in abilities:
                continue

            target = max(self.all_own_units, key = lambda t : priority(queen, t))
            if priority(queen, target) <= 0:
                continue

            queen(ability, target=target)

    async def corrosive_bile(self):

        def target_priority(target):
            priority = 10 + max(target.ground_dps, target.air_dps)
            priority /= 100 + target.health + target.shield
            priority /= 2 + target.movement_speed
            return priority

        ability = AbilityId.EFFECT_CORROSIVEBILE
        ability_data = self.game_data.abilities[ability.value]._proto
        ravagers = list(self.actual_by_type[UnitTypeId.RAVAGER])
        if not ravagers:
            return
        ravager_abilities = await self.get_available_abilities(ravagers)
        for ravager, abilities in zip(ravagers, ravager_abilities):
            if ability not in abilities:
                continue
            targets = (
                target
                for target in chain(self.all_enemy_units, self.destructables_fixed)
                if ravager.distance_to(target) <= ravager.radius + ability_data.cast_range
            )
            target: Unit = max(targets, key=target_priority, default=None)
            if not target:
                continue
            predicted_position = target.position
            previous_position = self.enemy_positions.get(target.tag)
            if previous_position:
                velocity = 22.4 * (target.position - previous_position) / (self.game_step)
                if velocity.length < 2:
                    predicted_position = target.position + 2.5 * velocity
            ravager(ability, target=predicted_position)

    def update_strategy(self):
        self.strategy.update(self)

    def get_gas_target(self):
        gas_target = self.strategy.gas_target(self)
        if gas_target == None:
            gas_target = super().get_gas_target()
        return gas_target

    async def on_step(self, iteration):


        await super(self.__class__, self).on_step(iteration)

        if iteration == 0:
            return

        steps = self.strategy.steps(self)

        steps_filtered = [s for s, m in steps.items() if iteration % m == 0]
            
        if self.debug:
            timings = await run_timed(steps_filtered)
            for key, value in timings.items():
                self.timings_acc[key] = self.timings_acc.get(key, 0) + value
            if iteration % TIMING_INTERVAL == 0:
                timings_items = ((k, round(1e3 * n / TIMING_INTERVAL, 1)) for k, n in self.timings_acc.items())
                timings_sorted = dict(sorted(timings_items, key=lambda p : p[1], reverse=True))
                print(timings_sorted)
                self.timings_acc = {}
        else:
            for step in steps_filtered:
                result = step()
                if inspect.isawaitable(result):
                    result = await result

    def draw_debug(self):
        self.client.debug_text_screen(f'Creep Coverage: {round(100 * self.creep_coverage)}%', (0.01, 0.02))
        return super().draw_debug()

    def upgrades_by_unit(self, unit: UnitTypeId) -> Iterable[UpgradeId]:
        if unit == UnitTypeId.ZERGLING:
            return chain(
                (UpgradeId.ZERGLINGMOVEMENTSPEED,),
                # (UpgradeId.ZERGLINGMOVEMENTSPEED, UpgradeId.ZERGLINGATTACKSPEED),
                # self.upgrade_sequence(ZERG_MELEE_UPGRADES),
                # self.upgrade_sequence(ZERG_ARMOR_UPGRADES),
            )
        elif unit == UnitTypeId.ULTRALISK:
            return chain(
                (UpgradeId.CHITINOUSPLATING, UpgradeId.ANABOLICSYNTHESIS),
                self.upgrade_sequence(ZERG_MELEE_UPGRADES),
                self.upgrade_sequence(ZERG_ARMOR_UPGRADES),
            )
        elif unit == UnitTypeId.BANELING:
            return chain(
                (UpgradeId.CENTRIFICALHOOKS,),
                self.upgrade_sequence(ZERG_MELEE_UPGRADES),
                self.upgrade_sequence(ZERG_ARMOR_UPGRADES),
            )
        elif unit == UnitTypeId.ROACH:
            return chain(
                (UpgradeId.GLIALRECONSTITUTION,),
                self.upgrade_sequence(ZERG_RANGED_UPGRADES),
                self.upgrade_sequence(ZERG_ARMOR_UPGRADES),
            )
        elif unit == UnitTypeId.HYDRALISK:
            return chain(
                (UpgradeId.EVOLVEGROOVEDSPINES, UpgradeId.EVOLVEMUSCULARAUGMENTS),
                self.upgrade_sequence(ZERG_RANGED_UPGRADES),
                self.upgrade_sequence(ZERG_ARMOR_UPGRADES),
            )
        elif unit == UnitTypeId.QUEEN:
            return chain(
                # self.upgradeSequence(ZERG_RANGED_UPGRADES),
                # self.upgradeSequence(ZERG_ARMOR_UPGRADES),
            )
        elif unit == UnitTypeId.MUTALISK:
            return chain(
                self.upgrade_sequence(ZERG_FLYER_UPGRADES),
                self.upgrade_sequence(ZERG_FLYER_ARMOR_UPGRADES),
            )
        elif unit == UnitTypeId.CORRUPTOR:
            return chain(
                self.upgrade_sequence(ZERG_FLYER_UPGRADES),
                self.upgrade_sequence(ZERG_FLYER_ARMOR_UPGRADES),
            )
        elif unit == UnitTypeId.BROODLORD:
            return chain(
                self.upgrade_sequence(ZERG_FLYER_ARMOR_UPGRADES),
                self.upgrade_sequence(ZERG_MELEE_UPGRADES),
                self.upgrade_sequence(ZERG_ARMOR_UPGRADES),
            )
        # elif unit == UnitTypeId.OVERSEER:
        #     return (UpgradeId.OVERLORDSPEED,)
        else:
            return []

    def make_tech(self):
        upgrades = chain(*(self.upgrades_by_unit(unit) for unit in self.composition))
        upgrades = list(dict.fromkeys(upgrades))
        upgrades = [u for u in upgrades if self.strategy.filter_upgrade(self, u)]
        targets = (
            *chain(*(REQUIREMENTS[unit] for unit in self.composition)),
            *chain(*(REQUIREMENTS[upgrade] for upgrade in upgrades)),
            *upgrades,
        )
        targets = list(dict.fromkeys(targets))
        for target in targets:
            equivalents =  WITH_TECH_EQUIVALENTS.get(target, { target })
            if sum(self.count(t) for t in equivalents) == 0:
                self.add_macro_plan(MacroPlan(target))

    def upgrade_sequence(self, upgrades) -> Iterable[UpgradeId]:
        for upgrade in upgrades:
            if not self.count(upgrade, include_planned=False):
                return (upgrade,)
        return tuple()

    async def scout(self):

        overseers = self.units(WITH_TECH_EQUIVALENTS[UnitTypeId.OVERSEER])
        if overseers.exists:
            ability = AbilityId.SPAWNCHANGELING_SPAWNCHANGELING
            overseers_abilities = await self.get_available_abilities(overseers)
            for overseer, abilities in zip(overseers, overseers_abilities):
                if ability in abilities:
                    overseer(ability)

        # free overseers once base is no longer blocked
        for base in self.bases:
            if base.blocked_since:
                detector_tag = self.blocked_base_detectors.get(base.position)
                detector = self.unit_by_tag.get(detector_tag)
                if not detector:
                    # assign overseer
                    detector = min(
                        (unit for unit in self.enumerate_army() if unit.is_detector),
                        key = lambda u : u.distance_to(base.position),
                        default = None)
                    if not detector:
                        continue
                    self.blocked_base_detectors[base.position] = detector.tag
                # move towards base
                target_distance = detector.detect_range - 3
                if target_distance < detector.distance_to(base.position):
                    detector.move(base.position.towards(detector, target_distance))
            else:
                # reset once no longer blocked
                if base.position in self.blocked_base_detectors:
                    del self.blocked_base_detectors[base.position]

        changelings = [
            c
            for t in CHANGELINGS
            for c in self.actual_by_type[t]
        ]
        for changeling in changelings:
            if not changeling:
                continue
            if not changeling.is_moving:
                target = random.choice(self.expansion_locations_list)
                changeling.move(target)


        if self.enemy_base_count + 1 < len(self.bases):
            base = self.bases[-self.enemy_base_count - 1]
            enemy_townhalls = [th
                for t in race_townhalls[self.enemy_race]
                for th in self.enemies_by_type[t]]
            if any(th.position == base.position for th in enemy_townhalls):
                self.enemy_base_count += 1
            else:
                overlord = self.unit_by_tag.get(self.scout_overlord)
                if not overlord:
                    overlord = next(iter(self.actual_by_type[UnitTypeId.OVERLORD]), None)
                if overlord and overlord.is_idle:
                    self.scout_overlord = overlord.tag
                    if overlord.sight_range < overlord.distance_to(base.position):
                        overlord.move(base.position.towards(overlord, overlord.sight_range))
                        overlord.move(base.position.towards(self.enemy_start_locations[0], -overlord.sight_range), queue=True)

    async def spread_creep(self):

        spreaders = [
            tumor 
            for tumor in self.actual_by_type[UnitTypeId.CREEPTUMORBURROWED]
            if tumor.tag not in self.inactive_tumors
        ]

        queens = [self.unit_by_tag[t] for t in self.creep_queens]

        for queen in queens:
            if (
                not self.has_creep(queen.position)
                and not queen.is_moving
                and self.townhalls.ready
            ):
                townhall = self.townhalls.ready.closest_to(queen)
                queen.move(townhall)
            elif (
                25 <= queen.energy
                and not any(o.ability.exact_id == AbilityId.BUILD_CREEPTUMOR_QUEEN for o in queen.orders)
            ):
                spreaders.append(queen)
        
        valid_map = np.logical_and(self.state.creep.data_numpy == 0, self.game_info.pathing_grid.data_numpy == 1)
        valid_map = np.transpose(valid_map)

        self.creep_coverage = np.sum(self.state.creep.data_numpy) / self.creep_tile_count
        if .95 < self.creep_coverage:
            return 

        if not spreaders:
            return

        spreader_abilities = await self.get_available_abilities(spreaders)
        for spreader, abilities in zip(spreaders, spreader_abilities):
            ability = CREEP_ABILITIES[spreader.type_id]
            if not ability in abilities:
                continue
            self.spread_creep_single(spreader, valid_map)


    def spread_creep_single(self, spreader: Unit, valid_map: np.ndarray, num_attempts: int = 1):

        start_position = spreader.position

        if (
            spreader.type_id == UnitTypeId.QUEEN
            # and self.count(UnitTypeId.CREEPTUMORBURROWED) == 0
        ):
            forward_base = max(
                self.townhalls.ready,
                key = lambda th : self.distance_map[th.position.rounded],
                default = None)
            if forward_base:
                start_position = forward_base.position 

        target = None
        for _ in range(num_attempts):
            angle = np.random.uniform(0, 2 * math.pi)
            distance = np.random.exponential(CREEP_RANGE)
            target_test = start_position + distance * Point2((math.cos(angle), math.sin(angle)))
            target_test = np.clip(target_test, self.creep_area_min, self.creep_area_max)
            target_test = Point2(target_test).rounded
            if not valid_map[target_test]:
                continue
            target = target_test
            break

        if not target:
            return

        max_range = CREEP_RANGE
        if spreader.type_id == UnitTypeId.QUEEN:
            max_range = 3 * CREEP_RANGE

        for i in range(max_range, 0, -1):
            position = spreader.position.towards(target, i)
            if not self.has_creep(position):
                continue
            if not self.is_visible(position):
                continue
            if self.blocked_base(position):
                continue
            spreader.build(UnitTypeId.CREEPTUMOR, position)
            break

        # if spreader.type_id == UnitTypeId.CREEPTUMORBURROWED:
        #     self.inactive_tumors.add(spreader.tag)

    def enumerate_army(self):
        for unit in super().enumerate_army():
            if unit.type_id == UnitTypeId.QUEEN:
                if unit.tag not in self.army_queens:
                    pass
                elif any(o.ability.exact_id == AbilityId.TRANSFUSION_TRANSFUSION for o in unit.orders):
                    pass
                else:
                    yield unit
            elif unit.type_id == UnitTypeId.RAVAGER:
                if any(o.ability.exact_id == AbilityId.EFFECT_CORROSIVEBILE for o in unit.orders):
                    pass
                else:
                    yield unit
            elif unit.type_id == UnitTypeId.OVERSEER:
                if unit.tag in self.blocked_base_detectors.values():
                    pass
                else:
                    yield unit
            else:
                yield unit

    async def manage_queens(self):

        queens = sorted(
            self.actual_by_type[UnitTypeId.QUEEN],
            key=lambda q:q.tag)

        macro_queen_count = math.ceil((1 - self.threat_level) * len(queens))
        macro_queen_count = min(6, self.townhalls.amount, macro_queen_count)
        creep_queen_count = 1 if 2 < macro_queen_count else 0
        # creep_queen_count = min(1, macro_queen_count)

        creep_queens = queens[0:creep_queen_count]
        inject_queens = queens[creep_queen_count:macro_queen_count]
        army_queens = queens[macro_queen_count:]

        self.creep_queens = { q.tag for q in creep_queens }
        self.army_queens = { q.tag for q in army_queens }
        self.inject_queens = { q.tag for q in inject_queens }

        for queen, base in zip(inject_queens, (b for b in self.bases if b.townhall)):
            townhall = self.unit_by_tag.get(base.townhall)
            if not townhall:
                continue
            if 7 < queen.position.distance_to(townhall.position):
                queen.attack(townhall.position)
            elif 25 <= queen.energy:
                queen(AbilityId.EFFECT_INJECTLARVA, townhall)

    def update_composition(self):
        self.composition = self.strategy.composition(self)

    def make_defenses(self):

        for unit_type in SPORE_TRIGGERS[self.enemy_race]:
            if any(self.enemies_by_type[unit_type]):
                self.build_spores = True

        if self.build_spores:
            for base in self.bases:
                base.defensive_targets = {
                    UnitTypeId.SPORECRAWLER: 1,
                }

    def morph_overlords(self):
        if 200 <= self.supply_cap:
            return
        supply_pending = sum(
            provided * self.count(unit, include_actual=False)
            for unit, provided in SUPPLY_PROVIDED.items()
        )
        if 200 <= self.supply_cap + supply_pending:
            return
        supply_buffer = 3
        supply_buffer += 3 * self.townhalls.amount
        supply_buffer += 3 * len(self.inject_queens)
        # supply_buffer += self.larva.amount
        if self.supply_left + supply_pending < supply_buffer:
            self.add_macro_plan(MacroPlan(UnitTypeId.OVERLORD, priority=1))

    def expand(self):

        worker_max = self.get_max_harvester()
        saturation = self.count(UnitTypeId.DRONE, include_planned=False) / max(1, worker_max)
        priority = -1.75 + saturation
        
        if not self.count(UnitTypeId.HATCHERY, include_actual=False):
            if any(self.planned_by_type[UnitTypeId.HATCHERY]):
                for plan in self.planned_by_type[UnitTypeId.HATCHERY]:
                    if plan.priority == BUILD_ORDER_PRIORITY:
                        pass
                    else:
                        plan.priority = priority
            else:
                plan = MacroPlan(UnitTypeId.HATCHERY)
                plan.priority = priority
                plan.max_distance = 0
                self.add_macro_plan(plan)