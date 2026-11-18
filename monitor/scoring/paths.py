"""Fixed finite concentration-correction paths built on a V1 score.

A path stores, for every station of the parent score, a rational direction d_i
and the affine coefficient row [a, b, c, e] such that the station score at
parameter t is a + b*U + c*V + e*t.  The parent observation is never modified;
only the concentration channel of the fixed affine rule is transported.
"""
from dataclasses import dataclass, replace
from fractions import Fraction

from monitor.observations.schema import ObservationError, exact, identifier
from monitor.scoring.representation import ScoreFrame


@dataclass(frozen=True)
class ScorePath:
    id: str
    score_id: str
    frame: ScoreFrame
    directions: tuple
    coefficients: tuple
    lo: Fraction
    hi: Fraction

    @property
    def stations(self):
        return tuple(score.station for score in self.frame.scores)

    def shifted_frame(self, t):
        """Return the parent frame with each constant term moved by e*t."""
        t = exact(t)
        rows = tuple(replace(score, a=row[0] + row[3] * t)
                     for score, row in zip(self.frame.scores, self.coefficients))
        return replace(self.frame, scores=rows)


def path_coefficients(frame, alpha, directions):
    """[a, b, c, alpha*d] rows aligned with the parent score order."""
    return tuple((score.a, score.b, score.c, alpha * direction)
                 for score, direction in zip(frame.scores, directions))


def parse_directions(frame, directions):
    if not isinstance(directions, dict):
        raise ObservationError("directions must map every station identity")
    stations = [score.station for score in frame.scores]
    if set(directions) != set(stations):
        raise ObservationError("directions must match exactly the parent stations")
    try:
        return tuple(exact(directions[station]) for station in stations)
    except ObservationError as exc:
        raise ObservationError("invalid station direction") from exc


def parse_path_bounds(lo, hi):
    lo, hi = exact(lo), exact(hi)
    if lo > hi:
        raise ObservationError("path interval must satisfy lo<=hi")
    return lo, hi


def build_path_payload(frame, model, score_id, directions, lo, hi):
    identifier(score_id)
    if model.id != frame.model_id:
        raise ObservationError("path model differs from parent score")
    parsed = parse_directions(frame, directions)
    lo, hi = parse_path_bounds(lo, hi)
    coefficients = path_coefficients(frame, model.rule.alpha, parsed)
    return dict(score_id=score_id, stations=[score.station for score in frame.scores],
                directions=list(parsed),
                coefficients=[list(row) for row in coefficients], lo=lo, hi=hi)


def path_from_payload(identity, payload, frame, model):
    """Validate a stored/offline path payload against its fixed sources."""
    keys = {"score_id", "stations", "directions", "coefficients", "lo", "hi", "parents"}
    if not isinstance(payload, dict) or set(payload) != keys:
        raise ObservationError("invalid score path fields")
    score_id = identifier(payload["score_id"])
    stations = [score.station for score in frame.scores]
    if payload["stations"] != stations:
        raise ObservationError("path stations differ from parent score order")
    if payload["parents"] != {"score": score_id}:
        raise ObservationError("path parent differs from stored score")
    raw_directions = payload["directions"]
    if not isinstance(raw_directions, list) or len(raw_directions) != len(stations):
        raise ObservationError("path requires one direction per station")
    directions = tuple(exact(v) for v in raw_directions)
    lo, hi = parse_path_bounds(payload["lo"], payload["hi"])
    expected = path_coefficients(frame, model.rule.alpha, directions)
    rows = payload["coefficients"]
    if not isinstance(rows, list) or len(rows) != len(stations):
        raise ObservationError("path coefficient rows differ from station count")
    parsed_rows = []
    for row, want in zip(rows, expected):
        if not isinstance(row, list) or len(row) != 4:
            raise ObservationError("path coefficient row requires four values")
        actual = tuple(exact(v) for v in row)
        if actual != want:
            raise ObservationError("path coefficients differ from fixed rule and direction")
        parsed_rows.append(actual)
    return ScorePath(identity, score_id, frame, directions, tuple(parsed_rows), lo, hi)


def load_path_result(db, identity):
    from monitor.archive.reading import ReadError, read_result
    from monitor.scoring.results import load_score_result
    from monitor.calibration.models import load_calibration_model
    try:
        root = read_result(db, identity)
        if root["kind"] != "score_path":
            raise ObservationError("expected score path result")
        payload = root["payload"]
        if not isinstance(payload, dict):
            raise ObservationError("score path payload is not an object")
        score_id = payload.get("score_id")
        if not isinstance(score_id, str):
            raise ObservationError("score path lacks a parent score identity")
        scoring = load_score_result(db, score_id)
        model = load_calibration_model(db, scoring.frame.model_id)
        return path_from_payload(identity, payload, scoring.frame, model)
    except ReadError:
        raise
    except ObservationError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ObservationError("corrupt score path") from exc
