from dataclasses import dataclass
from fractions import Fraction
from monitor.observations.schema import identifier, exact, ObservationError
from monitor.calibration.coefficients import SUPPORTED_RULE_VERSION


@dataclass(frozen=True)
class SharedScore:
    coordinate: int
    station: str
    observation_id: str
    a: Fraction
    b: Fraction
    c: Fraction

    def at(self, u, v):
        return self.a + self.b * u + self.c * v

    def to_payload(self):
        return dict(coordinate=self.coordinate, station=self.station,
                    observation_id=self.observation_id, coefficients=[self.a, self.b, self.c])


@dataclass(frozen=True)
class ScoreFrame:
    cohort_id: str
    model_id: str
    axes: tuple
    rule_version: str
    scores: tuple

    def to_payload(self):
        return dict(cohort_id=self.cohort_id, model_id=self.model_id, axes=list(self.axes),
                    rule_version=self.rule_version, scores=[s.to_payload() for s in self.scores])

    @classmethod
    def from_payload(cls, raw):
        if not isinstance(raw, dict) or set(raw) != {
                "cohort_id", "model_id", "axes", "rule_version", "scores"}:
            raise ObservationError("invalid score frame fields")
        if not isinstance(raw["scores"], list):
            raise ObservationError("score rows must be ordered")
        scores = []
        for row in raw["scores"]:
            if not isinstance(row, dict) or set(row) != {
                    "coordinate", "station", "observation_id", "coefficients"}:
                raise ObservationError("invalid score row")
            if not isinstance(row["coefficients"], list) or len(row["coefficients"]) != 3:
                raise ObservationError("score requires three coefficients")
            scores.append(SharedScore(row["coordinate"], identifier(row["station"]),
                                      identifier(row["observation_id"]),
                                      *(exact(v) for v in row["coefficients"])))
        frame = cls(identifier(raw["cohort_id"]), identifier(raw["model_id"]),
                    tuple(raw["axes"]), raw["rule_version"], tuple(scores))
        return validate_score_frame(frame)


def validate_score_frame(frame):
    if not 2 <= len(frame.scores) <= 8:
        raise ObservationError("score frame requires 2 to 8 stations")
    if len(frame.axes) != 2 or len(set(frame.axes)) != 2:
        raise ObservationError("score frame requires two distinct shared axes")
    for axis in frame.axes:
        identifier(axis)
    if frame.rule_version != SUPPORTED_RULE_VERSION:
        raise ObservationError("unsupported frame rule")
    if len({s.station for s in frame.scores}) != len(frame.scores):
        raise ObservationError("duplicate frame station")
    for coordinate, score in enumerate(frame.scores):
        if type(score.coordinate) is not int or score.coordinate != coordinate:
            raise ObservationError("score coordinates must preserve original order")
        identifier(score.station)
        identifier(score.observation_id)
        if any(not isinstance(v, Fraction) for v in (score.a, score.b, score.c)):
            raise ObservationError("score coefficients must be exact")
    return frame


def resolve_target_coordinates(frame, targets):
    positions = {s.station: s.coordinate for s in frame.scores}
    try:
        return tuple(positions[identifier(t)] for t in targets)
    except KeyError as exc:
        raise ObservationError("unknown target station") from exc
