from contextlib import contextmanager
import sqlite3


class StorageError(RuntimeError):
    pass


_DDL = (
    "CREATE TABLE meta(version INTEGER NOT NULL)",
    "INSERT INTO meta VALUES(1)",
    "CREATE TABLE artifacts(id TEXT PRIMARY KEY, kind TEXT NOT NULL, body TEXT NOT NULL,"
    " digest TEXT NOT NULL, committed INTEGER NOT NULL CHECK(committed IN(0,1)))",
    "CREATE TABLE parents(child TEXT NOT NULL REFERENCES artifacts(id), role TEXT NOT NULL,"
    " parent TEXT NOT NULL REFERENCES artifacts(id), PRIMARY KEY(child,role))",
    "CREATE TABLE bindings(namespace TEXT NOT NULL, request TEXT NOT NULL, input TEXT NOT NULL,"
    " artifact TEXT NOT NULL REFERENCES artifacts(id), PRIMARY KEY(namespace,request))",
    "CREATE TABLE observations(source TEXT NOT NULL, station TEXT NOT NULL, revision TEXT NOT NULL,"
    " artifact TEXT NOT NULL REFERENCES artifacts(id), PRIMARY KEY(source,station,revision))",
    "CREATE TABLE calibration_revisions(source TEXT NOT NULL, revision TEXT NOT NULL,"
    " artifact TEXT NOT NULL REFERENCES artifacts(id), PRIMARY KEY(source,revision))",
    "CREATE TABLE receipts(batch TEXT PRIMARY KEY, input TEXT NOT NULL,"
    " artifact TEXT NOT NULL REFERENCES artifacts(id))",
)


class ArchiveDatabase:
    def __init__(self, path=":memory:"):
        self.connection = sqlite3.connect(str(path), isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.fault = None
        self._depth = 0
        try:
            self.initialize()
        except Exception:
            self.close()
            raise

    def initialize(self):
        tables = {r[0] for r in self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        try:
            if not tables:
                with self.transaction():
                    for statement in _DDL:
                        self.connection.execute(statement)
            else:
                expected = {"meta", "artifacts", "parents", "bindings", "observations",
                            "calibration_revisions", "receipts"}
                if tables != expected:
                    raise StorageError("unexpected or incomplete archive structure")
                for statement in (_DDL[0], *_DDL[2:]):
                    name = statement.split("(", 1)[0].split()[-1]
                    saved = self.connection.execute(
                        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()[0]
                    if " ".join(saved.split()) != " ".join(statement.split()):
                        raise StorageError("archive table definition differs: " + name)
                if [tuple(r) for r in self.connection.execute("SELECT version FROM meta")] != [(1,)]:
                    raise StorageError("unsupported archive version")
                columns = {
                    "artifacts": ["id", "kind", "body", "digest", "committed"],
                    "parents": ["child", "role", "parent"],
                    "bindings": ["namespace", "request", "input", "artifact"],
                    "observations": ["source", "station", "revision", "artifact"],
                    "calibration_revisions": ["source", "revision", "artifact"],
                    "receipts": ["batch", "input", "artifact"],
                }
                for table, names in columns.items():
                    actual = [r[1] for r in self.connection.execute("PRAGMA table_info(" + table + ")")]
                    if actual != names:
                        raise StorageError("invalid columns: " + table)
                if self.connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise StorageError("broken archive references")
        except sqlite3.Error as exc:
            raise StorageError("cannot initialize archive") from exc

    @contextmanager
    def transaction(self):
        if self._depth:
            raise StorageError("nested write transactions are unsupported")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            raise StorageError("cannot begin archive transaction") from exc
        self._depth = 1
        try:
            yield
            self.connection.commit()
        except sqlite3.Error as exc:
            self.connection.rollback()
            raise StorageError("archive transaction failed") from exc
        except BaseException:
            self.connection.rollback()
            raise
        finally:
            self._depth = 0

    def checkpoint(self, stage):
        if self.fault is not None:
            self.fault(stage)

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
