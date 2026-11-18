from copy import deepcopy
from fractions import Fraction as F
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from monitor.archive.database import ArchiveDatabase
from monitor.archive.delivery import export_result, verify_bundle, build_bundle, DeliveryError
from monitor.archive.codec import loads, payload_digest
from monitor.analysis.jobs import analyze
from test_shared_ranking import (pipeline, observations, rectangle, point,
                                 snapshot, probabilities)


class TestDelivery(unittest.TestCase):
    def test_recompute_and_write_failure(self):
        with TemporaryDirectory(prefix="monitor-delivery-") as folder:
            path = Path(folder) / "archive.sqlite"
            target = Path(folder) / "report.json"
            with ArchiveDatabase(path) as db:
                rows = observations([("A", "1/3", 1, 0), ("B", "5/6", 0, 0), ("C", "-5/3", 0, 0)])
                scoring, _, _ = pipeline(db, rows=rows,
                                          scenarios=[rectangle("1/2"), point("1/2")],
                                          order=["C", "A", "B"])
                report = analyze(db, "delivery", scoring, 1, ["B", "A"])
                import sys
                previous = sys.getprofile()

                def forbid_computation(frame, event, arg):
                    if event == "call" and frame.f_code.co_name in ("compile_scores", "integrate_ranking_event"):
                        raise AssertionError("export attempted computation")

                sys.setprofile(forbid_computation)
                try:
                    export_result(db, report, target)
                finally:
                    sys.setprofile(previous)
                old = target.read_bytes()
                before = snapshot(db)
                for stage in ("write", "before_replace"):
                    reached = []

                    def fail(current):
                        reached.append(current)
                        if current == stage:
                            raise OSError("interrupted export")

                    with self.assertRaises(DeliveryError):
                        export_result(db, report, target, fault=fail)
                    self.assertIn(stage, reached)
                    self.assertEqual(target.read_bytes(), old)
                    self.assertEqual(list(Path(folder).glob(".report.json.*.tmp")), [])
                    self.assertEqual(snapshot(db), before)
                export_result(db, report, target)
                self.assertEqual(target.read_bytes(), old)
            path.unlink()
            actual = verify_bundle(target.read_text(encoding="utf-8"))
            self.assertEqual(probabilities(actual), ([F(7, 8), F(1, 8)], F(0), F(1)))
            self.assertEqual(actual["request"]["targets"], ["B", "A"])
            bundle = loads(target.read_text(encoding="utf-8"))
            observations_in_bundle = [r for r in bundle["artifacts"] if r["kind"] == "observation"]
            self.assertEqual([r["payload"]["observation"]["revision"] for r in observations_in_bundle],
                             ["r1", "r1", "r1"])
            values = {r["payload"]["observation"]["station"]: r["payload"]["observation"]["x"]
                      for r in observations_in_bundle}
            self.assertEqual(values, {"A": F(1, 3), "B": F(5, 6), "C": F(-5, 3)})

    def test_reject_corrupt_bundle(self):
        with ArchiveDatabase() as db:
            scoring, _, _ = pipeline(db)
            report = analyze(db, "report", scoring, 1, ["A", "B"])
            bundle = build_bundle(db, report)
        broken = deepcopy(bundle)
        root = next(r for r in broken["artifacts"] if r["id"] == broken["root"])
        root["payload"]["joint"] = F(1, 4)
        broken["source_digest"] = payload_digest(dict(root=broken["root"], artifacts=broken["artifacts"]))
        with self.assertRaises(DeliveryError):
            verify_bundle(broken)
        root["digest"] = payload_digest(root["payload"])
        root["id"] = "report:" + root["digest"]
        broken["root"] = root["id"]
        broken["source_digest"] = payload_digest(dict(root=broken["root"], artifacts=broken["artifacts"]))
        with self.assertRaisesRegex(DeliveryError, "offline probability"):
            verify_bundle(broken)
        missing = deepcopy(bundle)
        missing["artifacts"] = missing["artifacts"][:-1]
        missing["source_digest"] = payload_digest(dict(root=missing["root"], artifacts=missing["artifacts"]))
        with self.assertRaises(DeliveryError):
            verify_bundle(missing)

    def test_manifest_replay(self):
        import json
        from monitor.analysis.jobs import run_daily_manifest
        manifest = json.loads(Path("run_manifest.json").read_text(encoding="utf-8"))
        with ArchiveDatabase() as db:
            first = run_daily_manifest(db, manifest)
            before = snapshot(db)
            db.initialize()
            self.assertEqual(run_daily_manifest(db, manifest), first)
            self.assertEqual(snapshot(db), before)
            from monitor.archive.reading import read_result
            actual = read_result(db, first["report"])["payload"]
            self.assertEqual(probabilities(actual), ([F(3, 4), F(3, 4)], F(1, 2), F(1)))
