import argparse
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from monitor.observations.schema import identifier, ObservationError
from monitor.observations.revisions import ingest
from monitor.observations.cohorts import freeze_cohort
from monitor.calibration.models import save_calibration, load_calibration_model
from monitor.scoring.jobs import score
from monitor.scoring.results import load_score_result
from monitor.analysis.results import parse_analysis_request, AnalysisError
from monitor.analysis.events import build_ranking_event
from monitor.analysis.measure import integrate_ranking_event
from monitor.archive.catalog import lookup_request, publish_artifact
from monitor.archive.database import ArchiveDatabase
from monitor.archive.reading import read_result, ReadError
from monitor.archive.codec import canonical_json


def analyze(db, request_id, score_id, k, targets):
    identifier(request_id)
    with db.transaction():
        try:
            scoring = load_score_result(db, score_id)
            request = parse_analysis_request(scoring.frame, score_id, k, targets)
            previous = lookup_request(db, "analyze", request_id, request.to_payload())
            if previous is not None:
                return previous
            model = load_calibration_model(db, scoring.frame.model_id)
        except (ReadError, ObservationError) as exc:
            raise AnalysisError("unknown or corrupt scoring input") from exc
        event = build_ranking_event(scoring.frame, request)
        result = integrate_ranking_event(event, model)
        return publish_artifact(db, "report", result.to_payload(), {"score": score_id},
                                ("analyze", request_id, request.to_payload()))


def run_daily_manifest(db, manifest):
    receipt = ingest(db, manifest["batch"], manifest["observations"])
    cohort_id = freeze_cohort(db, manifest["cohort"])
    model_id = save_calibration(db, manifest["calibration"])
    score_id = score(db, manifest["score_request"], cohort_id, model_id)
    report_id = analyze(db, manifest["analysis_request"], score_id,
                        manifest["k"], manifest["targets"])
    return dict(receipt=receipt.id, cohort=cohort_id, model=model_id,
                score=score_id, report=report_id)


def _run(manifest, db_path, output):
    from monitor.archive.delivery import export_result, verify_bundle
    from monitor.observations.schema import parse_rational
    with ArchiveDatabase(db_path) as db:
        ids = run_daily_manifest(db, manifest)
        report = read_result(db, ids["report"])["payload"]
        expected = manifest["expected"]
        wanted = dict(marginals=[parse_rational(v) for v in expected["marginals"]],
                      joint=parse_rational(expected["joint"]), union=parse_rational(expected["union"]))
        actual = {k: report[k] for k in wanted}
        if actual != wanted:
            raise AnalysisError("daily sample probability mismatch")
        export_result(db, ids["report"], output)
        downstream = verify_bundle(Path(output).read_text(encoding="utf-8"))
        downstream_actual = {k: downstream[k] for k in wanted}
        if downstream_actual != wanted:
            raise AnalysisError("offline delivery differs")
        print("POINTWISE_SCENARIO " + canonical_json(dict(
            case_id="v2", input=dict(observations=manifest["observations"],
                                     calibration=manifest["calibration"],
                                     k=manifest["k"], targets=manifest["targets"]),
            expected=wanted, actual=actual, downstream_expected=wanted,
            downstream_actual=downstream_actual,
            code_paths=["monitor/scoring/compile.py", "monitor/analysis/events.py",
                        "monitor/analysis/measure.py", "monitor/archive/delivery.py"],
            oracle="rectangle halves plus half-weight all-tied atom; exact hand calculation")))
        print(canonical_json(dict(ids=ids, report=report, delivery=str(output))))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Chengchuan daily retest archive")
    parser.add_argument("command", choices=("demo", "run"))
    parser.add_argument("--manifest", default="run_manifest.json")
    parser.add_argument("--db")
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        if args.command == "demo":
            with TemporaryDirectory(prefix="monitor-demo-") as folder:
                _run(manifest, Path(folder) / "archive.sqlite", Path(folder) / "report.json")
        else:
            if not args.db or not args.out:
                parser.error("run requires --db and --out")
            _run(manifest, args.db, args.out)
        return 0
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
        print(canonical_json({"error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
