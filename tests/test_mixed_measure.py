from fractions import Fraction as F
import unittest
from monitor.archive.database import ArchiveDatabase
from test_shared_ranking import pipeline, rectangle, point, result, probabilities


class TestMixedMeasure(unittest.TestCase):
    def test_point_and_rectangle(self):
        with ArchiveDatabase() as db:
            scoring, _, _ = pipeline(db, scenarios=[rectangle("1/2"), point("1/2")])
            actual = result(db, scoring)
            self.assertEqual(probabilities(actual), ([F(3, 4), F(3, 4)], F(1, 2), F(1)))
            self.assertEqual(actual["union"], sum(actual["marginals"]) - actual["joint"])

    def test_scenario_split(self):
        outputs = []
        for scenarios in ([rectangle("1/2"), point("1/2")],
                          [rectangle("1/6"), rectangle("1/3"), point("1/2")]):
            with ArchiveDatabase() as db:
                scoring, _, _ = pipeline(db, scenarios=scenarios)
                outputs.append(probabilities(result(db, scoring)))
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[1], ([F(3, 4), F(3, 4)], F(1, 2), F(1)))

    def test_pure_point_and_zero_weight(self):
        with ArchiveDatabase() as db:
            scoring, _, _ = pipeline(db, scenarios=[point(), rectangle(0)])
            self.assertEqual(probabilities(result(db, scoring)), ([F(1), F(1)], F(1), F(1)))
