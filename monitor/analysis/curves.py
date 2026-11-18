"""Exact probability curves for a fixed finite concentration-correction path.

For a path parameter t the station score is ``a + b*U + c*V + e*t``.  Only the
constant term moves, so every pairwise comparison plane keeps its fixed slope
and translates linearly in t.  The probability vector is therefore a quadratic
polynomial between the parameters at which the arrangement of comparison
planes, restricted to a scenario rectangle, changes its combinatorial shape.

All such parameters are enumerated below (a complete, checkable basis):

* a comparison line passes through a rectangle vertex;
* two comparison lines meet on a rectangle edge;
* two parallel comparison lines become coincident;
* three comparison lines become concurrent (a bounded cell can appear or
  vanish there);
* a spatially constant comparison flips sign;
* at a point scenario, two station scores at that point become equal.

Between consecutive parameters the active cells have vertices that move
linearly in t (the fixed slopes make every line/line and line/edge
intersection affine in t), so every cell area - and hence every marginal,
joint and union probability - is a polynomial of degree at most two.  The
value on each open piece is recovered by exact interpolation through three
interior parameters using the V1 single-point integrator.  A knot is then
removed exactly when the two adjacent coefficient vectors agree and the
actual probability at the knot equals the polynomial's value there; knots
where the actual value is a genuine isolated jump are therefore never merged
away.  The surviving knots keep their actual values, integrated directly.
"""
from fractions import Fraction
from itertools import combinations

from monitor.archive.reading import ReadError
from monitor.analysis.events import RankingEvent
from monitor.analysis.measure import integrate_ranking_event
from monitor.analysis.results import AnalysisError, parse_analysis_request
from monitor.calibration.scenarios import (PointScenario, RectangleScenario,
                                           validate_distribution)
from monitor.observations.schema import ObservationError


def _pair_rows(path):
    """(b_i-b_j, c_i-c_j, a_i-a_j, e_i-e_j) for every station pair."""
    rows = path.coefficients
    result = []
    for i, j in combinations(range(len(rows)), 2):
        result.append((rows[i][1] - rows[j][1], rows[i][2] - rows[j][2],
                       rows[i][0] - rows[j][0], rows[i][3] - rows[j][3]))
    return result


def _spatial_lines(path):
    """Distinct geometric comparison lines, canonicalised by their fixed slopes."""
    seen, result = set(), []
    for b, c, a, e in _pair_rows(path):
        if b == 0 and c == 0:
            continue
        pivot = b if b else c
        key = (b / pivot, c / pivot, a / pivot, e / pivot)
        if key not in seen:
            seen.add(key)
            result.append((b, c, a, e))
    return result


def _intersection(first, second, t):
    """Intersection point of two comparison lines at parameter t."""
    b1, c1, a1, e1 = first
    b2, c2, a2, e2 = second
    det = b1 * c2 - b2 * c1
    u0 = (c1 * a2 - c2 * a1) / det
    u1 = (c1 * e2 - c2 * e1) / det
    v0 = (b2 * a1 - b1 * a2) / det
    v1 = (b2 * e1 - b1 * e2) / det
    return u0 + u1 * t, v0 + v1 * t


def _inside(point, rectangle):
    u, v = point
    u_lo, u_hi, v_lo, v_hi = rectangle
    return u_lo <= u <= u_hi and v_lo <= v <= v_hi


def _rectangle_events(lines, rectangles, add):
    for bounds in rectangles:
        u_lo, u_hi, v_lo, v_hi = bounds
        vertices = ((u_lo, v_lo), (u_hi, v_lo), (u_hi, v_hi), (u_lo, v_hi))
        for b, c, a, e in lines:
            if e == 0:
                continue
            for u, v in vertices:
                add(-(a + b * u + c * v) / e)
        for first, second in combinations(lines, 2):
            b1, c1, a1, e1 = first
            b2, c2, a2, e2 = second
            det = b1 * c2 - b2 * c1
            if det == 0:
                # Parallel: only coincidence is a discrete event.
                if b1:
                    scale = b2 / b1
                else:
                    scale = c2 / c1
                if e2 - scale * e1 != 0:
                    add(-(a2 - scale * a1) / (e2 - scale * e1))
                continue
            u0 = (c1 * a2 - c2 * a1) / det
            u1 = (c1 * e2 - c2 * e1) / det
            v0 = (b2 * a1 - b1 * a2) / det
            v1 = (b2 * e1 - b1 * e2) / det
            for edge in (u_lo, u_hi):
                if u1 != 0:
                    t = (edge - u0) / u1
                    if v_lo <= v0 + v1 * t <= v_hi:
                        add(t)
            for edge in (v_lo, v_hi):
                if v1 != 0:
                    t = (edge - v0) / v1
                    if u_lo <= u0 + u1 * t <= u_hi:
                        add(t)


def _concurrency_events(lines, rectangles, add):
    for triple in combinations(lines, 3):
        (b1, c1, a1, e1), (b2, c2, a2, e2), (b3, c3, a3, e3) = triple
        base = (b1 * (c2 * a3 - c3 * a2) - c1 * (b2 * a3 - b3 * a2)
                + a1 * (b2 * c3 - b3 * c2))
        slope = (b1 * (c2 * e3 - c3 * e2) - c1 * (b2 * e3 - b3 * e2)
                 + e1 * (b2 * c3 - b3 * c2))
        if slope == 0:
            continue
        t = -base / slope
        pair = None
        for first, second in combinations(triple, 2):
            if first[0] * second[1] - second[0] * first[1] != 0:
                pair = (first, second)
                break
        if pair is None:
            continue
        for bounds in rectangles:
            u, v = _intersection(pair[0], pair[1], t)
            if _inside((u, v), bounds):
                add(t)
                break


def _constant_events(path, add):
    for b, c, a, e in _pair_rows(path):
        if b == 0 and c == 0 and e != 0:
            add(-a / e)


def _point_events(path, points, add):
    for u0, v0 in points:
        for b, c, a, e in _pair_rows(path):
            if e != 0:
                add(-(a + b * u0 + c * v0) / e)


def candidate_times(path, model, lo, hi):
    """Sorted superset of the parameters where an open-piece formula changes."""
    times = {lo, hi}

    def add(value):
        if lo < value < hi:
            times.add(value)

    rectangles, points = [], []
    for scenario in model.scenarios:
        if isinstance(scenario, RectangleScenario):
            rectangles.append((scenario.u_lo, scenario.u_hi, scenario.v_lo, scenario.v_hi))
        elif isinstance(scenario, PointScenario):
            points.append((scenario.u, scenario.v))
        else:
            raise AnalysisError("unsupported scenario measure")
    if rectangles:
        lines = _spatial_lines(path)
        _rectangle_events(lines, rectangles, add)
        _concurrency_events(lines, rectangles, add)
    _constant_events(path, add)
    if points:
        _point_events(path, points, add)
    return sorted(times)


def _measure(path, model, request, t):
    # The request was already validated against the parent frame; shifting
    # only the constant coefficients keeps station order and coordinates.
    event = RankingEvent(path.shifted_frame(t), request)
    result = integrate_ranking_event(event, model)
    return dict(marginals=list(result.marginals), joint=result.joint, union=result.union)


def _quadratic(xs, ys):
    """Interpolate y = c0 + c1*x + c2*x^2 through exactly three points."""
    x1, y1 = xs[0], ys[0]
    x2, y2 = xs[1], ys[1]
    x3, y3 = xs[2], ys[2]
    c2 = ((y3 - y1) / (x3 - x1) - (y2 - y1) / (x2 - x1)) / (x3 - x2)
    c1 = (y2 - y1) / (x2 - x1) - c2 * (x2 + x1)
    c0 = y1 - c1 * x1 - c2 * x1 * x1
    return [c0, c1, c2]


def _evaluate(coefficients, t):
    return sum((coefficient * t ** power
                for power, coefficient in enumerate(coefficients)), Fraction(0))


def _piece_value(piece, t):
    return dict(marginals=[_evaluate(p, t) for p in piece["marginals"]],
                joint=_evaluate(piece["joint"], t), union=_evaluate(piece["union"], t))


def _fit_piece(samples, values, targets):
    """Exact degree<=2 interpolation.

    On a candidate open piece the true probability is a polynomial of degree
    at most two (see the module docstring), so agreement with three distinct
    interior samples determines it uniquely on the whole piece.
    """
    rows = [[value["marginals"][i] for value in values] for i in range(targets)]
    rows.append([value["joint"] for value in values])
    rows.append([value["union"] for value in values])
    fitted = [_quadratic(samples, ys) for ys in rows]
    return dict(marginals=fitted[:targets], joint=fitted[targets],
                union=fitted[targets + 1])


def _merge(breakpoints, pieces, actual, measure):
    """Drop redundant knots.

    A knot is removed exactly when the two adjacent coefficient vectors agree
    and the actual probability there equals the common polynomial's value at
    the knot.  The actual value is integrated lazily and cached, so knots that
    separate differing polynomials (genuine breaks) are never integrated just
    to be tested.  The scan repeats until no knot can be dropped.
    """
    changed = True
    while changed:
        changed = False
        for index in range(1, len(breakpoints) - 1):
            if pieces[index - 1] != pieces[index]:
                continue
            t = breakpoints[index]
            if t not in actual:
                actual[t] = measure(t)
            if _piece_value(pieces[index], t) != actual[t]:
                continue
            del breakpoints[index]
            del pieces[index]
            changed = True
            break
    values = []
    for t in breakpoints:
        if t not in actual:
            actual[t] = measure(t)
        values.append(actual[t])
    return breakpoints, pieces, values


def curve_for_path(path, model, k, targets):
    """Exact ordered breakpoints, open-piece quadratics and actual knot values."""
    try:
        request = parse_analysis_request(path.frame, path.score_id, k, targets)
        if model.id != path.frame.model_id:
            raise AnalysisError("path model differs from the fixed score")
        validate_distribution(model.scenarios)
        labels = dict(path_id=path.id, k=request.k, targets=list(request.targets))
        lo, hi = path.lo, path.hi

        cache = {}

        def measure(t):
            if t not in cache:
                cache[t] = _measure(path, model, request, t)
            return cache[t]

        if lo == hi:
            return dict(request=labels, breakpoints=[lo], pieces=[],
                        values=[measure(lo)])
        breakpoints = candidate_times(path, model, lo, hi)
        pieces = []
        for left, right in zip(breakpoints, breakpoints[1:]):
            # Three distinct interior parameters determine the degree<=2
            # polynomial exactly; this is interpolation, not a grid.
            samples = [left + (right - left) * Fraction(step, 4) for step in (1, 2, 3)]
            measured = [measure(t) for t in samples]
            pieces.append(_fit_piece(samples, measured, len(request.targets)))
        breakpoints, pieces, values = _merge(breakpoints, pieces, cache, measure)
        # Independent guard: every surviving open piece must reproduce exact
        # integration at further interior points; this rejects an incomplete
        # breakpoint basis rather than trusting the interpolation.
        for index, piece in enumerate(pieces):
            left, right = breakpoints[index], breakpoints[index + 1]
            for step in (1, 3):
                t = left + (right - left) * Fraction(step, 4)
                if _piece_value(piece, t) != measure(t):
                    raise AnalysisError("curve differs from exact measurement")
        return dict(request=labels, breakpoints=breakpoints, pieces=pieces, values=values)
    except (ObservationError, ReadError) as exc:
        if isinstance(exc, AnalysisError):
            raise
        raise AnalysisError("cannot compute fixed path curve") from exc
