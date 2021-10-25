
import inspect
import math
import itertools, random
import build

from constants import POOL16, ZERG_ARMOR_UPGRADES, HATCH17, POOL12, ZERG_MELEE_UPGRADES, ZERG_RANGED_UPGRADES, ZERG_FLYER_UPGRADES, ZERG_FLYER_ARMOR_UPGRADES
from cost import Cost
from macro_target import MacroTarget
from typing import Counter, Iterable, List, Coroutine, Dict, Set, Union, Tuple

from timer import run_timed

from sc2 import AbilityId
from sc2.unit import Unit
from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.dicts.unit_train_build_abilities import TRAIN_INFO

from constants import CHANGELINGS, SUPPLY_PROVIDED
from common import CommonAI
from utils import withEquivalents, sample
from unit_counters import UNIT_COUNTERS

CREEP_RANGE = 10
CREEP_ENABLED = True

SPORE_TIMING = {
    Race.Zerg: 7 * 60,
    Race.Protoss: 4.5 * 60,
    Race.Terran: 4.5 * 60,
}

class ZergAI(CommonAI):

    def __init__(self, build_order=HATCH17, **kwargs):
        super(self.__class__, self).__init__(**kwargs)
        for step in build_order:
            self.add_macro_target(MacroTarget(step))
        self.composition = dict()
        self.timings_acc = dict()
        self.abilities = dict()
        self.inject_assigments = dict()
        self.timings_interval = 64
        self.inject_assigments_max = 5

    async def on_unit_type_changed(self, unit: Unit, previous_type: UnitTypeId):
        if unit.type_id == UnitTypeId.LAIR:
            ability = AbilityId.BEHAVIOR_GENERATECREEPON
            for overlord in self.units_by_type[UnitTypeId.OVERLORD]:
                if ability in await self.get_available_abilities(overlord):
                    overlord(ability)
                    
        # elif unit.type_id == UnitTypeId.EGG:
        #     unit_morph = UNIT_BY_TRAIN_ABILITY[unit.orders[0].ability.id]
        #     self.supply_pending += SUPPLY_PROVIDED.get(unit_morph, 0)

        await super().on_unit_type_changed(unit, previous_type)

    async def on_unit_created(self, unit: Unit):
        if unit.type_id is UnitTypeId.OVERLORD:
            if self.structures(withEquivalents(UnitTypeId.LAIR)).exists:
                unit(AbilityId.BEHAVIOR_GENERATECREEPON)
        await super(self.__class__, self).on_unit_created(unit)

    async def on_unit_took_damage(self, unit: Unit, amount_damage_taken: float):
        if unit.type_id == UnitTypeId.OVERLORD:
            enemies = self.enemy_units | self.enemy_structures
            if enemies.exists:
                enemy = enemies.closest_to(unit)
                unit.move(unit.position.towards(enemy.position, -20))
            else:
                unit.move(unit.position.towards(self.start_location, 20))
        await super(self.__class__, self).on_unit_took_damage(unit, amount_damage_taken)

    async def update_abilities(self):
        self.abilities.clear()
        for unit, abilties in zip(self.all_own_units, await self.get_available_abilities(self.all_own_units)):
            units = self.abilities.setdefault(unit.type_id, dict())
            units[unit.tag] = abilties

    async def on_step(self, iteration):

        await super().on_step(iteration)

        steps = {
            self.update_tables: 1,
            # self.update_abilities: 1,
            # self.update_pending: 1,
            self.adjustComposition: 1,
            self.micro_queens: 1,
            self.spreadCreep: 1,
            # self.moveOverlord: 1,
            self.changelingScout: 1,
            self.morphOverlords: 1,
            self.adjustGasTarget: 4,
            self.morphUnits: 1,
            self.buildGasses: 1,
            # self.trainQueens: 4,
            # self.tech: 4,
            self.upgrade: 1,
            self.expand: 1,
            self.micro: 1,
            self.assignWorker: 1,
            self.macro: 3,
        }

        steps_filtered = [s for s, m in steps.items() if iteration % m == 0]
            
        if self.timings_interval:
            timings = await run_timed(steps_filtered)
            for key, value in timings.items():
                self.timings_acc[key] = self.timings_acc.get(key, 0) + value
            if iteration % self.timings_interval == 0:
                timings_items = ((k, round(1e3 * n / self.timings_interval, 1)) for k, n in self.timings_acc.items())
                timings_sorted = dict(sorted(timings_items, key=lambda p : p[1], reverse=True))
                print(timings_sorted)
                # print(self.pending)
                # print(len(self.macroObjectives))
                self.timings_acc = {}
        else:
            for step in steps_filtered:
                result = step()
                if inspect.isawaitable(result):
                    result = await result
    def counterComposition(self, enemies: Dict[UnitTypeId, int]) -> Dict[UnitTypeId, int]:

        enemyValue = sum((self.unitValue(u) * n for u, n in enemies.items()))
        if enemyValue == 0:
            return {}, []
        weights = {
            u: sum((w * self.unitValue(v) * enemies[v] for v, w in vw.items()))
            for u, vw in UNIT_COUNTERS.items()
        }
        techTargets = []
        composition = {}
        weights = sorted(weights.items(), key=lambda p: p[1], reverse=True)
        for u, w in weights:
            if 0 < self.getTechDistance(u):
                techTargets.append(u)
                continue
            elif w <= 0 and 0 < len(composition):
                break
            composition[u] = max(1, w)
        weightSum = sum(composition.values())
        composition = {
            u: math.ceil((w  / weightSum) * (enemyValue / self.unitValue(u)))
            for u, w in composition.items()
        }

        return composition, techTargets

    def upgrade(self):

        targets = set()
        if UnitTypeId.ZERGLING in self.composition:
            targets.add(UpgradeId.ZERGLINGMOVEMENTSPEED)
            if self.count(UnitTypeId.HIVE):
                targets.add(UpgradeId.ZERGLINGATTACKSPEED)
            targets.update(self.upgradeSequence(ZERG_MELEE_UPGRADES))
            targets.update(self.upgradeSequence(ZERG_ARMOR_UPGRADES))
        if UnitTypeId.ULTRALISK in self.composition:
            targets.add(UpgradeId.CHITINOUSPLATING)
            targets.add(UpgradeId.ANABOLICSYNTHESIS)
            targets.update(self.upgradeSequence(ZERG_MELEE_UPGRADES))
            targets.update(self.upgradeSequence(ZERG_ARMOR_UPGRADES))
        if UnitTypeId.BANELING in self.composition:
            targets.add(UpgradeId.CENTRIFICALHOOKS)
            targets.update(self.upgradeSequence(ZERG_MELEE_UPGRADES))
            targets.update(self.upgradeSequence(ZERG_ARMOR_UPGRADES))
        if UnitTypeId.ROACH in self.composition:
            targets.add(UpgradeId.GLIALRECONSTITUTION)
            targets.update(self.upgradeSequence(ZERG_RANGED_UPGRADES))
            targets.update(self.upgradeSequence(ZERG_ARMOR_UPGRADES))
        if UnitTypeId.HYDRALISK in self.composition:
            targets.add(UpgradeId.EVOLVEGROOVEDSPINES)
            targets.add(UpgradeId.EVOLVEMUSCULARAUGMENTS)
            targets.update(self.upgradeSequence(ZERG_RANGED_UPGRADES))
            targets.update(self.upgradeSequence(ZERG_ARMOR_UPGRADES))
        if UnitTypeId.MUTALISK in self.composition:
            targets.update(self.upgradeSequence(ZERG_FLYER_UPGRADES))
            targets.update(self.upgradeSequence(ZERG_FLYER_ARMOR_UPGRADES))
        if UnitTypeId.CORRUPTOR in self.composition:
            targets.update(self.upgradeSequence(ZERG_FLYER_UPGRADES))
            targets.update(self.upgradeSequence(ZERG_FLYER_ARMOR_UPGRADES))
        if UnitTypeId.BROODLORD in self.composition:
            if self.count(UnitTypeId.GREATERSPIRE, include_pending=False, include_planned=False):
                targets.update(self.upgradeSequence(ZERG_FLYER_ARMOR_UPGRADES))
                targets.update(self.upgradeSequence(ZERG_MELEE_UPGRADES))
                targets.update(self.upgradeSequence(ZERG_ARMOR_UPGRADES))
        if UnitTypeId.OVERSEER in self.composition:
            targets.add(UpgradeId.OVERLORDSPEED)

        # targets = {
        #     target
        #     for target in targets
        #     # if not self.count(upgrade)
        # }

        requirements = {
            requirement
            for upgrade in itertools.chain(self.composition.keys(), targets)
            for requirement in self.get_requirements(upgrade)
        }
        targets.update(requirements)

        for target in targets:
            if not self.count(target):
                self.add_macro_target(MacroTarget(target, 0))

        # self.tech_targets.update(upgrades_want)
        # self.tech_targets.update(self.composition.keys())


    def upgradeSequence(self, upgrades) -> Iterable[UpgradeId]:
        for upgrade in upgrades:
            if upgrade not in self.state.upgrades:
                return (upgrade,)
        return tuple()

    async def changelingScout(self):
        overseers = self.units(withEquivalents(UnitTypeId.OVERSEER))
        if overseers.exists:
            overseer = overseers.random
            ability = AbilityId.SPAWNCHANGELING_SPAWNCHANGELING
            if ability in await self.get_available_abilities(overseer):
                overseer(ability)
        for chanceling_type in CHANGELINGS:
            for changeling in self.units_by_type[chanceling_type]:
                if not changeling.is_moving:
                    target = random.choice(self.expansion_locations_list)
                    changeling.move(target)

    def moveOverlord(self):

        for overlord in self.units_by_type[UnitTypeId.OVERLORD]:
            if not overlord.is_moving:
                overlord.move(self.structures.random.position)

    async def spreadCreep(self, spreader: Unit = None, numAttempts: int = 5):

        if not CREEP_ENABLED:
            return
        
        # find spreader
        if not spreader:
            tumors = self.structures(UnitTypeId.CREEPTUMORBURROWED)
            if not tumors.exists:
                return
            tumor_abilities = await self.get_available_abilities(self.structures(UnitTypeId.CREEPTUMORBURROWED))
            for tumor, abilities in zip(tumors, tumor_abilities):
                if not AbilityId.BUILD_CREEPTUMOR_TUMOR in abilities:
                    continue
                spreader = tumor
                break

        if spreader is None:
            return

        # find target
        targets = (
            *self.expansion_locations_list,
            *(r.top_center for r in self.game_info.map_ramps),
        )

        targets = [t for t in targets if not self.has_creep(t)]
        if not targets:
            return

        def weight(p):
            d = sum(t.distance_to(p) for t in self.townhalls)
            d = len(self.townhalls) * spreader.distance_to(p)
            return pow(10 + d, -2)
        
        target = sample(targets, key=weight)
        target = spreader.position.towards(target, CREEP_RANGE)

        tumorPlacement = None
        for _ in range(numAttempts):
            position = await self.find_placement(AbilityId.ZERGBUILD_CREEPTUMOR, target)
            if position is None:
                continue
            if self.isBlockingExpansion(position):
                continue
            tumorPlacement = position
            break
        if tumorPlacement is None:
            return

        spreader.build(UnitTypeId.CREEPTUMOR, tumorPlacement)

    def buildSpores(self):
        # if self.buildOrder:
        #     return
        sporeTime = {
            Race.Zerg: 8 * 60,
            Race.Protoss: 5 * 60,
            Race.Terran: 5 * 60,
        }
        if (
            sporeTime[self.enemy_race] < self.time
            and self.count(UnitTypeId.SPORECRAWLER) < self.townhalls.amount
        ):
            self.add_macro_target(MacroTarget(UnitTypeId.SPORECRAWLER))

    def enumerate_army(self):
        for unit in super().enumerate_army():
            if unit.type_id == UnitTypeId.QUEEN:
                if unit.tag in self.inject_assigments.keys():
                    continue
                elif unit in self.pending_by_type[UnitTypeId.CREEPTUMORQUEEN]:
                    continue
            yield unit

    async def micro_queens(self):

        queens_delete = set()
        for queen_tag, townhall_tag in self.inject_assigments.items():
            
            queen = self.units_by_tag.get(queen_tag)
            townhall = self.units_by_tag.get(townhall_tag)

            if not (queen and townhall):
                queens_delete.add(queen_tag)
            elif not queen.is_idle:
                pass
            elif 5 < queen.distance_to(townhall):
                queen.attack(townhall.position)
            elif 25 <= queen.energy:
                queen(AbilityId.EFFECT_INJECTLARVA, townhall)

        for queen_tag in queens_delete:
            del self.inject_assigments[queen_tag]

        queens = sorted(self.units_by_type[UnitTypeId.QUEEN], key=lambda u:u.tag)
        townhalls = sorted(self.townhalls, key=lambda u:u.tag)

        queens_unassigned = [
            queen
            for queen in queens
            if not queen.tag in self.inject_assigments.keys()
        ]

        if len(self.inject_assigments) < self.inject_assigments_max:

            townhalls_unassigned = (
                townhall
                for townhall in townhalls
                if not townhall.tag in self.inject_assigments.values()
            )

            self.inject_assigments.update({
                queen.tag: townhall.tag
                for queen, townhall in zip(queens_unassigned, townhalls_unassigned)
            })

        queens_unassigned = [
            queen
            for queen in queens
            if not queen.tag in self.inject_assigments.keys()
        ]

        for queen in queens_unassigned:

            if queen in self.pending_by_type[UnitTypeId.CREEPTUMORQUEEN]:
                pass
            # elif queen.is_attacking:
            #     pass
            elif 25 <= queen.energy:
                await self.spreadCreep(queen)

        # queens = self.units(UnitTypeId.QUEEN).sorted_by_distance_to(self.start_location)
        # townhalls = self.townhalls.sorted_by_distance_to(self.start_location)
        
        # for i, queen in enumerate(queens):
        #     if self.townhalls.amount <= i:
        #         townhall = None
        #     elif 2 < len(queens) and i == len(queens) - 1:
        #         townhall = None
        #     else:
        #         townhall = townhalls[i]
        #     if not queen.is_idle:
        #         continue
        #     elif queen.energy < 25:
        #         if townhall and 5 < queen.distance_to(townhall):
        #             queen.attack(townhall.position)
        #     elif not townhall:
        #         await self.spreadCreep(spreader=queen)
        #     elif townhall.is_ready:
        #         queen(AbilityId.EFFECT_INJECTLARVA, townhall)

    def adjustComposition(self):

        workers_target = min(80, self.getMaxWorkers())
        self.composition = {
            UnitTypeId.DRONE: workers_target,
            UnitTypeId.QUEEN: min(3 + self.inject_assigments_max, 2 * self.townhalls.amount),
        }

        if SPORE_TIMING[self.enemy_race] < self.time:
            self.composition[UnitTypeId.SPORECRAWLER] = self.townhalls.ready.amount
        if 2 * SPORE_TIMING[self.enemy_race] < self.time:
            self.composition[UnitTypeId.SPINECRAWLER] = self.townhalls.ready.amount

        # supply_left = 200 - self.composition[UnitTypeId.DRONE] - 2 * self.composition[UnitTypeId.QUEEN]

        if self.townhalls.amount <= 2:
            pass
        elif self.townhalls.amount <= 3:
            # self.composition[UnitTypeId.ZERGLING] = 12
            self.composition[UnitTypeId.ROACH] = workers_target / 8
        elif (
            not self.count(UnitTypeId.LAIR, include_pending=False, include_planned=False)
            and not self.count(UnitTypeId.HIVE, include_pending=False, include_planned=False)
        ):
            self.composition[UnitTypeId.ROACH] = workers_target / 4
        elif not self.count(UnitTypeId.HIVE, include_pending=False, include_planned=False):
            self.composition[UnitTypeId.OVERSEER] = 1
            self.composition[UnitTypeId.HYDRALISK] = workers_target / 4
            self.composition[UnitTypeId.ROACH] = workers_target / 2
            # if UpgradeId.CENTRIFICALHOOKS in self.state.upgrades:
            #     self.composition[UnitTypeId.BANELING] = 40
            # else:
            #     self.composition[UnitTypeId.BANELING] = 0
                
        else:
            self.composition[UnitTypeId.OVERSEER] = 2
            self.composition[UnitTypeId.HYDRALISK] = 30
            self.composition[UnitTypeId.ROACH] = 30
            # self.composition[UnitTypeId.RAVAGER] = 40
            # self.composition[UnitTypeId.ZERGLING] = 40
            # self.composition[UnitTypeId.BANELING] = 40
            if self.count(UnitTypeId.GREATERSPIRE, include_pending=False, include_planned=False):
                self.composition[UnitTypeId.CORRUPTOR] = 10
                self.composition[UnitTypeId.BROODLORD] = 10
            else:
                self.composition[UnitTypeId.BROODLORD] = 0

    def adjustGasTarget(self):

        cost_zero = Cost(0, 0, 0)
        cost_sum = sum((target.cost or cost_zero for target in self.macro_targets), cost_zero)

        minerals = max(0, cost_sum.minerals - self.minerals)
        vespene = max(0, cost_sum.vespene - self.vespene)
        gasRatio = vespene / max(1, vespene + minerals)
        self.gas_target = gasRatio * self.count(UnitTypeId.DRONE, include_pending=False, include_planned=False)
        # self.gasTarget = 3 * int(self.gasTarget / 3)
        # print(self.gasTarget)

    def buildGasses(self):
        gas_depleted = self.gas_buildings.filter(lambda g : not g.has_vespene).amount
        gas_have = self.count(UnitTypeId.EXTRACTOR) - gas_depleted
        # gas_max = sum(1 for g in self.get_owned_geysers())
        # gas_want = min(gas_max, int(self.gas_target / 3))
        gas_want = math.ceil(self.gas_target / 3)
        if gas_have < gas_want:
            self.add_macro_target(MacroTarget(UnitTypeId.EXTRACTOR, 1))

    def morphOverlords(self):
        if 200 <= self.supply_cap:
            return
        supply_pending = sum(
            provided * self.count(unit, include_actual=False)
            for unit, provided in SUPPLY_PROVIDED.items()
        )
        if 200 <= self.supply_cap + supply_pending:
            return
        if self.supply_left + supply_pending < self.get_supply_buffer():
            self.add_macro_target(MacroTarget(UnitTypeId.OVERLORD, 1))

    def expand(self, saturation_target: float = 0.9):
        
        worker_max = self.getMaxWorkers()
        if (
            not self.count(UnitTypeId.HATCHERY, include_actual=False)
            and not self.townhalls.not_ready.exists
            and saturation_target * worker_max <= self.count(UnitTypeId.DRONE, include_planned=False)
        ):
            self.add_macro_target(MacroTarget(UnitTypeId.HATCHERY, 1))

    def morphUnits(self):
        

        if self.supply_used == 200:
            return

        composition_have = {
            unit: self.count(unit)
            for unit in self.composition.keys()
        }

        composition_missing = {
            unit: count - composition_have[unit]
            for unit, count in self.composition.items()
        }

        targets = [
            MacroTarget(unit, -random.random() * composition_have[unit] / self.composition[unit])
            # MacroTarget(unit, -random.random())
            for unit, count in composition_missing.items()
            if 0 < count
            # for i in range(count)
        ]

        for target in targets:
            self.add_macro_target(target)