from dataclasses import dataclass
from fractions import Fraction
from itertools import combinations
from monitor.analysis.geometry import HalfPlane, clip_halfplane, polygon_area
from monitor.scoring.representation import validate_score_frame
from monitor.analysis.results import parse_analysis_request, AnalysisError


@dataclass(frozen=True)
class RankingEvent:
    frame: object
    request: object


def build_ranking_event(frame, request):
    validate_score_frame(frame)
    checked = parse_analysis_request(frame, request.score_id, request.k, request.targets)
    if checked != request:
        raise AnalysisError("request coordinates differ from frame")
    return RankingEvent(frame, request)


def selected_at_point(event, u, v):
    values = [s.at(u, v) for s in event.frame.scores]
    return tuple(sum(other > values[i] for other in values) < event.request.k
                 for i in event.request.coordinates)


def _comparison_planes(frame):
    seen = set()
    for first, second in combinations(frame.scores, 2):
        line = (first.a - second.a, first.b - second.b, first.c - second.c)
        if line[1] == line[2] == 0:
            continue
        pivot = next(v for v in line if v)
        canonical = tuple(v / pivot for v in line)
        if canonical not in seen:
            seen.add(canonical)
            yield HalfPlane(*canonical)


def iter_event_regions(event, support):
    cells = [list(support)]
    for plane in _comparison_planes(event.frame):
        opposite = HalfPlane(-plane.a, -plane.b, -plane.c)
        split = []
        for cell in cells:
            for side in (plane, opposite):
                piece = clip_halfplane(cell, side)
                if polygon_area(piece) > 0:
                    split.append(piece)
        cells = split
    for cell in cells:
        count = len(cell)
        u = sum((p[0] for p in cell), Fraction(0)) / count
        v = sum((p[1] for p in cell), Fraction(0)) / count
        yield cell, selected_at_point(event, u, v)
