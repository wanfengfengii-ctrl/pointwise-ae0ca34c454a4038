from fractions import Fraction as F
import unittest
from monitor.archive.database import ArchiveDatabase
from monitor.archive.reading import read_result
from monitor.archive.codec import canonical_json
from monitor.observations.revisions import ingest
from monitor.observations.cohorts import freeze_cohort, load_cohort
from monitor.calibration.models import save_calibration
from monitor.scoring.jobs import score
from monitor.scoring.results import load_score_result
from monitor.analysis.jobs import analyze
from monitor.analysis.results import AnalysisError
from monitor.analysis.geometry import HalfPlane, clip_halfplane, rectangle_polygon, polygon_area


def observations(values=None):
    values = values or [("A", 0, 1, 0), ("B", 0, -1, 0), ("C", 0, 0, 0)]
    return [dict(source="station-lab", station=s, revision="r1", x=a, r_u=b, r_v=c, unit="ug/m3")
            for s, a, b, c in values]


def rectangle(weight=1, bounds=(-1, 1, -1, 1)):
    return dict(kind="rectangle", weight=weight,
                **dict(zip(("u_lo", "u_hi", "v_lo", "v_hi"), bounds)))


def point(weight=1, u=0, v=0):
    return dict(kind="point", weight=weight, u=u, v=v)


def calibration(scenarios=None):
    return dict(source="calibration-lab", revision="c1", variables=["U", "V"],
                scenarios=scenarios if scenarios is not None else [rectangle()],
                rule=dict(delta=0, alpha=1, beta=1, gamma=1, version="affine-v1",
                          input_unit="ug/m3", output_unit="score"))


def pipeline(db, rows=None, scenarios=None, order=None, suffix=""):
    rows = observations() if rows is None else rows
    ingest(db, "batch" + suffix, rows)
    by_station = {r["station"]: r for r in rows}
    ordered = [by_station[s] for s in order] if order is not None else rows
    cohort = freeze_cohort(db, [(r["source"], r["station"], r["revision"]) for r in ordered])
    model = save_calibration(db, calibration(scenarios))
    scoring = score(db, "score" + suffix, cohort, model)
    return scoring, cohort, model


def result(db, scoring, k=1, targets=("A", "B"), request="analysis"):
    identity = analyze(db, request, scoring, k, targets)
    return read_result(db, identity)["payload"]


def probabilities(payload):
    return payload["marginals"], payload["joint"], payload["union"]


def snapshot(db):
    tables = ("artifacts", "parents", "bindings", "observations", "calibration_revisions", "receipts")
    return {t: sorted(tuple(r) for r in db.connection.execute("SELECT * FROM " + t)) for t in tables}


class TestSharedRanking(unittest.TestCase):
    def setUp(self):
        self.db = ArchiveDatabase()

    def tearDown(self):
        self.db.close()

    def test_opposed_scores(self):
        scoring, _, _ = pipeline(self.db)
        actual = result(self.db, scoring)
        self.assertEqual(probabilities(actual), ([F(1, 2), F(1, 2)], F(0), F(1)))
        single = result(self.db, scoring, targets=("A",), request="single")
        self.assertEqual(single["marginals"][0], actual["marginals"][0])
        print("POINTWISE_SCENARIO " + canonical_json(dict(
            case_id="v1", input=dict(scores=["U", "-U", "0"], bounds=[-1, 1, -1, 1], k=1),
            expected=[F(1, 2), F(1, 2), F(0), F(1)],
            actual=[*actual["marginals"], actual["joint"], actual["union"]],
            downstream_expected=F(1, 2), downstream_actual=single["joint"],
            code_paths=["monitor/scoring/compile.py", "monitor/analysis/events.py",
                        "monitor/analysis/measure.py"],
            oracle="positive/negative U half-rectangles are disjoint")))

    def test_ingest_freeze(self):
        scoring, cohort, model = pipeline(self.db, order=["C", "B", "A"])
        loaded = load_cohort(self.db, cohort)
        self.assertEqual(loaded.stations, ("C", "B", "A"))
        frame = load_score_result(self.db, scoring).frame
        self.assertEqual(frame.model_id, model)
        self.assertEqual([s.coordinate for s in frame.scores], [0, 1, 2])
        self.assertEqual([(s.a, s.b, s.c) for s in frame.scores],
                         [(F(0), F(0), F(0)), (F(0), F(-1), F(0)), (F(0), F(1), F(0))])
        self.assertEqual(len(set(frame.axes)), 2)
        model_payload = read_result(self.db, model)["payload"]
        self.assertEqual(list(frame.axes), model_payload["axes"])
        self.assertEqual([s.observation_id for s in frame.scores], list(loaded.revision_ids))

    def test_request_edges(self):
        scoring, _, _ = pipeline(self.db)
        before = snapshot(self.db)
        invalid = [(k, ["A"]) for k in (0, 4, True, 1.0, "1")]
        invalid += [(1, t) for t in (["unknown"], ["A", "A"], ["A", "B", "C"], "A", [None])]
        for i, (k, targets) in enumerate(invalid):
            with self.subTest(k=k, targets=targets), self.assertRaises(AnalysisError):
                analyze(self.db, "bad" + str(i), scoring, k, targets)
            self.assertEqual(snapshot(self.db), before)
        self.assertEqual(probabilities(result(self.db, scoring, targets=[], request="empty")),
                         ([], F(0), F(0)))
        self.assertEqual(probabilities(result(self.db, scoring, targets=["B"], request="one")),
                         ([F(1, 2)], F(1, 2), F(1, 2)))
        self.assertEqual(probabilities(result(self.db, scoring, k=3, request="all")),
                         ([F(1), F(1)], F(1), F(1)))
        with self.assertRaises(AnalysisError):
            analyze(self.db, "missing", "score:missing", 1, ["A"])

    def test_area_normalization(self):
        scoring, _, _ = pipeline(self.db, scenarios=[rectangle(bounds=(-1, 3, 0, 2))])
        self.assertEqual(probabilities(result(self.db, scoring)),
                         ([F(3, 4), F(1, 4)], F(0), F(1)))

    def test_planar_area(self):
        rows = observations([("A", 0, 1, 0), ("B", 0, 0, 1), ("C", 0, 0, 0)])
        scoring, _, _ = pipeline(self.db, rows=rows)
        # Independent area: for 0<u<1, -1<v<u; integral(1+u,0,1)=3/2.
        area_by_hand = F(1) + F(1, 2)
        clipped = clip_halfplane(rectangle_polygon(F(-1), F(1), F(-1), F(1)),
                                 HalfPlane(F(0), F(1), F(0)))
        clipped = clip_halfplane(clipped, HalfPlane(F(0), F(1), F(-1)))
        self.assertEqual(polygon_area(clipped), area_by_hand)
        self.assertEqual(probabilities(result(self.db, scoring)),
                         ([area_by_hand / 4, area_by_hand / 4], F(0), F(3, 4)))
        self.assertEqual(probabilities(result(self.db, scoring, k=2, request="two")),
                         ([F(5, 8), F(5, 8)], F(1, 4), F(1)))
