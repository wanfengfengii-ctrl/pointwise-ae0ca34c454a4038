from copy import deepcopy
import unittest
from monitor.archive.database import ArchiveDatabase
from monitor.calibration.models import save_calibration
from monitor.calibration.schema import CalibrationError
from test_shared_ranking import calibration, rectangle, point, pipeline, snapshot


class TestCalibration(unittest.TestCase):
    def test_invalid_distribution(self):
        with ArchiveDatabase() as db:
            pipeline(db)
            before = snapshot(db)
            cases = [[rectangle(-1), point(2)], [rectangle("1/2")],
                     [rectangle(bounds=(0, 0, -1, 1))],
                     [point(1), rectangle(0, (0, 1, 2, 2))], [], [point("1/5")] * 5]
            for scenarios in cases:
                with self.subTest(scenarios=scenarios), self.assertRaises(CalibrationError):
                    save_calibration(db, calibration(scenarios))
                self.assertEqual(snapshot(db), before)

    def test_rule_and_distribution(self):
        with ArchiveDatabase() as db:
            before = snapshot(db)
            bad_values = (("alpha", True), ("beta", 0.25), ("gamma", None),
                          ("delta", "3/0"), ("input_unit", "mg/L"),
                          ("output_unit", "ug/m3"), ("version", "other"))
            for key, value in bad_values:
                raw = calibration()
                raw["rule"][key] = value
                with self.subTest(key=key), self.assertRaises(CalibrationError):
                    save_calibration(db, raw)
                self.assertEqual(snapshot(db), before)
            raw = calibration()
            raw["variables"] = ["V", "U"]
            with self.assertRaises(CalibrationError):
                save_calibration(db, raw)
            missing = deepcopy(calibration())
            del missing["rule"]["delta"]
            with self.assertRaises(CalibrationError):
                save_calibration(db, missing)
            self.assertEqual(snapshot(db), before)
