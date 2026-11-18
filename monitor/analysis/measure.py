from fractions import Fraction
from monitor.calibration.scenarios import (PointScenario, RectangleScenario,
                                           rectangle_support_area, validate_distribution)
from monitor.analysis.geometry import rectangle_polygon, polygon_area
from monitor.analysis.events import iter_event_regions, selected_at_point
from monitor.analysis.results import make_probability_result, AnalysisError


def _indicators(selected):
    if not selected:
        return (), Fraction(0), Fraction(0)
    return tuple(Fraction(int(v)) for v in selected), Fraction(int(all(selected))), Fraction(int(any(selected)))


def measure_scenario(event, scenario):
    count = len(event.request.targets)
    if not count:
        return (), Fraction(0), Fraction(0)
    if isinstance(scenario, PointScenario):
        return _indicators(selected_at_point(event, scenario.u, scenario.v))
    if not isinstance(scenario, RectangleScenario):
        raise AnalysisError("unsupported scenario measure")
    total = rectangle_support_area(scenario)
    support = rectangle_polygon(scenario.u_lo, scenario.u_hi, scenario.v_lo, scenario.v_hi)
    marginal = [Fraction(0)] * count
    joint = union = covered = Fraction(0)
    for polygon, selected in iter_event_regions(event, support):
        mass = polygon_area(polygon) / total
        covered += mass
        flags, both, either = _indicators(selected)
        for i, flag in enumerate(flags):
            marginal[i] += flag * mass
        joint += both * mass
        union += either * mass
    if covered != 1:
        raise AnalysisError("ranking partition does not conserve rectangle mass")
    return tuple(marginal), joint, union


def combine_scenario_measures(event, weighted):
    marginal = [Fraction(0)] * len(event.request.targets)
    joint = union = Fraction(0)
    for weight, (values, both, either) in weighted:
        for i, value in enumerate(values):
            marginal[i] += weight * value
        joint += weight * both
        union += weight * either
    return make_probability_result(event.request, marginal, joint, union)


def integrate_ranking_event(event, model):
    if (event.frame.model_id != model.id or
            event.frame.axes != tuple(model.axes.to_payload())):
        raise AnalysisError("ranking event uses a different shared model")
    scenarios = validate_distribution(model.scenarios)
    return combine_scenario_measures(
        event, ((s.weight, measure_scenario(event, s)) for s in scenarios))
