from monitor.observations.cohorts import validate_cohort_members
from monitor.scoring.rules import apply_affine_rule, RuleError
from monitor.scoring.representation import ScoreFrame, SharedScore, validate_score_frame


def check_scoring_parents(cohort, model):
    validate_cohort_members(cohort.members)
    if len(cohort.members) != len(cohort.revision_ids):
        raise RuleError("missing cohort revision coordinates")
    if model.axes.u == model.axes.v:
        raise RuleError("shared coordinates must be distinct")
    if model.axes.to_payload() != [model.revision_id + ":U", model.revision_id + ":V"]:
        raise RuleError("axes do not belong to calibration revision")


def compile_scores(cohort, model):
    check_scoring_parents(cohort, model)
    rows = tuple(SharedScore(i, observation.station, cohort.revision_ids[i],
                            *apply_affine_rule(observation, model.rule))
                 for i, observation in enumerate(cohort.members))
    return validate_score_frame(ScoreFrame(cohort.id, model.id,
                                          tuple(model.axes.to_payload()),
                                          model.rule.version, rows))
