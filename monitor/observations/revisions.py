from monitor.observations.schema import parse_observation, ObservationError, identifier
from monitor.observations.receipts import (lookup_receipt, receipt_matches,
                                          save_receipt, IngestReceipt)
from monitor.archive.catalog import get_committed_artifact, publish_artifact
from monitor.archive.codec import canonical_json


def get_observation_revision(db, source, station, revision):
    key = tuple(identifier(v) for v in (source, station, revision))
    row = db.connection.execute(
        "SELECT artifact FROM observations WHERE source=? AND station=? AND revision=?", key).fetchone()
    if row is None:
        raise ObservationError("unknown observation revision")
    record = get_committed_artifact(db, row["artifact"], "observation")
    observation = parse_observation(record["payload"]["observation"])
    if observation.identity != key:
        raise ObservationError("observation index mismatch")
    return row["artifact"], observation


def preflight_batch(db, rows):
    if not isinstance(rows, (list, tuple)) or not rows:
        raise ObservationError("batch must contain observations")
    parsed = [parse_observation(row) for row in rows]
    keys = [r.identity for r in parsed]
    if len(set(keys)) != len(keys):
        raise ObservationError("duplicate observation identity in batch")
    for observation in parsed:
        existing = db.connection.execute(
            "SELECT artifact FROM observations WHERE source=? AND station=? AND revision=?",
            observation.identity).fetchone()
        if existing:
            _, previous = get_observation_revision(db, *observation.identity)
            if canonical_json(previous.to_payload()) != canonical_json(observation.to_payload()):
                raise ObservationError("observation revision conflict")
    return parsed


def ingest(db, batch, rows):
    identifier(batch)
    with db.transaction():
        parsed = preflight_batch(db, rows)
        normalized = [r.to_payload() for r in parsed]
        receipt = lookup_receipt(db, batch)
        if receipt is not None:
            if not receipt_matches(receipt, normalized):
                raise ObservationError("batch identity conflict")
            refs = receipt["payload"]["parents"]
            return IngestReceipt(receipt["id"], batch,
                                 tuple(refs["member:" + str(i)] for i in range(len(refs))))
        revision_ids = []
        for observation in parsed:
            identity = publish_artifact(db, "observation",
                                        {"observation": observation.to_payload()}, {})
            db.connection.execute("INSERT OR IGNORE INTO observations VALUES(?,?,?,?)",
                                  (*observation.identity, identity))
            revision_ids.append(identity)
        return save_receipt(db, batch, normalized, revision_ids)
