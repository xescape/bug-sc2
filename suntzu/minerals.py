
from typing import Set, Union, Iterable
from sc2.position import Point2
from suntzu.resource import Resource

from .observation import Observation

class Minerals(Resource):

    def __init__(self, position: Point2):
        super().__init__(position)

    def update(self, observation: Observation):

        super().update(observation)

        self.harvesters = { h for h in self.harvesters if h in observation.unit_by_tag }
        
        patch = observation.resource_by_position.get(self.position)

        if not patch:
            self.remaining = 0
            for harvester in (observation.unit_by_tag[h] for h in self.harvesters):
                if harvester.is_carrying_resource:
                    harvester.return_resource()
                    harvester.stop(queue=True)
                else:
                    harvester.stop()
                continue
            return

        self.remaining = patch.mineral_contents

        for harvester in (observation.unit_by_tag[h] for h in self.harvesters):
            if harvester.is_gathering:
                if harvester.order_target != patch.tag:
                    harvester.gather(patch)
            elif harvester.is_carrying_resource:
                if not harvester.is_returning:
                    harvester.return_resource()
            else:
                harvester.gather(patch)

    @property
    def harvester_target(self):
        if self.remaining:
            return 2
        else:
            return 0