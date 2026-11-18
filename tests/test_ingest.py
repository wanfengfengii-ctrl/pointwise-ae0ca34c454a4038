from copy import deepcopy
import unittest
from monitor.archive.database import ArchiveDatabase
from monitor.observations.revisions import ingest, get_observation_revision
from monitor.observations.schema import ObservationError, parse_rational
from monitor.observations.cohorts import freeze_cohort
from test_shared_ranking import pipeline, observations, snapshot


class TestIngest(unittest.TestCase):
    def test_revision_conflict(self):
        with ArchiveDatabase() as db:
            pipeline(db)
            before = snapshot(db)
            conflict = observations()[0]
            conflict["x"] = 99
            fresh = observations([("D", 2, 0, 0), ("E", 3, 0, 0)])
            for rows in ([fresh[0], conflict, fresh[1]], [*fresh, conflict]):
                with self.assertRaises(ObservationError):
                    ingest(db, "rejected", rows)
                self.assertEqual(snapshot(db), before)
                for row in fresh:
                    with self.assertRaises(ObservationError):
                        get_observation_revision(db, row["source"], row["station"], row["revision"])
                with self.assertRaises(ObservationError):
                    freeze_cohort(db, [("station-lab", "D", "r1"), ("station-lab", "A", "r1")])

    def test_exact_replay(self):
        with ArchiveDatabase() as db:
            rows = observations()
            first = ingest(db, "day", rows)
            before = snapshot(db)
            alternate = deepcopy(rows)
            alternate[0]["r_u"] = "2/2"
            alternate[1]["x"] = "0/7"
            self.assertEqual(ingest(db, "day", alternate), first)
            self.assertEqual(snapshot(db), before)
            alternate.reverse()
            with self.assertRaises(ObservationError):
                ingest(db, "day", alternate)
            with self.assertRaises(ObservationError):
                ingest(db, "dup", [rows[0], rows[0]])
            for value in (True, 1.0, None, "", "1", "1/0", "nan", "1.2/2"):
                bad = deepcopy(rows)
                bad[0]["x"] = value
                with self.subTest(value=value), self.assertRaises(ObservationError):
                    ingest(db, "invalid", bad)
                self.assertEqual(snapshot(db), before)
            with self.assertRaises(ObservationError):
                ingest(db, "", rows)
            self.assertEqual(parse_rational("-4/-6"), parse_rational("2/3"))

    def test_cohort_rejections(self):
        with ArchiveDatabase() as db:
            ingest(db, "day", observations())
            before = snapshot(db)
            for refs in ([("station-lab", "A", "r1")],
                         [("station-lab", "A", "r1")] * 2,
                         [("station-lab", "A", "r1"), ("station-lab", "B", "unknown")]):
                with self.assertRaises(ObservationError):
                    freeze_cohort(db, refs)
                self.assertEqual(snapshot(db), before)
