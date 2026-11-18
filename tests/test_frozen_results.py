from copy import deepcopy
from fractions import Fraction as F
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from monitor.archive.database import ArchiveDatabase
from monitor.archive.reading import read_result
from monitor.observations.revisions import ingest
from monitor.observations.cohorts import freeze_cohort
from monitor.calibration.models import save_calibration
from monitor.calibration.schema import CalibrationError
from monitor.scoring.jobs import score
from monitor.scoring.results import load_score_result
from monitor.analysis.jobs import analyze
from test_shared_ranking import (pipeline, observations, calibration, result,
                                 probabilities, snapshot)


class TestFrozenResults(unittest.TestCase):
    def test_correction_and_reopen(self):
        with TemporaryDirectory(prefix="monitor-test-") as folder:
            path = Path(folder) / "archive.sqlite"
            with ArchiveDatabase(path) as db:
                scoring, cohort, model = pipeline(db)
                old_report = analyze(db, "original", scoring, 1, ["A", "B"])
                original = read_result(db, old_report)
                original_frame = load_score_result(db, scoring).frame
                corrected = observations()[0]
                corrected.update(revision="r2", x=3)
                ingest(db, "correction", [corrected])
                same_inputs = score(db, "old-cohort-again", cohort, model)
                self.assertEqual(same_inputs, scoring)
                self.assertEqual(load_score_result(db, same_inputs).frame, original_frame)
                new_cohort = freeze_cohort(db, [
                    ("station-lab", "A", "r2"), ("station-lab", "B", "r1"), ("station-lab", "C", "r1")])
                changed = score(db, "corrected-observations", new_cohort, model)
                self.assertEqual(probabilities(result(db, changed, request="corrected-observations")),
                                 ([F(1), F(0)], F(0), F(1)))
                new_calibration = calibration()
                new_calibration["revision"] = "c2"
                new_calibration["rule"]["beta"] = 0
                new_model = save_calibration(db, new_calibration)
                self.assertNotEqual(new_model, model)
                recalibrated = score(db, "recalibrated", cohort, new_model)
                self.assertEqual(probabilities(result(db, recalibrated, request="recalibrated")),
                                 ([F(1), F(1)], F(1), F(1)))
                before = snapshot(db)
                conflicting = deepcopy(new_calibration)
                conflicting["rule"]["beta"] = 2
                with self.assertRaises(CalibrationError):
                    save_calibration(db, conflicting)
                self.assertEqual(snapshot(db), before)
                db.initialize()
                self.assertEqual(snapshot(db), before)
                copy = read_result(db, old_report)
                copy["payload"]["marginals"][0] = F(99)
                source = next(k for k, v in copy["lineage"].items() if v["kind"] == "observation")
                copy["lineage"][source]["payload"]["observation"]["x"] = F(99)
                self.assertEqual(read_result(db, old_report), original)
            with ArchiveDatabase(path) as reopened:
                before_profile = sys.getprofile()

                def forbid_computation(frame, event, arg):
                    if event == "call" and frame.f_code.co_name in (
                            "compile_scores", "integrate_ranking_event", "iter_event_regions"):
                        raise AssertionError("historical read attempted computation")

                sys.setprofile(forbid_computation)
                try:
                    self.assertEqual(read_result(reopened, old_report), original)
                finally:
                    sys.setprofile(before_profile)
                self.assertEqual(read_result(reopened, cohort)["payload"],
                                 original["lineage"][cohort]["payload"])
