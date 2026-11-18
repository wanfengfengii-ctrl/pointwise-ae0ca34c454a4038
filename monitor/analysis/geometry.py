from dataclasses import dataclass
from fractions import Fraction


@dataclass(frozen=True)
class HalfPlane:
    a: Fraction
    b: Fraction
    c: Fraction

    def evaluate(self, point):
        return self.a + self.b * point[0] + self.c * point[1]


def rectangle_polygon(u_lo, u_hi, v_lo, v_hi):
    if u_hi <= u_lo or v_hi <= v_lo:
        raise ValueError("degenerate rectangle")
    return [(u_lo, v_lo), (u_hi, v_lo), (u_hi, v_hi), (u_lo, v_hi)]


def clip_halfplane(polygon, plane):
    if not polygon:
        return []
    result = []
    previous = polygon[-1]
    before = plane.evaluate(previous)
    for current in polygon:
        after = plane.evaluate(current)
        if (before < 0) != (after < 0):
            t = before / (before - after)
            crossing = (previous[0] + t * (current[0] - previous[0]),
                        previous[1] + t * (current[1] - previous[1]))
            if not result or crossing != result[-1]:
                result.append(crossing)
        if after >= 0 and (not result or current != result[-1]):
            result.append(current)
        previous, before = current, after
    if len(result) > 1 and result[0] == result[-1]:
        result.pop()
    return result


def polygon_area(polygon):
    if len(polygon) < 3:
        return Fraction(0)
    return abs(sum((p[0] * q[1] - p[1] * q[0]
                    for p, q in zip(polygon, polygon[1:] + polygon[:1])), Fraction(0))) / 2
