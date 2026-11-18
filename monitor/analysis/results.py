from dataclasses import dataclass
from fractions import Fraction
from monitor.observations.schema import ObservationError, identifier
from monitor.scoring.representation import resolve_target_coordinates


class AnalysisError(ObservationError):
    pass


@dataclass(frozen=True)
class AnalysisRequest:
    score_id: str
    k: int
    targets: tuple
    coordinates: tuple

    def to_payload(self):
        return dict(score_id=self.score_id, k=self.k, targets=list(self.targets))


@dataclass(frozen=True)
class ProbabilityResult:
    request: AnalysisRequest
    marginals: tuple
    joint: Fraction
    union: Fraction

    def to_payload(self):
        return dict(request=self.request.to_payload(), marginals=list(self.marginals),
                    joint=self.joint, union=self.union)


def parse_analysis_request(frame, score_id, k, targets):
    try:
        identifier(score_id)
        if type(k) is not int or not 1 <= k <= len(frame.scores):
            raise AnalysisError("k must be an integer from 1 to station count")
        if not isinstance(targets, (list, tuple)) or len(targets) > 2:
            raise AnalysisError("targets must contain at most two stations")
        targets = tuple(identifier(v) for v in targets)
        if len(set(targets)) != len(targets):
            raise AnalysisError("duplicate targets")
        positions = resolve_target_coordinates(frame, targets)
        return AnalysisRequest(score_id, k, targets, positions)
    except ObservationError as exc:
        raise AnalysisError(str(exc)) from exc


def validate_probabilities(request, marginals, joint, union):
    if len(marginals) != len(request.targets):
        raise AnalysisError("marginal result length mismatch")
    values = tuple(marginals) + (joint, union)
    if any(not isinstance(v, Fraction) or not 0 <= v <= 1 for v in values):
        raise AnalysisError("probabilities must be exact and in [0,1]")
    if not marginals:
        valid = joint == union == 0
    elif len(marginals) == 1:
        valid = joint == union == marginals[0]
    else:
        valid = (union == sum(marginals) - joint and
                 max(Fraction(0), sum(marginals) - 1) <= joint <= min(marginals))
    if not valid:
        raise AnalysisError("joint probability invariants failed")


def make_probability_result(request, marginals, joint, union):
    validate_probabilities(request, marginals, joint, union)
    return ProbabilityResult(request, tuple(marginals), joint, union)
