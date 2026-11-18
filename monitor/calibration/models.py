from monitor.calibration.schema import (parse_calibration, SharedAxes,
                                        FrozenCalibrationModel, CalibrationError)
from monitor.calibration.revisions import register_calibration_revision
from monitor.archive.catalog import publish_artifact, get_committed_artifact
from monitor.archive.reading import read_result


def freeze_calibration_model(db, revision_id, calibration):
    source = get_committed_artifact(db, revision_id, "calibration_revision")
    if parse_calibration(source["payload"]["calibration"]) != calibration:
        raise CalibrationError("model must preserve its registered calibration")
    axes = SharedAxes(revision_id + ":U", revision_id + ":V")
    return publish_artifact(db, "model",
                            dict(axes=axes.to_payload(), calibration=calibration.to_payload()),
                            {"revision": revision_id})


def save_calibration(db, raw):
    calibration = parse_calibration(raw)
    with db.transaction():
        revision_id = register_calibration_revision(db, calibration)
        return freeze_calibration_model(db, revision_id, calibration)


def load_calibration_model(db, model_id):
    root = read_result(db, model_id)
    if root["kind"] != "model":
        raise CalibrationError("expected calibration model")
    payload = root["payload"]
    revision_id = payload["parents"]["revision"]
    calibration = parse_calibration(payload["calibration"])
    parent = parse_calibration(root["lineage"][revision_id]["payload"]["calibration"])
    if parent != calibration:
        raise CalibrationError("model differs from fixed revision")
    expected = [revision_id + ":U", revision_id + ":V"]
    if payload["axes"] != expected:
        raise CalibrationError("invalid shared axes")
    return FrozenCalibrationModel(model_id, revision_id, SharedAxes(*expected), calibration)
