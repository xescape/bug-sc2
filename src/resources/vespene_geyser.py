from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from sc2.unit import Unit

from .resource_unit import ResourceUnit
from ..constants import RICH_GAS

if TYPE_CHECKING:
    from ..units.structure import Structure
    from ..ai_base import AIBase


class VespeneGeyser(ResourceUnit):

    def __init__(self, ai: AIBase, unit: Unit) -> None:
        super().__init__(ai, unit)
        self.structure: Optional[Structure] = None

    @property
    def is_rich(self) -> bool:
        if not self.unit:
            return False
        else:
            return self.unit.type_id in RICH_GAS

    @property
    def remaining(self) -> int:
        if not self.unit.is_visible:
            return 2250
        else:
            return self.unit.vespene_contents

    @property
    def harvester_target(self) -> int:
        return 3 if self.structure and self.structure.unit.is_ready and self.remaining else 0
