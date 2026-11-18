from monitor.observations.schema import ObservationError, identifier
from monitor.observations.cohorts import load_cohort
from monitor.calibration.models import load_calibration_model
from monitor.scoring.compile import compile_scores
from monitor.scoring.results import make_score_result
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
