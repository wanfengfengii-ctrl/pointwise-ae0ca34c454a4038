from monitor.observations.schema import ObservationError
from monitor.calibration.coefficients import validate_affine_rule


class RuleError(ObservationError):
    pass


def assert_rule_applicable(observation, rule):
    try:
        validate_affine_rule(rule)
    except ObservationError as exc:
        raise RuleError(str(exc)) from exc
    if observation.unit != rule.input_unit:
        raise RuleError("observation and rule units differ")


def apply_affine_rule(observation, rule):
    assert_rule_applicable(observation, rule)
    return (rule.delta + rule.alpha * observation.x,
            rule.beta * observation.r_u, rule.gamma * observation.r_v)
