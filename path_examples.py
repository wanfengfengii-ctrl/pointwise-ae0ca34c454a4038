"""Public finite hand calculations and end-to-end calls; no path solver."""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction as F
from pathlib import Path
from tempfile import TemporaryDirectory
import argparse
import unittest

from monitor.archive.database import ArchiveDatabase
from monitor.archive.codec import canonical_json
from monitor.archive.catalog import RequestConflictError
from monitor.archive.reading import read_result
from monitor.archive.delivery import export_result, verify_bundle
from monitor.observations.revisions import ingest
from monitor.observations.cohorts import freeze_cohort
from monitor.calibration.models import save_calibration, load_calibration_model
from monitor.scoring.jobs import score, ScoringError
from monitor.scoring.results import load_score_result
from monitor.analysis.results import parse_analysis_request, AnalysisError
from monitor.analysis.events import build_ranking_event
from monitor.analysis.measure import integrate_ranking_event


def probabilities(marginals, joint, union):
    return dict(marginals=[F(x) for x in marginals], joint=F(joint), union=F(union))


def polynomial(marginals, joint, union):
    return dict(marginals=[[F(x) for x in p] for p in marginals],
                joint=[F(x) for x in joint], union=[F(x) for x in union])


ZERO = (0, 0, 0)
ONE = (1, 0, 0)
RECTANGLE = dict(kind="rectangle", weight=1, u_lo=-1, u_hi=1, v_lo=-1, v_hi=1)
CASES = {
    "mixed": dict(
        rows=[("A", 0, 1, 0), ("B", 0, -1, 0), ("C", 0, 0, 0)],
        scenarios=[dict(RECTANGLE, weight="1/2"),
                   dict(kind="point", weight="1/2", u=0, v=0)],
        alpha=1, directions={"A": 1, "B": -1, "C": 0}, lo=-1, hi=1,
        k=1, targets=["A", "B"], breakpoints=[-1, 0, 1],
        pieces=[
            polynomial([(F(1, 4), F(1, 4), 0), (F(3, 4), F(-1, 4), 0)], ZERO, ONE),
            polynomial([(F(3, 4), F(1, 4), 0), (F(1, 4), F(-1, 4), 0)], ZERO, ONE)],
        values=[probabilities([0, 1], 0, 1),
                probabilities([F(3, 4), F(3, 4)], F(1, 2), 1),
                probabilities([1, 0], 0, 1)]),
    "quadratic": dict(
        rows=[("A", 0, 0, 0), ("B", 0, 1, 0), ("C", 0, 0, 1)],
        scenarios=[dict(RECTANGLE, u_lo=0, v_lo=0)],
        alpha=2, directions={"A": "1/2", "B": 0, "C": 0}, lo=-1, hi=2,
        k=1, targets=["A"], breakpoints=[-1, 0, 1, 2],
        pieces=[polynomial([ZERO], ZERO, ZERO),
                polynomial([(0, 0, 1)], (0, 0, 1), (0, 0, 1)),
                polynomial([ONE], ONE, ONE)],
        values=[probabilities([0], 0, 0), probabilities([0], 0, 0),
                probabilities([1], 1, 1), probabilities([1], 1, 1)]),
    "spatial_tie": dict(
        rows=[("A", 0, 0, 0), ("B", 0, 0, 0), ("C", 0, 0, 0)],
        scenarios=[RECTANGLE], alpha=1,
        directions={"A": 1, "B": -1, "C": 0}, lo=-1, hi=1,
        k=1, targets=["A", "B"], breakpoints=[-1, 0, 1],
        pieces=[polynomial([ZERO, ONE], ZERO, ONE),
                polynomial([ONE, ZERO], ZERO, ONE)],
        values=[probabilities([0, 1], 0, 1), probabilities([1, 1], 1, 1),
                probabilities([1, 0], 0, 1)]),
}


def seed(db, case, reverse=False):
    rows = [dict(source="path-lab", station=s, revision="r1",
                 x=x, r_u=u, r_v=v, unit="ug/m3") for s, x, u, v in case["rows"]]
    ingest(db, "path-input", rows)
    ordered = rows[::-1] if reverse else rows
    cohort = freeze_cohort(db, [(r["source"], r["station"], r["revision"]) for r in ordered])
    raw = dict(source="path-calibration", revision="c1", variables=["U", "V"],
               scenarios=deepcopy(case["scenarios"]),
               rule=dict(delta=0, alpha=case["alpha"], beta=1, gamma=1,
                         version="affine-v1", input_unit="ug/m3", output_unit="score"))
    model = save_calibration(db, raw)
    return score(db, "parent-score", cohort, model)


def expected_curve(case):
    return dict(breakpoints=[F(x) for x in case["breakpoints"]],
                pieces=deepcopy(case["pieces"]), values=deepcopy(case["values"]))


def evaluate(curve, t):
    t = F(t)
    knots = curve["breakpoints"]
    if t in knots:
        return curve["values"][knots.index(t)]
    index = next(i for i in range(len(knots)-1) if knots[i] < t < knots[i+1])
    piece = curve["pieces"][index]
    def at(coefficients):
        return sum((c * t**i for i, c in enumerate(coefficients)), F(0))
    return dict(marginals=[at(p) for p in piece["marginals"]],
                joint=at(piece["joint"]), union=at(piece["union"]))


def static_value(db, score_id, directions, t, k, targets):
    original = load_score_result(db, score_id).frame
    model = load_calibration_model(db, original.model_id)
    moved = replace(original, scores=tuple(
        replace(s, a=s.a + model.rule.alpha * F(directions[s.station]) * t)
        for s in original.scores))
    request = parse_analysis_request(moved, score_id, k, targets)
    result = integrate_ranking_event(build_ranking_event(moved, request), model)
    return probabilities(result.marginals, result.joint, result.union)


def baseline():
    checked = 0
    for name, case in CASES.items():
        with ArchiveDatabase() as db:
            parent = seed(db, case)
            curve = expected_curve(case)
            points = set(curve["breakpoints"])
            for left, right in zip(curve["breakpoints"], curve["breakpoints"][1:]):
                points.update(left + (right-left)*F(i, 4) for i in (1, 2, 3))
            for t in sorted(points):
                actual = static_value(db, parent, case["directions"], t, case["k"], case["targets"])
                assert actual == evaluate(curve, t), (name, t, actual)
                checked += 1
    print(canonical_json(dict(mode="baseline", hand_point_checks=checked)))


def snapshot(db):
    tables = ("artifacts", "parents", "bindings", "observations", "calibration_revisions", "receipts")
    return {t: sorted(tuple(r) for r in db.connection.execute("SELECT * FROM " + t)) for t in tables}


def curve_fields(payload):
    return {key: payload[key] for key in ("breakpoints", "pieces", "values")}


def check_path_payload(db, identity, parent, case):
    saved = read_result(db, identity)
    assert saved["kind"] == "score_path"
    frame = load_score_result(db, parent).frame
    model = load_calibration_model(db, frame.model_id)
    stations = [s.station for s in frame.scores]
    directions = [F(case["directions"][s]) for s in stations]
    expected = dict(score_id=parent, stations=stations, directions=directions,
                    coefficients=[[s.a, s.b, s.c, model.rule.alpha*d]
                                  for s, d in zip(frame.scores, directions)],
                    lo=F(case["lo"]), hi=F(case["hi"]), parents={"score": parent})
    assert saved["payload"] == expected
    return saved


def check_curve_payload(db, identity, path, case):
    saved = read_result(db, identity)
    assert saved["kind"] == "path_report"
    wanted = dict(expected_curve(case),
                  request=dict(path_id=path, k=case["k"], targets=case["targets"]),
                  parents={"path": path})
    assert saved["payload"] == wanted, (identity, saved["payload"], wanted)
    return saved


def assert_rejected(db, error, operation):
    before = snapshot(db)
    try:
        operation()
    except error:
        pass
    else:
        raise AssertionError("invalid operation was accepted")
    assert snapshot(db) == before


def assert_rollback(db, operation):
    for stage in ("payload", "parents", "binding"):
        before = snapshot(db)
        reached = []
        def fail(current):
            reached.append(current)
            if current == stage:
                raise OSError("public path publication exercise")
        db.fault = fail
        try:
            operation(stage)
        except OSError:
            pass
        else:
            raise AssertionError("publication fault was swallowed")
        finally:
            db.fault = None
        assert stage in reached
        assert snapshot(db) == before
        first = operation(stage)
        after = snapshot(db)
        assert operation(stage) == first
        assert snapshot(db) == after


def run_paths(verbose=False):
    from monitor.scoring.jobs import score_path
    from monitor.analysis.jobs import analyze_path
    evidence = []
    for name, case in CASES.items():
        with TemporaryDirectory(prefix="path-example-") as folder:
            db_path = Path(folder) / "archive.sqlite"
            target = Path(folder) / "curve.json"
            with ArchiveDatabase(db_path) as db:
                parent = seed(db, case)
                directions = deepcopy(case["directions"])
                path = score_path(db, "path", parent, directions, case["lo"], case["hi"])
                saved_path = check_path_payload(db, path, parent, case)
                identity = analyze_path(db, "curve", path, case["k"], case["targets"])
                saved = check_curve_payload(db, identity, path, case)
                before = snapshot(db)
                assert score_path(db, "path", parent, dict(reversed(list(directions.items()))),
                                  case["lo"], case["hi"]) == path
                assert analyze_path(db, "curve", path, case["k"], case["targets"]) == identity
                assert snapshot(db) == before
                directions["A"] = 99
                assert read_result(db, path) == saved_path
                copy = read_result(db, identity)
                copy["payload"]["values"][0]["joint"] = F(99)
                assert read_result(db, identity) == saved
                bad = dict(case["directions"])
                del bad["A"]
                assert_rejected(db, ScoringError,
                                lambda: score_path(db, "bad", parent, bad, case["lo"], case["hi"]))
                assert_rejected(db, RequestConflictError,
                                lambda: score_path(db, "path", parent, case["directions"],
                                                   case["lo"], case["hi"]+1))
                assert_rejected(db, AnalysisError,
                                lambda: analyze_path(db, "bad", path, True, case["targets"]))
                if name == "mixed":
                    assert_rollback(db, lambda stage: score_path(
                        db, "retry-path-"+stage, parent, case["directions"],
                        case["lo"]-1-("payload", "parents", "binding").index(stage), case["hi"]))
                    assert_rollback(db, lambda stage: analyze_path(
                        db, "retry-curve-"+stage, path,
                        2 if stage != "binding" else 3,
                        ["A"] if stage == "parents" else ["A", "B"]))
                    zero = score_path(db, "point", parent, case["directions"], 0, 0)
                    point_report = analyze_path(db, "point", zero, 1, ["A", "B"])
                    got = read_result(db, point_report)["payload"]
                    assert got["breakpoints"] == [F(0)] and got["pieces"] == []
                    assert got["values"] == [case["values"][1]]
                    empty = analyze_path(db, "empty", path, 1, [])
                    assert curve_fields(read_result(db, empty)["payload"]) == dict(
                        breakpoints=[F(-1), F(1)],
                        pieces=[polynomial([], ZERO, ZERO)], values=[probabilities([], 0, 0)]*2)
                correction = dict(source="path-lab", station="A", revision="r2",
                                  x=77, r_u=0, r_v=0, unit="ug/m3")
                ingest(db, "correction", [correction])
                assert read_result(db, path) == saved_path
                assert read_result(db, identity) == saved
                export_result(db, identity, target)
                text = target.read_text(encoding="utf-8")
                from monitor.archive.delivery import DeliveryError
                for stage in ("write", "before_replace"):
                    def fail(current):
                        if current == stage:
                            raise OSError("public curve export exercise")
                    try:
                        export_result(db, identity, target, fault=fail)
                    except DeliveryError:
                        pass
                    else:
                        raise AssertionError("export fault was swallowed")
                    assert target.read_text(encoding="utf-8") == text
                    assert list(Path(folder).glob(".curve.json.*.tmp")) == []
            with ArchiveDatabase(db_path) as db:
                assert read_result(db, identity) == saved
                assert read_result(db, path) == saved_path
                export_result(db, identity, target)
                assert target.read_text(encoding="utf-8") == text
            db_path.unlink()
            recomputed = verify_bundle(text)
            assert recomputed == {k: v for k, v in saved["payload"].items() if k != "parents"}
            facts = dict(case=name, breakpoints=[str(v) for v in saved["payload"]["breakpoints"]],
                         pieces=len(saved["payload"]["pieces"]),
                         offline_matches=True)
            knots = saved["payload"]["breakpoints"]
            pieces = saved["payload"]["pieces"]
            isolated = []
            for index in range(1, len(knots) - 1):
                actual = saved["payload"]["values"][index]
                left = evaluate({"breakpoints": knots[index-1:index+1],
                                 "values": [actual, actual],
                                 "pieces": [pieces[index-1]]},
                                (knots[index-1] + knots[index]) / 2)
                left_at_knot = dict(
                    marginals=[sum((c * knots[index] ** i for i, c in enumerate(p)), F(0))
                               for p in pieces[index-1]["marginals"]],
                    joint=sum((c * knots[index] ** i for i, c in enumerate(pieces[index-1]["joint"])), F(0)),
                    union=sum((c * knots[index] ** i for i, c in enumerate(pieces[index-1]["union"])), F(0)))
                if left_at_knot != actual:
                    isolated.append(str(knots[index]))
            facts["isolated_knots"] = isolated
            if verbose:
                print(canonical_json(dict(case=name, curve=saved["payload"], offline=recomputed)))
            evidence.append(facts)
        with ArchiveDatabase() as db:
            parent = seed(db, case, reverse=True)
            path = score_path(db, "path", parent, case["directions"], case["lo"], case["hi"])
            check_path_payload(db, path, parent, case)
            identity = analyze_path(db, "curve", path, case["k"], case["targets"])
            assert curve_fields(read_result(db, identity)["payload"]) == expected_curve(case)
    print(canonical_json(dict(mode="paths", cases=list(CASES), evidence=evidence)))


def run_v1_demo():
    """The original daily manifest demo: real V1 report plus offline verify."""
    import json
    from monitor.analysis.jobs import _run
    manifest = json.loads(Path("run_manifest.json").read_text(encoding="utf-8"))
    with TemporaryDirectory(prefix="manifest-demo-") as folder:
        _run(manifest, Path(folder) / "manifest.sqlite",
             Path(folder) / "manifest-report.json")


def demo_correction_reopen():
    """Real curve, then a correction and reopen, then explicit offline
    recomputation of both the frozen old curve and a newly built correction
    curve."""
    from monitor.scoring.jobs import score_path
    from monitor.analysis.jobs import analyze_path
    case = CASES["mixed"]
    with TemporaryDirectory(prefix="path-correction-") as folder:
        db_path = Path(folder) / "archive.sqlite"
        target = Path(folder) / "frozen-curve.json"
        with ArchiveDatabase(db_path) as db:
            parent = seed(db, case)
            path = score_path(db, "path", parent, case["directions"], case["lo"], case["hi"])
            old = analyze_path(db, "curve", path, case["k"], case["targets"])
            old_saved = read_result(db, old)["payload"]
            old_path = read_result(db, path)["payload"]
            corrected = dict(source="path-lab", station="A", revision="r2",
                             x=3, r_u=0, r_v=0, unit="ug/m3")
            ingest(db, "correction", [corrected])
            cohort = freeze_cohort(db, [("path-lab", "A", "r2"),
                                        ("path-lab", "B", "r1"),
                                        ("path-lab", "C", "r1")])
            model = save_calibration(db, dict(
                source="path-calibration", revision="c1", variables=["U", "V"],
                scenarios=deepcopy(case["scenarios"]),
                rule=dict(delta=0, alpha=case["alpha"], beta=1, gamma=1,
                          version="affine-v1", input_unit="ug/m3", output_unit="score")))
            new_parent = score(db, "corrected-parent", cohort, model)
            new_path = score_path(db, "corrected-path", new_parent,
                                  case["directions"], case["lo"], case["hi"])
            new_curve = analyze_path(db, "corrected-curve", new_path, case["k"],
                                     case["targets"])
            new_saved = read_result(db, new_curve)["payload"]
            assert read_result(db, old) == dict(
                id=old, kind="path_report", payload=old_saved,
                lineage=read_result(db, old)["lineage"])
            assert read_result(db, path)["payload"] == old_path
            export_result(db, old, target)
            frozen_text = target.read_text(encoding="utf-8")
            export_result(db, new_curve, Path(folder) / "corrected-curve.json")
            corrected_text = (Path(folder) / "corrected-curve.json").read_text(encoding="utf-8")
        db_path.unlink()
        frozen_offline = verify_bundle(frozen_text)
        corrected_offline = verify_bundle(corrected_text)
        assert frozen_offline == {k: v for k, v in old_saved.items() if k != "parents"}
        assert corrected_offline == {k: v for k, v in new_saved.items() if k != "parents"}
        assert frozen_offline["values"] == old_saved["values"]
        assert corrected_offline["values"] != frozen_offline["values"]
        print(canonical_json(dict(
            case="mixed-correction",
            frozen_curve=old_saved,
            corrected_curve=new_saved,
            frozen_offline_matches=True,
            corrected_offline_matches=True,
            old_parent_unchanged=True,
            oracle="correction r2 (x=3 at A) changes only the corrected path")))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("baseline", "verify", "demo"))
    mode = parser.parse_args().mode
    if mode == "baseline":
        baseline()
    else:
        if mode == "verify":
            suite = unittest.defaultTestLoader.discover("tests")
            if not unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful():
                return 1
        else:
            run_v1_demo()
        run_paths(verbose=mode == "demo")
        if mode == "demo":
            demo_correction_reopen()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
