
from typing import Iterable, Set, Tuple, Union
from sc2.dicts.unit_research_abilities import RESEARCH_INFO
from sc2.dicts.unit_train_build_abilities import TRAIN_INFO
from sc2.dicts.unit_trained_from import UNIT_TRAINED_FROM
from sc2.dicts.upgrade_researched_from import UPGRADE_RESEARCHED_FROM
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.position import Point2
from sc2.constants import EQUIVALENTS_FOR_TECH_PROGRESS

import numpy as np

def canAttack(a, b):
    return a.distance_to(b) < 6 + (a.air_range if b.is_flying else a.ground_range)

def makeUnique(a):
    b = []
    for x in a:
        # check if exists in unq_list
        if x not in b:
            b.append(x)
    return b

def armyValue(group):
    return sum((unitValue(unit) for unit in group))

def unitPriority(unit, target = None):
    if target is None:
        dps = max(unit.air_dps, unit.ground_dps)
    else:
        dps = unit.calculate_dps_vs_target(target)
    return dps / (unit.shield + unit.health)

def center(group):
    xs = 0
    ys = 0
    for unit in group:
        xs += unit.position[0]
        ys += unit.position[1]
    xs /= group.amount
    ys /= group.amount
    return Point2((xs, ys))

def withEquivalents(unit: UnitTypeId) -> Set[UnitTypeId]:
    if unit in EQUIVALENTS_FOR_TECH_PROGRESS:
        return { unit } | EQUIVALENTS_FOR_TECH_PROGRESS[unit]
    else:
        return { unit }

def unitValue(unit, target = None):
    if target is None:
        dps = max(unit.air_dps, unit.ground_dps)
    else:
        dps = unit.calculate_dps_vs_target(target)
    return dps * (unit.health + unit.shield)

def dot(x, y):
    return sum((xi * yi for xi, yi in zip(x, y)))

def choose_by_distance_to(choices, subject):
    w = [pow(max(1, subject.distance_to(c)), -2) for c in choices]
    ws = sum(w)
    p = [wi / ws for wi in w]
    i = np.random.choice(len(choices), p=p)
    return choices[i]

def get_requirements(item: Union[UnitTypeId, UpgradeId]) -> Iterable[Union[UnitTypeId, UpgradeId]]:

    if type(item) is UnitTypeId:
        trainers = UNIT_TRAINED_FROM[item]
        trainer = sorted(trainers, key=lambda v:v.value)[0]
        yield trainer
        info = TRAIN_INFO[trainer][item]
    elif type(item) is UpgradeId:
        researcher = UPGRADE_RESEARCHED_FROM[item]
        yield researcher
        info = RESEARCH_INFO[researcher][item]
    else:
        raise TypeError()

    requirements = {
        info.get("required_building"),
        info.get("required_upgrade")
    }
    requirements.discard(None)

    for r in requirements:
        yield r
        for r2 in get_requirements(r):
            yield r2