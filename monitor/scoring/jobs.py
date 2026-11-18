from monitor.observations.schema import ObservationError, identifier
from monitor.observations.cohorts import load_cohort
from monitor.calibration.models import load_calibration_model
from monitor.scoring.compile import compile_scores
from monitor.scoring.results import make_score_result, load_score_result
from monitor.scoring.paths import build_path_payload
from monitor.archive.catalog import lookup_request, publish_artifact
from monitor.archive.reading import ReadError


class ScoringError(ObservationError):
    pass


def score(db, request_id, cohort_id, model_id):
    identifier(request_id)
    inputs = dict(cohort_id=identifier(cohort_id), model_id=identifier(model_id))
    with db.transaction():
        previous = lookup_request(db, "score", request_id, inputs)
        if previous is not None:
            return previous
        try:
            cohort = load_cohort(db, cohort_id)
            model = load_calibration_model(db, model_id)
            frame = compile_scores(cohort, model)
        except (ObservationError, ReadError) as exc:
            raise ScoringError("cannot compile fixed scoring inputs") from exc
        return publish_artifact(db, "score", make_score_result(frame),
                                {"cohort": cohort_id, "model": model_id},
                                ("score", request_id, inputs))


def score_path(db, request_id, score_id, directions, lo, hi):
    identifier(request_id)
    parent_id = identifier(score_id)
    with db.transaction():
        try:
            scoring = load_score_result(db, parent_id)
            model = load_calibration_model(db, scoring.frame.model_id)
            payload = build_path_payload(scoring.frame, model, parent_id,
                                         directions, lo, hi)
        except (ObservationError, ReadError) as exc:
            raise ScoringError("cannot build fixed score path") from exc
        inputs = dict(score_id=parent_id, directions=payload["directions"],
                      lo=payload["lo"], hi=payload["hi"])
        previous = lookup_request(db, "score_path", request_id, inputs)
        if previous is not None:
            return previous
        return publish_artifact(db, "score_path", payload, {"score": parent_id},
                                ("score_path", request_id, inputs))
