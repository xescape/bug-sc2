
from typing import Union, Iterable, Dict

from sc2.ids.upgrade_id import UpgradeId
from sc2.ids.unit_typeid import UnitTypeId

from .zerg_macro import ZergMacro
from .zerg_strategy import ZergStrategy
from ..observation import Observation

class RoachRush(ZergMacro):

    def __init__(self):
        super().__init__()
        self.tech_time = 4.25 * 60

    def build_order(self) -> Iterable[Union[UnitTypeId, UpgradeId]]:
        return [
            UnitTypeId.DRONE,
            UnitTypeId.OVERLORD,
            UnitTypeId.DRONE,
            UnitTypeId.DRONE,
            UnitTypeId.DRONE,
            UnitTypeId.DRONE,
            UnitTypeId.SPAWNINGPOOL,
            UnitTypeId.DRONE,
            UnitTypeId.DRONE,
            UnitTypeId.DRONE,
            UnitTypeId.DRONE,
            UnitTypeId.EXTRACTOR,
            UnitTypeId.HATCHERY,
            UnitTypeId.QUEEN,
            UnitTypeId.DRONE,
            UnitTypeId.DRONE,
            UnitTypeId.ROACHWARREN,
            UnitTypeId.DRONE,
            UnitTypeId.OVERLORD,
            UnitTypeId.ROACH,
            UnitTypeId.ROACH,
            UnitTypeId.ROACH,
            UnitTypeId.ROACH,
            UnitTypeId.ROACH,
            UnitTypeId.ROACH,
            UnitTypeId.ROACH,
        ]

    def destroy_destructables(self, bot) -> bool:
        return self.tech_time < bot.time