from dataclasses import dataclass
from monitor.archive.catalog import publish_artifact, get_committed_artifact
from monitor.archive.codec import canonical_json
from monitor.observations.schema import ObservationError, identifier


@dataclass(frozen=True)
class IngestReceipt:
    id: str
    batch: str
    revisions: tuple


def lookup_receipt(db, batch):
    identifier(batch)
    row = db.connection.execute("SELECT input,artifact FROM receipts WHERE batch=?", (batch,)).fetchone()
    if row is None:
        return None
    record = get_committed_artifact(db, row["artifact"], "receipt")
    if record["payload"]["batch"] != batch:
        raise ObservationError("receipt index mismatch")
    return dict(input=row["input"], id=row["artifact"], payload=record["payload"])


def receipt_matches(receipt, observations):
    return receipt["input"] == canonical_json(observations)


def save_receipt(db, batch, observations, revision_ids):
    parents = {"member:" + str(i): v for i, v in enumerate(revision_ids)}
    identity = publish_artifact(db, "receipt",
                                dict(batch=batch, observations=observations), parents)
    db.connection.execute("INSERT INTO receipts VALUES(?,?,?)",
                          (batch, canonical_json(observations), identity))
    return IngestReceipt(identity, batch, tuple(revision_ids))
