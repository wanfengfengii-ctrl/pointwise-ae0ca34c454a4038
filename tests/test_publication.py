from copy import deepcopy
import unittest
from monitor.archive.database import ArchiveDatabase
from monitor.archive.catalog import RequestConflictError
from monitor.archive.reading import read_result, ReadError
from monitor.observations.cohorts import freeze_cohort
from monitor.calibration.models import save_calibration
from monitor.scoring.jobs import score, ScoringError
from monitor.analysis.jobs import analyze
from test_shared_ranking import pipeline, calibration, snapshot


class TestPublication(unittest.TestCase):
    def test_rollback_and_retry(self):
        for stage in ("payload", "parents", "binding"):
            with self.subTest(stage=stage), ArchiveDatabase() as db:
                scoring, cohort, model = pipeline(db)
                old_id = analyze(db, "old", scoring, 1, ["A", "B"])
                old = read_result(db, old_id)
                before = snapshot(db)
                reached = []

                def fail(current):
                    reached.append(current)
                    if current == stage:
                        raise OSError("injected publication failure at " + stage)

                db.fault = fail
                with self.assertRaises(OSError):
                    analyze(db, "retry", scoring, 2, ["A", "B"])
                db.fault = None
                self.assertIn(stage, reached)
                self.assertEqual(snapshot(db), before)
                self.assertEqual(read_result(db, old_id), old)
                report = analyze(db, "retry", scoring, 2, ["A", "B"])
                after = snapshot(db)
                self.assertEqual(len(after["artifacts"]), len(before["artifacts"]) + 1)
                self.assertEqual(analyze(db, "retry", scoring, 2, ["A", "B"]), report)
                self.assertEqual(snapshot(db), after)
                new_raw = calibration()
                new_raw["revision"] = "score-failure"
                new_raw["rule"]["beta"] = 2
                new_model = save_calibration(db, new_raw)
                before_score = snapshot(db)
                db.fault = fail
                with self.assertRaises(OSError):
                    score(db, "score-retry", cohort, new_model)
                db.fault = None
                self.assertEqual(snapshot(db), before_score)
                new_score = score(db, "score-retry", cohort, new_model)
                self.assertEqual(score(db, "score-retry", cohort, new_model), new_score)

    def test_request_binding(self):
        with ArchiveDatabase() as db:
            scoring, cohort, model = pipeline(db)
            report = analyze(db, "same", scoring, 1, ["A", "B"])
            alternate_cohort = freeze_cohort(db, [
                ("station-lab", "B", "r1"), ("station-lab", "A", "r1"), ("station-lab", "C", "r1")])
            alternate_score = score(db, "alternate", alternate_cohort, model)
            before = snapshot(db)
            for sid, k, targets in ((alternate_score, 1, ["A", "B"]),
                                    (scoring, 2, ["A", "B"]), (scoring, 1, ["B", "A"])):
                with self.assertRaises(RequestConflictError):
                    analyze(db, "same", sid, k, targets)
                self.assertEqual(snapshot(db), before)
            self.assertEqual(analyze(db, "same", scoring, 1, ["A", "B"]), report)
            with self.assertRaises(RequestConflictError):
                score(db, "score", alternate_cohort, model)
            # Operation namespaces are independent.
            self.assertEqual(analyze(db, "score", scoring, 1, ["A", "B"]), report)
            with self.assertRaises(ScoringError):
                score(db, "unknown", "missing-cohort", model)

    def test_invalid_read(self):
        with ArchiveDatabase() as db:
            scoring, _, _ = pipeline(db)
            for identity in ("missing", "report:not-committed"):
                if identity.startswith("report:"):
                    db.connection.execute("INSERT INTO artifacts VALUES(?,?,?,?,0)",
                                          (identity, "report", "{}", "broken"))
                with self.assertRaises(ReadError):
                    read_result(db, identity)
            root = read_result(db, scoring)
            source = next(k for k, v in root["lineage"].items() if v["kind"] == "observation")
            body = db.connection.execute("SELECT body FROM artifacts WHERE id=?", (source,)).fetchone()[0]
            db.connection.execute("UPDATE artifacts SET body=? WHERE id=?", (body[:-1], source))
            with self.assertRaises(ReadError):
                read_result(db, scoring)
            db.connection.execute("UPDATE artifacts SET body=? WHERE id=?", (body, source))
            self.assertEqual(read_result(db, scoring), root)

    def test_initialization_rejects_broken_structure(self):
        from monitor.archive.database import StorageError
        with ArchiveDatabase() as db:
            db.connection.execute("DROP TABLE receipts")
            with self.assertRaises(StorageError):
                db.initialize()
