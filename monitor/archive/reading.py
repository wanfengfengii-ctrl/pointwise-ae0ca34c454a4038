from copy import deepcopy
import sqlite3
from monitor.archive.catalog import resolve_lineage, PublicationError
from monitor.archive.database import StorageError


class ReadError(StorageError):
    pass


def read_lineage(db, artifact_id):
    try:
        return deepcopy(resolve_lineage(db, artifact_id))
    except (PublicationError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
        raise ReadError("cannot read fixed lineage: " + str(artifact_id)) from exc


def read_result(db, artifact_id):
    lineage = read_lineage(db, artifact_id)
    root = lineage[artifact_id]
    return dict(id=artifact_id, kind=root["kind"], payload=deepcopy(root["payload"]),
                lineage=lineage)
