from dataclasses import dataclass
from monitor.observations.schema import parse_observation, ObservationError
from monitor.observations.revisions import get_observation_revision
from monitor.archive.catalog import publish_artifact
from monitor.archive.reading import read_result


class CohortError(ObservationError):
    pass


@dataclass(frozen=True)
class Cohort:
    id: str
    members: tuple
    revision_ids: tuple

    @property
    def stations(self):
        return tuple(member.station for member in self.members)


def validate_cohort_members(members):
    if not isinstance(members, (list, tuple)) or not 2 <= len(members) <= 8:
        raise CohortError("cohort requires 2 to 8 stations")
    stations = [m.station for m in members]
    if len(set(stations)) != len(stations):
        raise CohortError("cohort stations must be distinct")


def freeze_cohort(db, references):
    if not isinstance(references, (list, tuple)):
        raise CohortError("references must be an ordered sequence")
    with db.transaction():
        resolved = []
        for reference in references:
            if not isinstance(reference, (list, tuple)) or len(reference) != 3:
                raise CohortError("each reference is source, station, revision")
            resolved.append(get_observation_revision(db, *reference))
        members = [r[1] for r in resolved]
        validate_cohort_members(members)
        parents = {"member:" + str(i): r[0] for i, r in enumerate(resolved)}
        return publish_artifact(db, "cohort",
                                {"members": [m.to_payload() for m in members]}, parents)


def load_cohort(db, cohort_id):
    root = read_result(db, cohort_id)
    if root["kind"] != "cohort":
        raise CohortError("expected cohort")
    payload = root["payload"]
    members = tuple(parse_observation(v) for v in payload["members"])
    validate_cohort_members(members)
    refs = tuple(payload["parents"]["member:" + str(i)] for i in range(len(members)))
    if len(refs) != len(payload["parents"]):
        raise CohortError("cohort provenance length mismatch")
    for member, identity in zip(members, refs):
        actual = root["lineage"][identity]["payload"]["observation"]
        if parse_observation(actual) != member:
            raise CohortError("cohort snapshot differs from revision")
    return Cohort(cohort_id, members, refs)
