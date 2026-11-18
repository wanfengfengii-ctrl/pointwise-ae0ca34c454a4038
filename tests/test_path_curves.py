from copy import deepcopy
from fractions import Fraction as F
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

from monitor.archive.database import ArchiveDatabase
from monitor.archive.catalog import RequestConflictError
from monitor.archive.codec import canonical_json, payload_digest
from monitor.archive.delivery import (
    DeliveryError, build_bundle, export_result, verify_bundle)
from monitor.archive.reading import read_result, ReadError
from monitor.calibration.models import save_calibration, load_calibration_model
from monitor.observations.revisions import ingest
from monitor.observations.cohorts import freeze_cohort
from monitor.scoring.jobs import score, score_path, ScoringError
from monitor.scoring.results import load_score_result
from monitor.analysis.jobs import analyze, analyze_path
from monitor.analysis.results import AnalysisError
from monitor.analysis.events import build_ranking_event
from monitor.analysis.measure import integrate_ranking_event
from test_shared_ranking import (observations, pipeline, point, probabilities,
                                 rectangle, snapshot)


def _row(station, x=0, r_u=0, r_v=0):
    return dict(source="path-lab", station=station, revision="r1",
                x=x, r_u=r_u, r_v=r_v, unit="ug/m3")


def _calibration(scenarios, alpha=1, revision="c1"):
    return dict(source="path-calibration", revision=revision, variables=["U", "V"],
                scenarios=scenarios,
                rule=dict(delta=0, alpha=alpha, beta=1, gamma=1,
                          version="affine-v1", input_unit="ug/m3", output_unit="score"))


def seed(db, rows, scenarios, alpha=1, reverse=False, batch="path-input",
         request="parent-score"):
    ingest(db, batch, rows)
    ordered = rows[::-1] if reverse else rows
    cohort = freeze_cohort(db, [(r["source"], r["station"], r["revision"]) for r in ordered])
    model = save_calibration(db, _calibration(scenarios, alpha))
    return score(db, request, cohort, model)


def poly(*values):
    return [F(v) for v in values]


def curve_values(marginals, joint, union):
    return dict(marginals=[F(v) for v in marginals], joint=F(joint), union=F(union))


def piece(marginals, joint, union):
    return dict(marginals=[poly(*p) for p in marginals], joint=poly(*joint),
                union=poly(*union))


def evaluate(curve, t):
    """Independent evaluator for the returned payload (not the solved form)."""
    t = F(t)
    knots = curve["breakpoints"]
    if t in knots:
        return curve["values"][knots.index(t)]
    index = next(i for i in range(len(knots) - 1) if knots[i] < t < knots[i + 1])
    selected = curve["pieces"][index]

    def at(coefficients):
        return sum((c * t ** i for i, c in enumerate(coefficients)), F(0))

    return dict(marginals=[at(p) for p in selected["marginals"]],
                joint=at(selected["joint"]), union=at(selected["union"]))


def pointwise(db, path_id, k, targets, t):
    """V1 single-point integration as an independent oracle."""
    saved = read_result(db, path_id)
    parent = load_score_result(db, saved["payload"]["score_id"])
    model = load_calibration_model(db, parent.frame.model_id)
    from dataclasses import replace
    moved = replace(parent.frame, scores=tuple(
        replace(row, a=row.a + extra[3] * F(t))
        for row, extra in zip(parent.frame.scores, saved["payload"]["coefficients"])))
    from monitor.analysis.results import parse_analysis_request
    request = parse_analysis_request(moved, saved["payload"]["score_id"], k, targets)
    result = integrate_ranking_event(build_ranking_event(moved, request), model)
    return dict(marginals=list(result.marginals), joint=result.joint, union=result.union)


def dense_times(curve):
    times = set(curve["breakpoints"])
    for left, right in zip(curve["breakpoints"], curve["breakpoints"][1:]):
        step = right - left
        for numerator in (1, 2, 3, 5, 7, 11, 13):
            times.add(left + step * F(numerator, 17))
    return sorted(times)


class TestPathCurves(unittest.TestCase):
    def test_hand_mixed(self):
        rows = [_row("A", r_u=1), _row("B", r_u=-1), _row("C")]
        with ArchiveDatabase() as db:
            parent = seed(db, rows, [dict(rectangle("1/2")), point("1/2")])
            path = score_path(db, "p", parent, {"A": 1, "B": -1, "C": 0}, -1, 1)
            curve = read_result(db, analyze_path(db, "c", path, 1, ["A", "B"]))["payload"]
        expected = dict(
            breakpoints=[F(-1), F(0), F(1)],
            pieces=[piece([(F(1, 4), F(1, 4), 0), (F(3, 4), F(-1, 4), 0)], (0, 0, 0), (1, 0, 0)),
                    piece([(F(3, 4), F(1, 4), 0), (F(1, 4), F(-1, 4), 0)], (0, 0, 0), (1, 0, 0))],
            values=[curve_values([0, 1], 0, 1),
                    curve_values([F(3, 4), F(3, 4)], F(1, 2), 1),
                    curve_values([1, 0], 0, 1)])
        self.assertEqual({k: curve[k] for k in expected}, expected)
        self.assertEqual(curve["request"], dict(path_id=path, k=1, targets=["A", "B"]))
        self.assertEqual(curve["parents"], {"path": path})
        # The isolated joint value at t=0 must not be merged into either side:
        # both adjacent polynomials have joint identically zero.
        self.assertEqual(curve["pieces"][0]["joint"], [F(0), F(0), F(0)])
        self.assertEqual(curve["pieces"][1]["joint"], [F(0), F(0), F(0)])
        self.assertEqual(curve["values"][1]["joint"], F(1, 2))

    def test_hand_quadratic(self):
        rows = [_row("A"), _row("B", r_u=1), _row("C", r_v=1)]
        with ArchiveDatabase() as db:
            parent = seed(db, rows, [dict(rectangle(1, (0, 1, 0, 1)))], alpha=2)
            path = score_path(db, "p", parent, {"A": "1/2", "B": 0, "C": 0}, -1, 2)
            curve = read_result(db, analyze_path(db, "c", path, 1, ["A"]))["payload"]
        expected = dict(
            breakpoints=[F(-1), F(0), F(1), F(2)],
            pieces=[piece([(0, 0, 0)], (0, 0, 0), (0, 0, 0)),
                    piece([(0, 0, 1)], (0, 0, 1), (0, 0, 1)),
                    piece([(1, 0, 0)], (1, 0, 0), (1, 0, 0))],
            values=[curve_values([0], 0, 0), curve_values([0], 0, 0),
                    curve_values([1], 1, 1), curve_values([1], 1, 1)])
        self.assertEqual({k: curve[k] for k in expected}, expected)

    def test_hand_spatial_tie(self):
        rows = [_row("A"), _row("B"), _row("C")]
        with ArchiveDatabase() as db:
            parent = seed(db, rows, [dict(rectangle())], alpha=1)
            path = score_path(db, "p", parent, {"A": 1, "B": -1, "C": 0}, -1, 1)
            curve = read_result(db, analyze_path(db, "c", path, 1, ["A", "B"]))["payload"]
        self.assertEqual(curve["breakpoints"], [F(-1), F(0), F(1)])
        self.assertEqual(curve["values"][1], curve_values([1, 1], 1, 1))
        self.assertEqual(curve["values"][0], curve_values([0, 1], 0, 1))
        self.assertEqual(curve["values"][2], curve_values([1, 0], 0, 1))
        self.assertNotEqual(curve["pieces"][0], curve["pieces"][1])

    def _solve(self, rows, scenarios, alpha, directions, lo, hi, k, targets,
               reverse=False):
        db = ArchiveDatabase()
        self.addCleanup(db.close)
        parent = seed(db, rows, scenarios, alpha, reverse=reverse)
        path = score_path(db, "p", parent, directions, lo, hi)
        curve = read_result(db, analyze_path(db, "c", path, k, targets))["payload"]
        return db, path, curve

    def test_concurrent_comparison_planes(self):
        # A=U, B=V, C=2U-V+t.  All three pairwise comparison lines meet at
        # the rectangle centre exactly at t=0, creating a cell that exists on
        # neither open side.
        rows = [_row("A", r_u=1), _row("B", r_v=1), _row("C", r_u=2, r_v=-1)]
        db, path, curve = self._solve(
            rows, [dict(rectangle())], 1, {"A": 0, "B": 0, "C": 1}, -1, 1, 1, ["C", "A"])
        self.assertIn(F(0), curve["breakpoints"])
        for t in dense_times(curve):
            self.assertEqual(evaluate(curve, t), pointwise(db, path, 1, ["C", "A"], t), t)

    def test_parallel_planes_become_coincident(self):
        # A=U+t, B=2U-1, C=0: the A|B and A|C comparison lines are parallel
        # and coincide exactly at t=-1/2, where their order swaps at once.
        rows = [_row("A", r_u=1), _row("B", x=-1, r_u=2), _row("C")]
        db, path, curve = self._solve(
            rows, [dict(rectangle())], 1, {"A": 1, "B": 0, "C": 0}, -1, 1, 1, ["A", "B"])
        self.assertIn(F(-1, 2), curve["breakpoints"])
        for t in dense_times(curve):
            self.assertEqual(evaluate(curve, t), pointwise(db, path, 1, ["A", "B"], t), t)

    def test_constant_comparison_and_two_targets(self):
        # C is spatially constant, so its comparison with A flips sign at an
        # isolated parameter while the spatial arrangement is untouched.
        rows = [_row("A", r_u=1), _row("B", r_v=1), _row("C", x=0)]
        db, path, curve = self._solve(
            rows, [dict(rectangle())], 1, {"A": 1, "B": -1, "C": 0}, -2, 2, 2,
            ["B", "A"])
        for t in dense_times(curve):
            self.assertEqual(evaluate(curve, t), pointwise(db, path, 2, ["B", "A"], t), t)
        for index, selected in enumerate(curve["pieces"]):
            left, right = curve["breakpoints"][index], curve["breakpoints"][index + 1]
            for step in (1, 2, 3):
                t = left + (right - left) * F(step, 4)
                values = dict(marginals=[sum((c * t ** i for i, c in enumerate(p)), F(0))
                                         for p in selected["marginals"]],
                              joint=sum((c * t ** i for i, c in enumerate(selected["joint"])), F(0)),
                              union=sum((c * t ** i for i, c in enumerate(selected["union"])), F(0)))
                self.assertEqual(values["union"],
                                 values["marginals"][0] + values["marginals"][1]
                                 - values["joint"])

    def test_empty_targets_and_point_interval(self):
        rows = [_row("A", r_u=1), _row("B", r_u=-1), _row("C")]
        db = ArchiveDatabase()
        self.addCleanup(db.close)
        parent = seed(db, rows, [dict(rectangle("1/2")), point("1/2")])
        path = score_path(db, "p", parent, {"A": 1, "B": -1, "C": 0}, -1, 1)
        empty = read_result(db, analyze_path(db, "e", path, 1, []))["payload"]
        self.assertEqual(empty["breakpoints"], [F(-1), F(1)])
        self.assertEqual(empty["pieces"], [piece([], (0, 0, 0), (0, 0, 0))])
        self.assertEqual(empty["values"], [curve_values([], 0, 0)] * 2)
        single = score_path(db, "q", parent, {"A": 1, "B": -1, "C": 0}, 0, 0)
        report = read_result(db, analyze_path(db, "s", single, 1, ["A", "B"]))["payload"]
        self.assertEqual(report["breakpoints"], [F(0)])
        self.assertEqual(report["pieces"], [])
        self.assertEqual(report["values"], [curve_values([F(3, 4), F(3, 4)], F(1, 2), 1)])

    def test_zero_directions_are_constant(self):
        rows = [_row("A", r_u=1), _row("B", r_u=-1), _row("C")]
        db = ArchiveDatabase()
        self.addCleanup(db.close)
        parent = seed(db, rows, [dict(rectangle("1/2")), point("1/2")])
        path = score_path(db, "p", parent, {"A": 0, "B": 0, "C": 0}, -3, 2)
        curve = read_result(db, analyze_path(db, "c", path, 1, ["A", "B"]))["payload"]
        self.assertEqual(curve["breakpoints"], [F(-3), F(2)])
        self.assertEqual(curve["pieces"],
                         [piece([(F(3, 4), 0, 0), (F(3, 4), 0, 0)], (F(1, 2), 0, 0), (1, 0, 0))])
        self.assertEqual(curve["values"][0], curve["values"][1])

    def test_spatial_tie_knot_value_is_isolated(self):
        # At t=0 both A and B are selected only at the tie; the joint value 1
        # is a point value that neither open interval polynomial reports.
        rows = [_row("A"), _row("B"), _row("C")]
        db = ArchiveDatabase()
        self.addCleanup(db.close)
        parent = seed(db, rows, [dict(rectangle())])
        path = score_path(db, "p", parent, {"A": 1, "B": -1, "C": 0}, -1, 1)
        curve = read_result(db, analyze_path(db, "c", path, 1, ["A", "B"]))["payload"]
        for adjacent in curve["pieces"]:
            self.assertEqual(adjacent["joint"], [F(0), F(0), F(0)])
        self.assertEqual(curve["values"][1], curve_values([1, 1], 1, 1))

    def test_zero_weight_and_overlap_are_measured(self):
        rows = [_row("A", r_u=1), _row("B", r_u=-1), _row("C")]
        scenarios = [dict(rectangle(0)), point("1/2"), rectangle("1/2", (-1, 2, -2, 1))]
        db = ArchiveDatabase()
        self.addCleanup(db.close)
        parent = seed(db, rows, scenarios)
        path = score_path(db, "p", parent, {"A": 1, "B": -1, "C": 0}, -1, 1)
        curve = read_result(db, analyze_path(db, "c", path, 1, ["A", "B"]))["payload"]
        for t in dense_times(curve):
            self.assertEqual(evaluate(curve, t), pointwise(db, path, 1, ["A", "B"], t), t)

    def test_reordered_cohort_transports_directions(self):
        rows = [_row("A", r_u=1), _row("B", r_u=-1), _row("C")]
        db = ArchiveDatabase()
        self.addCleanup(db.close)
        directions = {"A": "1/3", "B": "-2/3", "C": "5/7"}
        first = seed(db, rows, [dict(rectangle("1/2")), point("1/2")])
        path_a = score_path(db, "p", first, directions, -1, 2)
        curve_a = read_result(db, analyze_path(db, "c", path_a, 1, ["A", "B"]))["payload"]
        second = seed(db, rows, [dict(rectangle("1/2")), point("1/2")],
                      reverse=True, batch="path-input-reversed",
                      request="parent-score-reversed")
        # Same physics, different cohort order: the path is re-created against
        # the reordered score parent, and directions travel by station.
        path_b = score_path(db, "p2", second, dict(reversed(list(directions.items()))), -1, 2)
        curve_b = read_result(db, analyze_path(db, "c2", path_b, 1, ["A", "B"]))["payload"]
        fields = ("breakpoints", "pieces", "values")
        self.assertEqual({k: curve_b[k] for k in fields}, {k: curve_a[k] for k in fields})
        saved = read_result(db, path_b)["payload"]
        self.assertEqual(saved["stations"], ["C", "B", "A"])
        self.assertEqual(saved["directions"], [F(5, 7), F(-2, 3), F(1, 3)])

    def test_rejects_invalid_paths(self):
        rows = [_row("A", r_u=1), _row("B", r_u=-1), _row("C")]
        db = ArchiveDatabase()
        self.addCleanup(db.close)
        parent = seed(db, rows, [dict(rectangle())])
        before = snapshot(db)
        bad = [{"A": 1, "B": 1}, {"A": 1, "B": 1, "C": 0, "D": 0}, "A",
               {"A": True, "B": 1, "C": 0}, {"A": 1.0, "B": 1, "C": 0},
               {"A": None, "B": 1, "C": 0}, {"A": "1/0", "B": 1, "C": 0}]
        for directions in bad:
            with self.subTest(directions=directions), self.assertRaises(ScoringError):
                score_path(db, "bad", parent, directions, -1, 1)
            self.assertEqual(snapshot(db), before)
        for lo, hi in ((1, -1), (True, 1), ("1/0", 1), (1.5, 1)):
            with self.subTest(lo=lo, hi=hi), self.assertRaises(ScoringError):
                score_path(db, "bad", parent, {"A": 1, "B": 1, "C": 0}, lo, hi)
            self.assertEqual(snapshot(db), before)
        with self.assertRaises(ScoringError):
            score_path(db, "bad", "score:missing", {"A": 1, "B": 1, "C": 0}, -1, 1)
        path = score_path(db, "p", parent, {"A": 1, "B": 1, "C": 0}, -1, 1)
        with self.assertRaises(ScoringError):
            score_path(db, "nested", path, {"A": 1, "B": 1, "C": 0}, -1, 1)
        self.assertEqual(read_result(db, path)["kind"], "score_path")

    def test_rejects_invalid_analysis(self):
        rows = [_row("A", r_u=1), _row("B", r_u=-1), _row("C")]
        db = ArchiveDatabase()
        self.addCleanup(db.close)
        parent = seed(db, rows, [dict(rectangle())])
        path = score_path(db, "p", parent, {"A": 1, "B": -1, "C": 0}, -1, 1)
        before = snapshot(db)
        for k, targets in ((0, ["A"]), (4, ["A"]), (True, ["A"]), (1.0, ["A"]),
                           (1, ["unknown"]), (1, ["A", "A"]), (1, ["A", "B", "C"]),
                           (1, "A"), (1, [None])):
            with self.subTest(k=k, targets=targets), self.assertRaises(AnalysisError):
                analyze_path(db, "bad", path, k, targets)
            self.assertEqual(snapshot(db), before)
        for identity in ("score_path:missing", parent):
            with self.assertRaises(AnalysisError):
                analyze_path(db, "bad", identity, 1, ["A"])
            self.assertEqual(snapshot(db), before)

    def test_freeze_correction_and_reopen(self):
        rows = [_row("A", r_u=1), _row("B", r_u=-1), _row("C")]
        with TemporaryDirectory(prefix="path-freeze-") as folder:
            database = Path(folder) / "archive.sqlite"
            with ArchiveDatabase(database) as db:
                parent = seed(db, rows, [dict(rectangle("1/2")), point("1/2")])
                path = score_path(db, "p", parent, {"A": 1, "B": -1, "C": 0}, -1, 1)
                curve_id = analyze_path(db, "c", path, 1, ["A", "B"])
                saved_path = read_result(db, path)
                saved_curve = read_result(db, curve_id)
                frame = load_score_result(db, parent).frame
                self.assertEqual(saved_curve["payload"]["parents"], {"path": path})
                copy = read_result(db, curve_id)
                copy["payload"]["values"][0]["joint"] = F(99)
                copy["payload"]["pieces"][0]["joint"][0] = F(99)
                self.assertEqual(read_result(db, curve_id), saved_curve)
                corrected = dict(rows[0], revision="r2", x=77)
                ingest(db, "correction", [corrected])
                recalibrated = _calibration([dict(rectangle("1/2")), point("1/2")],
                                            revision="c2")
                recalibrated["rule"]["alpha"] = 5
                save_calibration(db, recalibrated)
                self.assertEqual(read_result(db, path), saved_path)
                self.assertEqual(read_result(db, curve_id), saved_curve)
                self.assertEqual(load_score_result(db, parent).frame, frame)
            with ArchiveDatabase(database) as reopened:
                self.assertEqual(read_result(reopened, path), saved_path)
                self.assertEqual(read_result(reopened, curve_id), saved_curve)
                previous = sys.getprofile()

                def forbid_computation(frame_, event, arg):
                    if event == "call" and frame_.f_code.co_name in (
                            "compile_scores", "integrate_ranking_event",
                            "iter_event_regions", "curve_for_path", "_measure"):
                        raise AssertionError("historical read attempted computation")

                sys.setprofile(forbid_computation)
                try:
                    self.assertEqual(read_result(reopened, curve_id), saved_curve)
                    self.assertEqual(read_result(reopened, path), saved_path)
                finally:
                    sys.setprofile(previous)

    def test_publication_rollback_and_retry(self):
        rows = [_row("A", r_u=1), _row("B", r_u=-1), _row("C")]
        for stage in ("payload", "parents", "binding"):
            with self.subTest(stage=stage), ArchiveDatabase() as db:
                parent = seed(db, rows, [dict(rectangle("1/2")), point("1/2")])
                path = score_path(db, "p", parent, {"A": 1, "B": -1, "C": 0}, -1, 1)
                curve = analyze_path(db, "c", path, 1, ["A", "B"])
                before = snapshot(db)
                reached = []

                def fail(current):
                    reached.append(current)
                    if current == stage:
                        raise OSError("injected path publication failure")

                db.fault = fail
                with self.assertRaises(OSError):
                    score_path(db, "retry", parent, {"A": 0, "B": 1, "C": 0}, -2, 2)
                db.fault = None
                self.assertIn(stage, reached)
                self.assertEqual(snapshot(db), before)
                retried = score_path(db, "retry", parent, {"A": 0, "B": 1, "C": 0}, -2, 2)
                after = snapshot(db)
                self.assertEqual(score_path(db, "retry", parent,
                                            {"C": 0, "B": 1, "A": 0}, -2, 2), retried)
                self.assertEqual(snapshot(db), after)
                self.assertEqual(read_result(db, path)["payload"]["directions"], [1, -1, 0])
                self.assertEqual(curve, analyze_path(db, "c", path, 1, ["A", "B"]))
                forward = analyze_path(db, "retry-curve", retried, 2, ["B", "A"])
                db.fault = fail
                with self.assertRaises(OSError):
                    analyze_path(db, "retry-curve-2", retried, 1, ["A"])
                db.fault = None
                again = analyze_path(db, "retry-curve-2", retried, 1, ["A"])
                self.assertEqual(analyze_path(db, "retry-curve-2", retried, 1, ["A"]), again)
                self.assertNotEqual(forward, again)

    def test_request_namespaces_and_conflicts(self):
        rows = [_row("A", r_u=1), _row("B", r_u=-1), _row("C")]
        with ArchiveDatabase() as db:
            parent = seed(db, rows, [dict(rectangle())])
            path = score_path(db, "same", parent, {"A": 1, "B": -1, "C": 0}, -1, 1)
            curve = analyze_path(db, "same", path, 1, ["A", "B"])
            before = snapshot(db)
            with self.assertRaises(RequestConflictError):
                score_path(db, "same", parent, {"A": 1, "B": -1, "C": 0}, -1, 2)
            with self.assertRaises(RequestConflictError):
                analyze_path(db, "same", path, 2, ["A", "B"])
            with self.assertRaises(RequestConflictError):
                analyze_path(db, "same", path, 1, ["B", "A"])
            self.assertEqual(snapshot(db), before)
            # score_path and analyze_path namespaces are independent of V1 too.
            report = analyze(db, "same", parent, 1, ["A", "B"])
            self.assertNotIn(report, (path, curve))
            self.assertEqual(score_path(db, "same", parent,
                                        {"A": 1, "B": -1, "C": 0}, -1, 1), path)
            self.assertEqual(analyze_path(db, "same", path, 1, ["A", "B"]), curve)

    def test_delivery_and_tamper(self):
        rows = [_row("A", r_u=1), _row("B", r_u=-1), _row("C")]
        with TemporaryDirectory(prefix="path-delivery-") as folder:
            database = Path(folder) / "archive.sqlite"
            target = Path(folder) / "curve.json"
            with ArchiveDatabase(database) as db:
                parent = seed(db, rows, [dict(rectangle("1/2")), point("1/2")])
                path = score_path(db, "p", parent, {"A": 1, "B": -1, "C": 0}, -1, 1)
                curve = analyze_path(db, "c", path, 1, ["A", "B"])
                saved = read_result(db, curve)
                export_result(db, curve, target)
                old = target.read_bytes()
                for stage in ("write", "before_replace"):
                    def fail(current, stage=stage):
                        if current == stage:
                            raise OSError("interrupted curve export")
                    with self.assertRaises(DeliveryError):
                        export_result(db, curve, target, fault=fail)
                    self.assertEqual(target.read_bytes(), old)
                    self.assertEqual(list(Path(folder).glob(".curve.json.*.tmp")), [])
                bundle = build_bundle(db, curve)
                root_record = next(r for r in bundle["artifacts"] if r["id"] == bundle["root"])
                self.assertEqual(saved["payload"], root_record["payload"])
                # The V1 report path still exports and verifies.
                report = analyze(db, "v1", parent, 1, ["A", "B"])
                v1_target = Path(folder) / "report.json"
                export_result(db, report, v1_target)
                v1_values = verify_bundle(v1_target.read_text(encoding="utf-8"))
                self.assertEqual(probabilities(v1_values), ([F(3, 4), F(3, 4)], F(1, 2), F(1)))
            database.unlink()
            text = target.read_text(encoding="utf-8")
            recomputed = verify_bundle(text)
            self.assertEqual(recomputed,
                             {k: v for k, v in saved["payload"].items() if k != "parents"})
            from monitor.archive.codec import loads
            tampered = loads(text)
            root = next(r for r in tampered["artifacts"] if r["id"] == tampered["root"])
            root["payload"]["values"][1]["joint"] = F(0)
            root["digest"] = payload_digest(root["payload"])
            root["id"] = "path_report:" + root["digest"]
            tampered["root"] = root["id"]
            tampered["source_digest"] = payload_digest(
                dict(root=tampered["root"], artifacts=tampered["artifacts"]))
            with self.assertRaisesRegex(DeliveryError, "offline curve"):
                verify_bundle(tampered)
            # Forged parent coefficients are rejected against the fixed rule.
            forged = loads(text)
            path_record = next(r for r in forged["artifacts"] if r["kind"] == "score_path")
            path_record["payload"]["directions"][0] = F(5)
            path_record["digest"] = payload_digest(path_record["payload"])
            path_record["id"] = "score_path:" + path_record["digest"]
            for record in forged["artifacts"]:
                if record["payload"].get("parents", {}).get("path") == saved["payload"]["parents"]["path"]:
                    record["payload"]["parents"]["path"] = path_record["id"]
                    record["digest"] = payload_digest(record["payload"])
                    record["id"] = "path_report:" + record["digest"]
                    forged["root"] = record["id"]
            forged["source_digest"] = payload_digest(
                dict(root=forged["root"], artifacts=forged["artifacts"]))
            with self.assertRaises(DeliveryError):
                verify_bundle(forged)


if __name__ == "__main__":
    unittest.main()
