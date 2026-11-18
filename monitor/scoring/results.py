from dataclasses import dataclass
from monitor.scoring.representation import ScoreFrame, validate_score_frame
from monitor.archive.reading import read_result
from monitor.observations.cohorts import load_cohort
from monitor.calibration.models import load_calibration_model
from monitor.observations.schema import ObservationError


@dataclass(frozen=True)
class ScoreResult:
    id: str
    frame: ScoreFrame


def make_score_result(frame):
    validate_score_frame(frame)
    return {"frame": frame.to_payload()}


def validate_score_result(frame, parents, cohort, model):
    validate_score_frame(frame)
    if parents != {"cohort": frame.cohort_id, "model": frame.model_id}:
        raise ObservationError("score parent identity mismatch")
    if (cohort.id != frame.cohort_id or model.id != frame.model_id
            or tuple(model.axes.to_payload()) != frame.axes
            or model.rule.version != frame.rule_version):
        raise ObservationError("score shared space differs from fixed model")
    if len(frame.scores) != len(cohort.members):
        raise ObservationError("score cohort length differs")
    for score, observation, revision_id in zip(frame.scores, cohort.members, cohort.revision_ids):
        if score.station != observation.station or score.observation_id != revision_id:
            raise ObservationError("score identity transport mismatch")
    return frame


def load_score_result(db, identity):
    root = read_result(db, identity)
    if root["kind"] != "score":
        raise ObservationError("expected score result")
    frame = ScoreFrame.from_payload(root["payload"]["frame"])
    cohort = load_cohort(db, frame.cohort_id)
    model = load_calibration_model(db, frame.model_id)
    validate_score_result(frame, root["payload"]["parents"], cohort, model)
    return ScoreResult(identity, frame)
