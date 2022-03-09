
from typing import Optional, Set, Union, Iterable, Tuple, List
from sc2.position import Point2
from abc import ABC, abstractmethod

from .resource_base import ResourceBase

class ResourceSingle(ResourceBase):

    def __init__(self, position: Point2):
        super().__init__(position)
        self.harvester_list: List[int] = list()

    def get_resource(self, harvester: int) -> Optional[ResourceBase]:
        if harvester in self.harvesters:
            return self
        else:
            return None

    def try_add(self, harvester: int) -> bool:
        if harvester in self.harvester_list:
            return False
        self.harvester_list.append(harvester)
        return True

    def try_remove_any(self,) -> Optional[int]:
        if not any(self.harvesters):
            return None
        return self.harvester_list.pop()

    def try_remove(self, harvester: int) -> bool:
        if harvester in self.harvester_list:
            self.harvester_list.remove(harvester)
            return True
        else:
            return False

    @property
    def harvesters(self) -> Iterable[int]:
        return self.harvester_list