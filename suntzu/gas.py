
from typing import Optional, Set

from s2clientprotocol.error_pb2 import Error
from sc2.constants import ALL_GAS
from sc2.position import Point2
from suntzu.resource import Resource

from suntzu.observation import Observation
from suntzu.resource_single import ResourceSingle

class Gas(ResourceSingle):

    def __init__(self, position: Point2):
        super().__init__(position)
        self.building: Optional[int] = None

    @property
    def harvester_target(self):
        if self.remaining:
            return 3
        else:
            return 0

    def update(self, observation: Observation):
        super().update(observation)
        geyser = observation.resource_by_position.get(self.position)
        if not geyser:
            self.remaining = 0
            return

        building = observation.unit_by_tag.get(self.building)
        if not building:
            self.building = None
            gas_buildings = {
                g
                for t in ALL_GAS
                for g in observation.actual_by_type[t]
            }
            building = next(filter(lambda g : g.position == self.position, gas_buildings), None)


        if not (building and building.is_ready):
            self.remaining = 0
            return

        self.building = building.tag

        # if building.assigned_harvesters < len(self.harvesters) - 1:
        #     return

        # if building.assigned_harvesters == len(self.harvesters) - 1:
        #     removed = 0
        #     for harvester in frozenset(self.harvesters):
        #         if harvester not in observation.unit_by_tag:
        #             self.harvesters.remove(harvester)
        #             removed += 1
        #     if removed == 0:
        #         raise Error()

        self.remaining = building.vespene_contents

        if self.building and not self.remaining:
            for harvester in (observation.unit_by_tag.get(h) for h in self.harvester_set):
                if not harvester:
                    continue
                harvester.stop()
            self.harvester_set.clear()
        else:
            for harvester in (observation.unit_by_tag.get(h) for h in self.harvester_set):
                if not harvester:
                    continue
                elif harvester.is_carrying_resource:
                    if not harvester.is_returning:
                        harvester.return_resource()
                elif harvester.is_returning:
                    pass
                else:
                    harvester.gather(building)