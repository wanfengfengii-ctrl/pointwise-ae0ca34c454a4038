from fractions import Fraction as F
import unittest
from monitor.archive.database import ArchiveDatabase
from test_shared_ranking import observations, pipeline, point, result, probabilities


class TestTies(unittest.TestCase):
    def test_identical_scores(self):
        rows = observations([("A", 2, 1, -1), ("B", 2, 1, -1), ("C", 2, 1, -1)])
        for order in (["A", "B", "C"], ["C", "A", "B"]):
            with ArchiveDatabase() as db:
                scoring, _, _ = pipeline(db, rows=rows, order=order)
                actual = result(db, scoring, targets=["B", "A"])
                self.assertEqual(probabilities(actual), ([F(1), F(1)], F(1), F(1)))
                self.assertEqual(actual["request"]["targets"], ["B", "A"])

    def test_point_tie_boundary(self):
        rows = observations([("A", 1, 0, 0), ("B", 1, 0, 0), ("C", 0, 0, 0)])
        with ArchiveDatabase() as db:
            scoring, _, _ = pipeline(db, rows=rows, scenarios=[point()])
            self.assertEqual(probabilities(result(db, scoring)), ([F(1), F(1)], F(1), F(1)))
            self.assertEqual(result(db, scoring, targets=["C"], request="c")["marginals"], [F(0)])

    def test_permutation(self):
        rows = observations([("A", 0, 1, 0), ("B", "1/2", 0, 0), ("C", -2, 0, 0)])
        for order in (["A", "B", "C"], ["C", "A", "B"]):
            with ArchiveDatabase() as db:
                scoring, _, _ = pipeline(db, rows=rows, order=order)
                self.assertEqual(probabilities(result(db, scoring, targets=["B", "A"])),
                                 ([F(3, 4), F(1, 4)], F(0), F(1)))
                self.assertEqual(result(db, scoring, targets=["C"], request="c")["marginals"], [F(0)])
