from dataclasses import dataclass
from fractions import Fraction
from monitor.observations.schema import exact, ObservationError


@dataclass(frozen=True)
class PointScenario:
    weight: Fraction
    u: Fraction
    v: Fraction

    def to_payload(self):
        return dict(kind="point", weight=self.weight, u=self.u, v=self.v)


@dataclass(frozen=True)
class RectangleScenario:
    weight: Fraction
    u_lo: Fraction
    u_hi: Fraction
    v_lo: Fraction
    v_hi: Fraction

    def to_payload(self):
        return dict(kind="rectangle", weight=self.weight, u_lo=self.u_lo,
                    u_hi=self.u_hi, v_lo=self.v_lo, v_hi=self.v_hi)


def rectangle_support_area(scenario):
    if scenario.u_hi <= scenario.u_lo or scenario.v_hi <= scenario.v_lo:
        raise ObservationError("rectangle must have positive lengths on both axes")
    return (scenario.u_hi - scenario.u_lo) * (scenario.v_hi - scenario.v_lo)


def validate_distribution(scenarios):
    if not 1 <= len(scenarios) <= 4:
        raise ObservationError("distribution requires 1 to 4 scenarios")
    for scenario in scenarios:
        if scenario.weight < 0:
            raise ObservationError("negative scenario weight")
        if isinstance(scenario, RectangleScenario):
            rectangle_support_area(scenario)
        elif not isinstance(scenario, PointScenario):
            raise ObservationError("unknown scenario")
    if sum((s.weight for s in scenarios), Fraction(0)) != 1:
        raise ObservationError("scenario weights must sum to one")
    return tuple(scenarios)


def parse_scenarios(raw):
    if not isinstance(raw, (list, tuple)):
        raise ObservationError("scenarios must be ordered")
    parsed = []
    for item in raw:
        if not isinstance(item, dict):
            raise ObservationError("scenario must be an object")
        if item.get("kind") == "point":
            keys = ("weight", "u", "v")
            cls = PointScenario
        elif item.get("kind") == "rectangle":
            keys = ("weight", "u_lo", "u_hi", "v_lo", "v_hi")
            cls = RectangleScenario
        else:
            raise ObservationError("unknown scenario kind")
        if set(item) != set(keys) | {"kind"}:
            raise ObservationError("invalid scenario fields")
        parsed.append(cls(*(exact(item[k]) for k in keys)))
    return validate_distribution(parsed)
