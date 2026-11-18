from dataclasses import dataclass
from monitor.observations.schema import identifier, ObservationError
from monitor.calibration.scenarios import parse_scenarios
from monitor.calibration.coefficients import parse_affine_rule


class CalibrationError(ObservationError):
    pass


@dataclass(frozen=True)
class SharedAxes:
    u: str
    v: str

    def to_payload(self):
        return [self.u, self.v]


@dataclass(frozen=True)
class CalibrationRevision:
    source: str
    revision: str
    scenarios: tuple
    rule: object

    def to_payload(self):
        return dict(source=self.source, revision=self.revision, variables=["U", "V"],
                    scenarios=[s.to_payload() for s in self.scenarios],
                    rule=self.rule.to_payload())


@dataclass(frozen=True)
class FrozenCalibrationModel:
    id: str
    revision_id: str
    axes: SharedAxes
    calibration: CalibrationRevision

    @property
    def scenarios(self):
        return self.calibration.scenarios

    @property
    def rule(self):
        return self.calibration.rule


def parse_calibration(raw):
    try:
        if not isinstance(raw, dict) or set(raw) != {
                "source", "revision", "variables", "scenarios", "rule"}:
            raise CalibrationError("invalid calibration fields")
        if raw["variables"] != ["U", "V"]:
            raise CalibrationError("V1 uses shared U,V in this order")
        return CalibrationRevision(identifier(raw["source"]), identifier(raw["revision"]),
                                   parse_scenarios(raw["scenarios"]), parse_affine_rule(raw["rule"]))
    except ObservationError as exc:
        raise CalibrationError(str(exc)) from exc
