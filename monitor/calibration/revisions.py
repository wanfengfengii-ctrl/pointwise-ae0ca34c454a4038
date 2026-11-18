from monitor.calibration.schema import parse_calibration, CalibrationError
from monitor.archive.catalog import get_committed_artifact, publish_artifact
from monitor.archive.codec import canonical_json
from monitor.observations.schema import identifier


def get_calibration_revision(db, source, revision):
    source, revision = identifier(source), identifier(revision)
    row = db.connection.execute(
        "SELECT artifact FROM calibration_revisions WHERE source=? AND revision=?",
        (source, revision)).fetchone()
    if row is None:
        raise CalibrationError("unknown calibration revision")
    record = get_committed_artifact(db, row["artifact"], "calibration_revision")
    value = parse_calibration(record["payload"]["calibration"])
    if (value.source, value.revision) != (source, revision):
        raise CalibrationError("calibration index mismatch")
    return row["artifact"], value


def check_calibration_revision(db, value):
    row = db.connection.execute(
        "SELECT artifact FROM calibration_revisions WHERE source=? AND revision=?",
        (value.source, value.revision)).fetchone()
    if row is not None:
        identity, old = get_calibration_revision(db, value.source, value.revision)
        if canonical_json(old.to_payload()) != canonical_json(value.to_payload()):
            raise CalibrationError("calibration revision conflict")
        return identity
    return None


def register_calibration_revision(db, value):
    existing = check_calibration_revision(db, value)
    if existing is not None:
        return existing
    identity = publish_artifact(db, "calibration_revision", {"calibration": value.to_payload()}, {})
    db.connection.execute("INSERT INTO calibration_revisions VALUES(?,?,?)",
                          (value.source, value.revision, identity))
    return identity
