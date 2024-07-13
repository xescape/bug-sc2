from __future__ import annotations

import math
from typing import TYPE_CHECKING, Counter

import numpy as np
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId

from ..constants import ZERG_FLYER_ARMOR_UPGRADES, ZERG_FLYER_UPGRADES
from ..unit_counters import UNIT_COUNTER_DICT
from .strategy import Strategy

if TYPE_CHECKING:
    from ..ai_base import AIBase


class ZergMacro(Strategy):
    def __init__(self, ai: AIBase):
        super().__init__(ai)
        self.tech_up = False

    async def on_step(self) -> None:
        self.update_composition()

    def update_composition(self) -> None:
        worker_count = self.ai.state.score.food_used_economy
        worker_target = np.clip(self.ai.get_max_harvester(), 1, 80)

        ratio = max(
            1 - self.ai.combat.confidence,
            worker_count / worker_target,
        )
        # ratio = self.ai.threat_level

        # larva_rate = self.ai.macro.future_spending.larva / (60 * max(1, self.ai.macro.future_timeframe))
        # larva_rate = max(0.0, larva_rate - self.ai.townhalls.ready.amount / 11.0)
        # queen_target = math.ceil(larva_rate / (3 / 29))
        # queen_target = min(queen_target, self.ai.townhalls.amount)
        queen_target = 1 + self.ai.townhalls.amount
        queen_target = np.clip(queen_target, 0, 8)
        # print(queen_target)

        # queen_target = min(8, 1 + self.ai.townhalls.amount)

        composition = {
            UnitTypeId.DRONE: worker_target,
            UnitTypeId.QUEEN: queen_target,
            UnitTypeId.ZERGLING: 0.0,
            UnitTypeId.ROACH: 0.0,
            UnitTypeId.RAVAGER: 0.0,
            UnitTypeId.HYDRALISK: 0.0,
            UnitTypeId.BROODLORD: 0.0,
            UnitTypeId.CORRUPTOR: 0.0,
            UnitTypeId.MUTALISK: 0.0,
        }

        can_build = {t: not any(self.ai.get_missing_requirements(t)) for t in composition}

        enemy_counts = Counter[UnitTypeId](
            enemy.type_id
            for enemy in self.ai.all_enemy_units
            if enemy.type_id in UNIT_COUNTER_DICT
        )

        self.tech_up = 40 <= worker_count and 3 <= self.ai.townhalls.amount
        lair_count = self.ai.count(UnitTypeId.LAIR, include_pending=False, include_planned=False)
        hive_count = self.ai.count(UnitTypeId.HIVE, include_pending=True, include_planned=False)

        if any(enemy_counts):
            for enemy_type, count in enemy_counts.items():
                if counters := UNIT_COUNTER_DICT.get(enemy_type):
                    for counter in counters:
                        if can_build[counter]:
                            composition[counter] += (
                                3 * ratio * count * self.ai.get_unit_cost(enemy_type) / self.ai.get_unit_cost(counter)
                            )
                            break
        else:
            composition[UnitTypeId.ZERGLING] = 1.0

        composition[UnitTypeId.RAVAGER] += composition[UnitTypeId.ROACH] / 10
        composition[UnitTypeId.CORRUPTOR] += composition[UnitTypeId.BROODLORD] / 5

        if self.tech_up:
            # composition[UnitTypeId.OVERLORDTRANSPORT] = 1
            composition[UnitTypeId.ROACHWARREN] = 1
            composition[UnitTypeId.OVERSEER] = 1

        if self.tech_up and 0 < lair_count + hive_count:
            composition[UnitTypeId.HYDRALISKDEN] = 1
            composition[UnitTypeId.OVERSEER] = 2
            composition[UnitTypeId.EVOLUTIONCHAMBER] = 2

        if self.tech_up and 0 < hive_count:
            composition[UnitTypeId.GREATERSPIRE] = 1
            composition[UnitTypeId.OVERSEER] = 3

        if worker_count == worker_target:
            banking_minerals = max(0, self.ai.minerals - 300)
            banking_gas = max(0, self.ai.minerals - 300)
            if 0 < banking_minerals and 0 < banking_gas:
                composition[UnitTypeId.ZERGLING] += 24

                if 0 < banking_gas:
                    if 0 < hive_count:
                        composition[UnitTypeId.BROODLORD] += 12
                        composition[UnitTypeId.CORRUPTOR] += 3
                    if 0 < lair_count:
                        composition[UnitTypeId.HYDRALISK] += 12
                    else:
                        composition[UnitTypeId.ROACH] += 12

        self.ai.macro.composition = {k: math.floor(v) for k, v in composition.items() if 0 < v}

    def filter_upgrade(self, upgrade) -> bool:
        if not self.tech_up and upgrade != UpgradeId.ZERGLINGMOVEMENTSPEED:
            return False
        elif upgrade == UpgradeId.ZERGGROUNDARMORSLEVEL1:
            return 0 < self.ai.count(UpgradeId.ZERGMISSILEWEAPONSLEVEL1, include_planned=False)
        elif upgrade == UpgradeId.ZERGGROUNDARMORSLEVEL2:
            return 0 < self.ai.count(UpgradeId.ZERGMISSILEWEAPONSLEVEL2, include_planned=False)
        elif upgrade == UpgradeId.ZERGGROUNDARMORSLEVEL3:
            return 0 < self.ai.count(UpgradeId.ZERGMISSILEWEAPONSLEVEL3, include_planned=False)
        elif upgrade in ZERG_FLYER_UPGRADES or upgrade in ZERG_FLYER_ARMOR_UPGRADES:
            return 0 < self.ai.count(UnitTypeId.GREATERSPIRE, include_planned=False)
        elif upgrade == UpgradeId.OVERLORDSPEED:
            return 8 * 60 < self.ai.time
        # elif upgrade in {UpgradeId.BURROW, UpgradeId.TUNNELINGCLAWS}:
        #     return False
        else:
            return super().filter_upgrade(upgrade)
