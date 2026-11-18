from monitor.archive.codec import canonical_json, loads, payload_digest
from monitor.archive.database import StorageError
from monitor.observations.schema import identifier


class PublicationError(StorageError):
    pass


class RequestConflictError(PublicationError):
    pass


def _parent_types(kind, parents):
    if kind in ("observation", "calibration_revision"):
        if parents:
            raise PublicationError("source artifact has parents")
        return {}
    fixed = {"model": {"revision": "calibration_revision"},
             "score": {"cohort": "cohort", "model": "model"},
             "report": {"score": "score"}}
    if kind == "cohort" or kind == "receipt":
        if not parents or set(parents) != {"member:" + str(i) for i in range(len(parents))}:
            raise PublicationError("invalid member parent sequence")
        return {role: "observation" for role in parents}
    if kind not in fixed or set(parents) != set(fixed[kind]):
        raise PublicationError("invalid typed parents")
    return fixed[kind]


def validate_artifact(record):
    payload = record["payload"]
    if not isinstance(payload, dict) or not isinstance(payload.get("parents"), dict):
        raise PublicationError("artifact payload lacks parent references")
    digest = payload_digest(payload)
    if record["digest"] != digest or record["id"] != record["kind"] + ":" + digest:
        raise PublicationError("artifact content mismatch")
    _parent_types(record["kind"], payload["parents"])
    return record


def get_committed_artifact(db, artifact_id, expected_kind=None):
    row = db.connection.execute(
        "SELECT * FROM artifacts WHERE id=? AND committed=1", (artifact_id,)).fetchone()
    if row is None:
        raise PublicationError("unknown or uncommitted artifact: " + str(artifact_id))
    try:
        record = dict(id=row["id"], kind=row["kind"], digest=row["digest"],
                      payload=loads(row["body"]))
        validate_artifact(record)
        refs = {r["role"]: r["parent"] for r in db.connection.execute(
            "SELECT role,parent FROM parents WHERE child=?", (artifact_id,))}
        if refs != record["payload"]["parents"]:
            raise PublicationError("stored references differ from payload")
        if expected_kind is not None and record["kind"] != expected_kind:
            raise PublicationError("wrong artifact kind")
        return record
    except (ValueError, TypeError, KeyError) as exc:
        raise PublicationError("corrupt artifact") from exc


def resolve_lineage(db, artifact_id):
    result, visiting = {}, set()

    def visit(identity):
        if identity in visiting:
            raise PublicationError("cyclic provenance")
        if identity in result:
            return result[identity]
        visiting.add(identity)
        record = get_committed_artifact(db, identity)
        expected = _parent_types(record["kind"], record["payload"]["parents"])
        for role, parent in record["payload"]["parents"].items():
            if visit(parent)["kind"] != expected[role]:
                raise PublicationError("parent type mismatch")
        visiting.remove(identity)
        result[identity] = record
        return record

    visit(artifact_id)
    return result


def lookup_request(db, namespace, request_id, inputs):
    identifier(request_id)
    row = db.connection.execute(
        "SELECT input,artifact FROM bindings WHERE namespace=? AND request=?",
        (namespace, request_id)).fetchone()
    if row is None:
        return None
    if row["input"] != canonical_json(inputs):
        raise RequestConflictError("request identity already bound to different input")
    resolve_lineage(db, row["artifact"])
    return row["artifact"]


def publish_artifact(db, kind, payload, parents, binding=None):
    if not db._depth:
        raise PublicationError("publication requires caller transaction")
    payload = dict(payload, parents=dict(parents))
    expected = _parent_types(kind, parents)
    for role, parent in parents.items():
        get_committed_artifact(db, parent, expected[role])
        resolve_lineage(db, parent)
    digest = payload_digest(payload)
    identity = kind + ":" + digest
    row = db.connection.execute("SELECT id FROM artifacts WHERE id=?", (identity,)).fetchone()
    if row is None:
        db.connection.execute("INSERT INTO artifacts VALUES(?,?,?,?,0)",
                              (identity, kind, canonical_json(payload), digest))
        db.checkpoint("payload")
        for role, parent in parents.items():
            db.connection.execute("INSERT INTO parents VALUES(?,?,?)", (identity, role, parent))
        db.checkpoint("parents")
        db.connection.execute("UPDATE artifacts SET committed=1 WHERE id=?", (identity,))
    else:
        get_committed_artifact(db, identity, kind)
    if binding is not None:
        namespace, request_id, inputs = binding
        existing = lookup_request(db, namespace, request_id, inputs)
        if existing is None:
            db.connection.execute("INSERT INTO bindings VALUES(?,?,?,?)",
                                  (namespace, request_id, canonical_json(inputs), identity))
        elif existing != identity:
            raise RequestConflictError("binding result differs")
        db.checkpoint("binding")
    return identity
