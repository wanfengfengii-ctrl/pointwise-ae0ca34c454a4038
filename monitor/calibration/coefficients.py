from dataclasses import dataclass
from monitor.observations.schema import exact, ObservationError

SUPPORTED_RULE_VERSION = "affine-v1"


@dataclass(frozen=True)
class AffineRule:
    delta: object
    alpha: object
    beta: object
    gamma: object
    version: str
    input_unit: str
    output_unit: str

    def to_payload(self):
        return dict(delta=self.delta, alpha=self.alpha, beta=self.beta, gamma=self.gamma,
                    version=self.version, input_unit=self.input_unit, output_unit=self.output_unit)


def validate_affine_rule(rule):
    if (rule.version != SUPPORTED_RULE_VERSION or rule.input_unit != "ug/m3"
            or rule.output_unit != "score"):
        raise ObservationError("unsupported rule version or units")
    for value in (rule.delta, rule.alpha, rule.beta, rule.gamma):
        exact(value)
    return rule


def parse_affine_rule(raw):
    keys = ("delta", "alpha", "beta", "gamma", "version", "input_unit", "output_unit")
    if not isinstance(raw, dict) or set(raw) != set(keys):
        raise ObservationError("invalid affine rule fields")
    return validate_affine_rule(AffineRule(*(exact(raw[k]) for k in keys[:4]),
                                         *(raw[k] for k in keys[4:])))
