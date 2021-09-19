
import math
import itertools, random

from s2clientprotocol.error_pb2 import QueueIsFull
from s2clientprotocol.sc2api_pb2 import Macro
from macro_objective import MacroObjective
from s2clientprotocol.raw_pb2 import Unit
from typing import List, Coroutine, Dict, Union

from cost import Cost
from sc2 import AbilityId
from sc2 import unit
from sc2.game_data import AbilityData
from sc2.units import Units
from build_order import Hatch16, Pool12, Pool16
from timer import run_timed

from sc2.data import Race
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.dicts.unit_trained_from import UNIT_TRAINED_FROM
from sc2.dicts.unit_train_build_abilities import TRAIN_INFO

from common import CommonAI
from utils import CHANGELINGS, armyValue, filterArmy, makeUnique, withEquivalents
from unit_counters import UNIT_COUNTERS

MELEE_UPGRADES = [
    UpgradeId.ZERGMELEEWEAPONSLEVEL1,
    UpgradeId.ZERGMELEEWEAPONSLEVEL2,
    UpgradeId.ZERGMELEEWEAPONSLEVEL3,
]

ARMOR_UPGRADES = [
    UpgradeId.ZERGGROUNDARMORSLEVEL1,
    UpgradeId.ZERGGROUNDARMORSLEVEL2,
    UpgradeId.ZERGGROUNDARMORSLEVEL3,
]

RANGED_UPGRADES = [
    UpgradeId.ZERGMISSILEWEAPONSLEVEL1,
    UpgradeId.ZERGMISSILEWEAPONSLEVEL2,
    UpgradeId.ZERGMISSILEWEAPONSLEVEL3,
]

FLYER_UPGRADES = [
    UpgradeId.ZERGFLYERWEAPONSLEVEL1,
    UpgradeId.ZERGFLYERWEAPONSLEVEL2,
    UpgradeId.ZERGFLYERWEAPONSLEVEL3,
]

FLYER_ARMOR_UPGRADES = [
    UpgradeId.ZERGFLYERARMORSLEVEL1,
    UpgradeId.ZERGFLYERARMORSLEVEL2,
    UpgradeId.ZERGFLYERARMORSLEVEL3,
]

HATCH_FIRST = [
    UnitTypeId.DRONE,
    UnitTypeId.OVERLORD,
    UnitTypeId.DRONE,
    UnitTypeId.DRONE,
    UnitTypeId.DRONE,
    UnitTypeId.HATCHERY,
    UnitTypeId.DRONE,
    UnitTypeId.DRONE,
    UnitTypeId.DRONE,
    UnitTypeId.EXTRACTOR,
    UnitTypeId.SPAWNINGPOOL,
    UnitTypeId.DRONE,
    UnitTypeId.DRONE,
    UnitTypeId.DRONE,
    UnitTypeId.DRONE,
    UnitTypeId.OVERLORD,
    UpgradeId.ZERGLINGMOVEMENTSPEED,
    UnitTypeId.QUEEN,
    UnitTypeId.QUEEN,
    UnitTypeId.ZERGLING,
    UnitTypeId.ZERGLING,
]

POOL_FIRST = [
    UnitTypeId.DRONE,
    UnitTypeId.OVERLORD,
    UnitTypeId.DRONE,
    UnitTypeId.DRONE,
    UnitTypeId.DRONE,
    UnitTypeId.SPAWNINGPOOL,
    UnitTypeId.DRONE,
    UnitTypeId.DRONE,
    UnitTypeId.HATCHERY,
    UnitTypeId.DRONE,
    UnitTypeId.ZERGLING,
    UnitTypeId.ZERGLING,
    UnitTypeId.ZERGLING,
    UnitTypeId.QUEEN,
    UnitTypeId.EXTRACTOR,
    UnitTypeId.OVERLORD,
]

class ZergAI(CommonAI):

    def __init__(self, **kwargs):
        super(self.__class__, self).__init__(**kwargs)
        self.composition = {}
        self.buildOrder = POOL_FIRST
        self.goLair = False
        self.goHive = False
        self.macroObjectives = []
        self.gasTarget = 0
        self.timings_acc = {}
        self.timings_interval = 64

    async def on_unit_created(self, unit: Unit):
        if unit.type_id is UnitTypeId.OVERLORD and self.structures(withEquivalents(UnitTypeId.LAIR)).exists:
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

    async def on_step(self, iteration):

        steps = {
            self.followBuildOrder: 1,
            self.adjustComposition: 4,
            self.microQueens: 1,
            self.spreadCreep: 16,
            self.moveOverlord: 4,
            self.changelingScout: 16,
            self.morphOverlords: 16,
            self.adjustGasTarget: 16,
            self.morphUnits: 8,
            self.buildGasses: 16,
            self.trainQueens: 4,
            self.techBuildings: 16,
            self.upgrade: 16,
            self.techUp: 16,
            self.expandIfNecessary: 8,
            # self.buildSpores: 1,
            self.micro: 1,
            self.assignWorker: 4,
            self.reachMacroObjective: 4,
        }

        steps_filtered = [s for s, m in steps.items() if iteration % m == 0]
        timings = await run_timed(steps_filtered)
            
        for key, value in timings.items():
            self.timings_acc[key] = self.timings_acc.get(key, 0) + value

        if iteration % self.timings_interval == 0:
            timings_items = ((k, round(1e3 * n / self.timings_interval, 1)) for k, n in self.timings_acc.items())
            timings_sorted = dict(sorted(timings_items, key=lambda p : p[1], reverse=True))
            print(dict(timings_sorted))
            self.timings_acc = {}

    def followBuildOrder(self):
        if self.buildOrder and not self.macroObjectives:
            target = self.buildOrder.pop(0)
            self.macroObjectives.append(MacroObjective(target))

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
        if self.buildOrder:
            return
        targets = []
        if UnitTypeId.ZERGLING in self.composition:
            targets.append(UpgradeId.ZERGLINGMOVEMENTSPEED)
            if self.goHive:
                targets.append(UpgradeId.ZERGLINGATTACKSPEED)
            targets.extend(self.upgradeSequence(MELEE_UPGRADES))
            targets.extend(self.upgradeSequence(ARMOR_UPGRADES))
        if UnitTypeId.ULTRALISK in self.composition:
            targets.append(UpgradeId.CHITINOUSPLATING)
            targets.append(UpgradeId.ANABOLICSYNTHESIS)
            targets.extend(self.upgradeSequence(MELEE_UPGRADES))
            targets.extend(self.upgradeSequence(ARMOR_UPGRADES))
        if UnitTypeId.BANELING in self.composition:
            targets.append(UpgradeId.CENTRIFICALHOOKS)
            targets.extend(self.upgradeSequence(MELEE_UPGRADES))
            targets.extend(self.upgradeSequence(ARMOR_UPGRADES))
        if UnitTypeId.ROACH in self.composition:
            targets.append(UpgradeId.GLIALRECONSTITUTION)
            targets.extend(self.upgradeSequence(RANGED_UPGRADES))
            targets.extend(self.upgradeSequence(ARMOR_UPGRADES))
        if UnitTypeId.HYDRALISK in self.composition:
            targets.append(UpgradeId.EVOLVEGROOVEDSPINES)
            targets.append(UpgradeId.EVOLVEMUSCULARAUGMENTS)
            targets.extend(self.upgradeSequence(RANGED_UPGRADES))
            targets.extend(self.upgradeSequence(ARMOR_UPGRADES))
        if UnitTypeId.MUTALISK in self.composition:
            targets.extend(self.upgradeSequence(FLYER_UPGRADES))
            targets.extend(self.upgradeSequence(FLYER_ARMOR_UPGRADES))
        if UnitTypeId.CORRUPTOR in self.composition:
            targets.extend(self.upgradeSequence(FLYER_UPGRADES))
            targets.extend(self.upgradeSequence(FLYER_ARMOR_UPGRADES))
        if UnitTypeId.BROODLORD in self.composition:
            targets.extend(self.upgradeSequence(FLYER_ARMOR_UPGRADES))
            # targets.extend(self.upgradeSequence(MELEE_UPGRADES))
            # targets.extend(self.upgradeSequence(ARMOR_UPGRADES))
        if UnitTypeId.OVERSEER in self.composition:
            targets.append(UpgradeId.OVERLORDSPEED)

        # targets = set(targets)
        # targets = { t for t in targets if not self.already_pending_upgrade(t) }
        # pendingUpgrades = self.state.upgrades | set(t.item for t in self.macroObjectives)
        # pendingUpgrades = { t for t in pendingUpgrades if type(t) is UpgradeId }
        # targets = targets.difference(pendingUpgrades)

        for t in targets:
            if t in self.state.upgrades:
                pass
            elif self.already_pending_upgrade(t):
                pass
            elif any(o.item is t for o in self.macroObjectives):
                pass
            else:
                self.macroObjectives.append(MacroObjective(t))

    def upgradeSequence(self, upgrades) -> List[UpgradeId]:
        for upgrade in upgrades:
            if upgrade not in self.state.upgrades:
                return [upgrade]
        return []

    def techBuildings(self):
        if self.buildOrder:
            return
        if UnitTypeId.ZERGLING in self.composition and self.count(UnitTypeId.SPAWNINGPOOL) < 1:
            self.macroObjectives.append(MacroObjective(UnitTypeId.SPAWNINGPOOL))
        elif (UnitTypeId.ROACH in self.composition or UnitTypeId.RAVAGER in self.composition) and self.count(UnitTypeId.ROACHWARREN) < 1:
            self.macroObjectives.append(MacroObjective(UnitTypeId.ROACHWARREN))
        elif UnitTypeId.BANELING in self.composition and self.count(UnitTypeId.BANELINGNEST) < 1:
            self.macroObjectives.append(MacroObjective(UnitTypeId.BANELINGNEST))
        elif (UnitTypeId.HYDRALISK in self.composition or UnitTypeId.LURKER in self.composition) and self.count(UnitTypeId.HYDRALISKDEN) < 1:
            self.macroObjectives.append(MacroObjective(UnitTypeId.HYDRALISKDEN))
        elif (UnitTypeId.MUTALISK in self.composition or UnitTypeId.CORRUPTOR in self.composition or UnitTypeId.BROODLORD in self.composition) and self.count(UnitTypeId.SPIRE) < 1:
            self.macroObjectives.append(MacroObjective(UnitTypeId.SPIRE))
        elif UnitTypeId.BROODLORD in self.composition and self.count(UnitTypeId.GREATERSPIRE) < 1:
            self.macroObjectives.append(MacroObjective(UnitTypeId.GREATERSPIRE))
        elif UnitTypeId.ULTRALISK in self.composition and self.count(UnitTypeId.ULTRALISKCAVERN) < 1:
            self.macroObjectives.append(MacroObjective(UnitTypeId.ULTRALISKCAVERN))

    async def changelingScout(self):
        overseers = self.units(withEquivalents(UnitTypeId.OVERSEER))
        if overseers.exists:
            overseer = overseers.random
            ability = TRAIN_INFO[overseer.type_id][UnitTypeId.CHANGELING]["ability"]
            if ability in await self.get_available_abilities(overseer):
                overseer(ability)
        changelings = self.units(CHANGELINGS).idle
        if changelings.exists:
            changeling = changelings.random
            target = random.choice(self.expansion_locations_list)
            changeling.move(target)

    def moveOverlord(self):

        overlords = self.units(UnitTypeId.OVERLORD)
        targets = self.structures(UnitTypeId.CREEPTUMOR) | self.structures(UnitTypeId.CREEPTUMORQUEEN) | self.townhalls
        # targets = targets.filter(lambda s : not s.is_idle)
        overlords_idle = overlords.idle
        if overlords_idle.exists and targets.exists:
            unit = overlords_idle.random
            target = targets.random
            if 1 < unit.distance_to(target):
                unit.move(target.position)

        overseers_idle = self.units(UnitTypeId.OVERSEER).idle
        if overseers_idle.exists:
            overseer = overseers_idle.random
            target = self.units.random
            overseer.move(target.position)

    def techUp(self):
        if self.buildOrder:
            return
        if self.goLair:
            if self.count(UnitTypeId.EVOLUTIONCHAMBER) < 2:
                self.macroObjectives.append(MacroObjective(UnitTypeId.EVOLUTIONCHAMBER))
            if self.count(withEquivalents(UnitTypeId.LAIR)) < 1:
                self.macroObjectives.append(MacroObjective(UnitTypeId.LAIR))
        else:
            self.goLair |= UnitTypeId.BANELING in self.composition
            self.goLair |= UnitTypeId.ROACH in self.composition
            self.goLair |= UnitTypeId.HYDRALISK in self.composition
            self.goLair |= UnitTypeId.MUTALISK in self.composition
            self.goLair |= UnitTypeId.CORRUPTOR in self.composition
            self.goLair |= UpgradeId.ZERGMISSILEWEAPONSLEVEL1 in self.state.upgrades
            self.goLair |= UpgradeId.ZERGMELEEWEAPONSLEVEL1 in self.state.upgrades
            self.goLair |= UpgradeId.ZERGGROUNDARMORSLEVEL1 in self.state.upgrades
            self.goLair |= self.goHive
        if self.goHive:
            if self.count(withEquivalents(UnitTypeId.HIVE)) < 1:
                if self.count(withEquivalents(UnitTypeId.INFESTATIONPIT)) < 1:
                    self.macroObjectives.append(MacroObjective(UnitTypeId.INFESTATIONPIT))
                else:
                    self.macroObjectives.append(MacroObjective(UnitTypeId.HIVE))
        else:
            self.goHive |= UnitTypeId.ULTRALISK in self.composition
            self.goHive |= UnitTypeId.BROODLORD in self.composition
            self.goHive |= UpgradeId.ZERGMISSILEWEAPONSLEVEL2 in self.state.upgrades
            self.goHive |= UpgradeId.ZERGMELEEWEAPONSLEVEL2 in self.state.upgrades
            self.goHive |= UpgradeId.ZERGGROUNDARMORSLEVEL2 in self.state.upgrades
            self.goHive |= UpgradeId.ZERGFLYERARMORSLEVEL2 in self.state.upgrades
            self.goHive |= UpgradeId.ZERGFLYERARMORSLEVEL2 in self.state.upgrades

    async def spreadCreep(self, spreader: Unit = None, numAttempts: int = 3):
        if spreader is None:
            tumors = self.structures(UnitTypeId.CREEPTUMORBURROWED)
            for _ in range(min(tumors.amount, numAttempts)):
                tumor = tumors.random
                if not AbilityId.BUILD_CREEPTUMOR_TUMOR in await self.get_available_abilities(tumor):
                    continue
                spreader = tumor
                break
        if spreader is None:
            return
        if random.random() < 0.1:
            target = spreader.position.towards(random.choice(self.expansion_locations_list), 10)
        else:
            target = spreader.position.towards(self.townhalls.center, -10)
        target = spreader.position.towards(random.choice(self.expansion_locations_list), 10)
        if target is None:
            return
        tumorPlacement = None
        for _ in range(numAttempts):
            position = await self.find_placement(AbilityId.ZERGBUILD_CREEPTUMOR, target, placement_step=1)
            if position is None:
                continue
            if self.isBlockingExpansion(position):
                continue
            tumorPlacement = position
            break
        if tumorPlacement is None:
            return
        assert(spreader.build(UnitTypeId.CREEPTUMOR, tumorPlacement))

    def buildSpores(self):
        if self.buildOrder:
            return
        sporeTime = {
            Race.Zerg: 8 * 60,
            Race.Protoss: 5 * 60,
            Race.Terran: 5 * 60,
        }
        if (
            sporeTime[self.enemy_race] < self.time
            and self.count(UnitTypeId.SPORECRAWLER) < self.townhalls.amount
            and self.already_pending(UnitTypeId.SPORECRAWLER) < 1
        ):
            self.macroObjectives.append(MacroObjective(UnitTypeId.SPORECRAWLER))

    async def microQueens(self):
        queens = self.units(UnitTypeId.QUEEN).sorted(key=lambda q: q.tag)
        hatcheries = self.townhalls.sorted(key=lambda h: h.tag)
        assignment = zip(hatcheries, queens)
        creep_count = self.structures(UnitTypeId.CREEPTUMORBURROWED).amount
        creep_chance = math.exp(-creep_count / 30)
        for hatchery, queen in assignment:
            if not queen.is_idle:
                continue
            elif queen.energy < 25:
                if 5 < queen.distance_to(hatchery):
                    queen.attack(hatchery.position)
            elif 7 < self.larva.amount and random.random() < creep_chance:
                await self.spreadCreep(spreader=queen)
            elif hatchery.is_ready:
                queen(AbilityId.EFFECT_INJECTLARVA, hatchery)

            # abilities = await self.get_available_abilities(queen)
            # if AbilityId.BUILD_CREEPTUMOR_QUEEN in abilities and 7 < self.larva.amount and random.random() < math.exp(-.03*self.count(UnitTypeId.CREEPTUMORBURROWED)):
            #     await self.spreadCreep(spreader=queen)
            # elif AbilityId.EFFECT_INJECTLARVA in abilities and hatchery.is_ready:
            #     queen(AbilityId.EFFECT_INJECTLARVA, hatchery)
            # elif 5 < queen.distance_to(hatchery):
            #     queen.attack(hatchery.position)

    def adjustComposition(self):
        workersTarget = min(80, self.getMaxWorkers())
        self.composition = { UnitTypeId.DRONE: workersTarget }
        # self.composition[UnitTypeId.OVERSEER] = 1
        if self.townhalls.ready.amount < 3:
            pass
        elif not self.goLair:
            self.composition[UnitTypeId.ROACH] = 80
            self.composition[UnitTypeId.ZERGLING] = 80
        elif not self.goHive:
            self.composition[UnitTypeId.ROACH] = 50
            self.composition[UnitTypeId.HYDRALISK] = 50
            # self.composition[UnitTypeId.HYDRALISK] = 20
            # self.composition[UnitTypeId.ZERGLING] = 80
            # self.composition[UnitTypeId.BANELING] = 40
        else:
            self.composition[UnitTypeId.BROODLORD] = 20
            self.composition[UnitTypeId.HYDRALISK] = 40
            self.composition[UnitTypeId.ROACH] = 20
        # else:
        #     self.composition[UnitTypeId.ZERGLING] = 4
        # if 5 <= self.townhalls.amount:
        #     self.composition[UnitTypeId.CORRUPTOR] = workersTarget
        # if 6 <= self.townhalls.amount:
        #     self.composition[UnitTypeId.BROODLORD] = workersTarget

    def adjustGasTarget(self):
        cost_minerals = 0
        cost_vespene = 0
        for t, n in self.composition.items():
            cost = self.calculate_cost(t)
            cost_minerals += n * cost.minerals
            cost_vespene += n * cost.vespene
        minerals = max(0, cost_minerals - self.minerals)
        vespene = max(0, cost_vespene - self.vespene)
        gasRatio = vespene / max(1, vespene + minerals)
        self.gasTarget = 3 + int(1.5 * gasRatio * self.workers.amount)

    def buildGasses(self):
        if self.buildOrder:
            return
        gasActual = self.gas_buildings.filter(lambda v : v.has_vespene).amount
        # gasPending = self.already_pending(UnitTypeId.EXTRACTOR) + sum(1 for t in self.macroObjectives if t.item is UnitTypeId.EXTRACTOR)
        gasPending = sum(1 for t in self.macroObjectives if t.item is UnitTypeId.EXTRACTOR)
        gasNeeded = max(0, int(math.ceil(self.gasTarget / 3)) - (gasActual + gasPending))
        if 0 < gasNeeded:
            self.macroObjectives.append(MacroObjective(UnitTypeId.EXTRACTOR))
    
    def trainQueens(self):
        if self.buildOrder:
            return
        queenTarget = min(5, self.townhalls.amount)
        queenPending = sum(1 for o in self.macroObjectives if o.item is UnitTypeId.QUEEN)
        queenTrainer = self.townhalls
        queenTrainer = queenTrainer.filter(lambda t : not t.is_idle)
        queenTrainer = queenTrainer.filter(lambda t : t.orders[0].ability.id is AbilityId.TRAINQUEEN_QUEEN)
        queenPending += queenTrainer.amount
        queenActual = self.units(UnitTypeId.QUEEN).amount
        if queenActual + queenPending < queenTarget:
            self.macroObjectives.append(MacroObjective(UnitTypeId.QUEEN))

    def morphOverlords(self):
        if self.buildOrder:
            return
        if (
            self.supply_cap < 200
            and self.supply_left + self.getSupplyPending() < self.getSupplyBuffer()
        ):
            self.macroObjectives.append(MacroObjective(UnitTypeId.OVERLORD))
        if self.goLair and not self.count(UnitTypeId.OVERSEER):
            self.macroObjectives.append(MacroObjective(UnitTypeId.OVERSEER))

    def expandIfNecessary(self, max_pending = 1, saturation_target = 0.8):
        if self.buildOrder:
            return
        pendingCount = (
            self.getTraining(UnitTypeId.HATCHERY).amount +
            sum(1 for t in self.macroObjectives if t.item is UnitTypeId.HATCHERY) +
            sum(1 for h in self.townhalls if h.build_progress < 1))
        if pendingCount < max_pending and saturation_target * self.composition[UnitTypeId.DRONE] < self.workers.amount:
            self.macroObjectives.append(MacroObjective(UnitTypeId.HATCHERY))

    def morphUnits(self):
        if self.buildOrder:
            return
        reserve = Cost(0, 0, 0)
        for t in self.macroObjectives:
            if t.cost is not None:
                reserve = reserve + t.cost
        items = list(self.composition.items())
        random.shuffle(items)
        for unit, target in items:
            if 3 <= sum(1 if t.item is unit else 0 for t in self.macroObjectives):
                continue
            # if not self.canAffordWithReserve(self.createCost(unit), reserve):
            #     continue
            if self.tech_requirement_progress(unit) < 1:
                continue
            missing = target - self.count(unit)
            if missing <= 0:
                continue
            self.macroObjectives.append(MacroObjective(unit))

            trainer = list(UNIT_TRAINED_FROM[unit])[0]
            if not self.isStructure(trainer) and trainer is not UnitTypeId.LARVA:
                if any(t.item is trainer for t in self.macroObjectives):
                    continue
                missingTrainers = max(0, missing - self.count(trainer))
                if missingTrainers <= 0:
                    continue
                self.macroObjectives.append(MacroObjective(trainer))