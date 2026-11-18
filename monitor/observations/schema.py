from dataclasses import dataclass
from fractions import Fraction
import re


class ObservationError(ValueError):
    pass


def identifier(value):
    if not isinstance(value, str) or not value.strip():
        raise ObservationError("identity must be a nonempty string")
    return value


def parse_rational(value):
    if type(value) is int:
        return Fraction(value)
    if isinstance(value, str) and re.fullmatch(r"[+-]?\d+/[+-]?\d+", value):
        try:
            numerator, denominator = value.split("/")
            return Fraction(int(numerator), int(denominator))
        except (ValueError, ZeroDivisionError) as exc:
            raise ObservationError("invalid rational") from exc
    raise ObservationError("rational requires an integer or p/q string")


def exact(value):
    return value if isinstance(value, Fraction) else parse_rational(value)


@dataclass(frozen=True)
class ObservationRevision:
    source: str
    station: str
    revision: str
    x: Fraction
    r_u: Fraction
    r_v: Fraction
    unit: str = "ug/m3"

    @property
    def identity(self):
        return self.source, self.station, self.revision

    def to_payload(self):
        return dict(source=self.source, station=self.station, revision=self.revision,
                    x=self.x, r_u=self.r_u, r_v=self.r_v, unit=self.unit)


def parse_observation(raw):
    if not isinstance(raw, dict):
        raise ObservationError("observation must be an object")
    if set(raw) != {"source", "station", "revision", "x", "r_u", "r_v", "unit"}:
        raise ObservationError("observation fields differ from V1 schema")
    if raw["unit"] != "ug/m3":
        raise ObservationError("unsupported observation unit")
    return ObservationRevision(*(identifier(raw[k]) for k in ("source", "station", "revision")),
                               *(exact(raw[k]) for k in ("x", "r_u", "r_v")), raw["unit"])


def canonical_observation(raw):
    return parse_observation(raw).to_payload()
