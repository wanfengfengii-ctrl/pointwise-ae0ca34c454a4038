import argparse
import os
from pathlib import Path
import sys
import tempfile
from monitor.archive.codec import canonical_json, loads, payload_digest
from monitor.archive.catalog import validate_artifact, _parent_types
from monitor.archive.reading import read_result
from monitor.archive.database import ArchiveDatabase
from monitor.observations.schema import parse_observation
from monitor.observations.cohorts import Cohort, validate_cohort_members
from monitor.calibration.schema import parse_calibration, SharedAxes, FrozenCalibrationModel
from monitor.scoring.compile import compile_scores
from monitor.scoring.representation import ScoreFrame
from monitor.analysis.results import parse_analysis_request
from monitor.analysis.events import build_ranking_event
from monitor.analysis.measure import integrate_ranking_event
from monitor.analysis.curves import curve_for_path
from monitor.scoring.paths import path_from_payload


class DeliveryError(RuntimeError):
    pass


def build_bundle(db, report_id):
    root = read_result(db, report_id)
    if root["kind"] not in ("report", "path_report"):
        raise DeliveryError("delivery requires a probability report")
    artifacts = [root["lineage"][key] for key in sorted(root["lineage"])]
    content = dict(root=report_id, artifacts=artifacts)
    return dict(version=1, **content, source_digest=payload_digest(content))


def export_result(db, report_id, target, fault=None):
    temporary = None
    try:
        text = canonical_json(build_bundle(db, report_id)) + "\n"
        target = Path(target)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=target.parent, prefix="." + target.name + ".",
                                         suffix=".tmp", delete=False) as handle:
            temporary = handle.name
            cut = len(text) // 2
            handle.write(text[:cut])
            if fault:
                fault("write")
            handle.write(text[cut:])
            handle.flush()
            os.fsync(handle.fileno())
        if fault:
            fault("before_replace")
        os.replace(temporary, target)
        temporary = None
    except Exception as exc:
        raise DeliveryError("delivery failed: " + str(exc)) from exc
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _fixed_score(records, score_id):
    scoring = records[score_id]["payload"]
    frame = ScoreFrame.from_payload(scoring["frame"])
    if scoring["parents"] != {"cohort": frame.cohort_id, "model": frame.model_id}:
        raise DeliveryError("score parents differ")
    cohort_payload = records[frame.cohort_id]["payload"]
    members = tuple(parse_observation(v) for v in cohort_payload["members"])
    validate_cohort_members(members)
    revision_ids = tuple(cohort_payload["parents"]["member:" + str(i)] for i in range(len(members)))
    if len(revision_ids) != len(cohort_payload["parents"]):
        raise DeliveryError("cohort parent count differs")
    for member, identity in zip(members, revision_ids):
        if parse_observation(records[identity]["payload"]["observation"]) != member:
            raise DeliveryError("cohort source snapshot differs")
    cohort = Cohort(frame.cohort_id, members, revision_ids)
    model_payload = records[frame.model_id]["payload"]
    revision_id = model_payload["parents"]["revision"]
    calibration = parse_calibration(model_payload["calibration"])
    if calibration != parse_calibration(records[revision_id]["payload"]["calibration"]):
        raise DeliveryError("calibration source snapshot differs")
    axes = [revision_id + ":U", revision_id + ":V"]
    if model_payload["axes"] != axes:
        raise DeliveryError("model shared axes differ")
    model = FrozenCalibrationModel(frame.model_id, revision_id, SharedAxes(*axes), calibration)
    recomputed_frame = compile_scores(cohort, model)
    if frame != recomputed_frame:
        raise DeliveryError("stored affine scores do not match fixed inputs")
    return frame, model


def _verify_report(records, report_id):
    report = records[report_id]
    saved = report["payload"]
    score_id = saved["parents"]["score"]
    frame, model = _fixed_score(records, score_id)
    request = saved["request"]
    if set(request) != {"score_id", "k", "targets"} or request["score_id"] != score_id:
        raise DeliveryError("report request differs from score parent")
    request = parse_analysis_request(frame, score_id, request["k"], request["targets"])
    result = integrate_ranking_event(build_ranking_event(frame, request), model).to_payload()
    expected_saved = dict(result, parents={"score": score_id})
    if saved != expected_saved:
        raise DeliveryError("offline probability recomputation differs")
    return result


def _verify_path_report(records, report_id):
    report = records[report_id]
    saved = report["payload"]
    path_id = saved["parents"]["path"]
    path_payload = records[path_id]["payload"]
    if path_payload.get("parents") != {"score": path_payload.get("score_id")}:
        raise DeliveryError("path parent differs from stored score")
    frame, model = _fixed_score(records, path_payload["score_id"])
    path = path_from_payload(path_id, path_payload, frame, model)
    request = saved["request"]
    if (not isinstance(request, dict)
            or set(request) != {"path_id", "k", "targets"}
            or request["path_id"] != path_id):
        raise DeliveryError("curve request differs from path parent")
    result = curve_for_path(path, model, request["k"], request["targets"])
    expected_saved = dict(result, parents={"path": path_id})
    if saved != expected_saved:
        raise DeliveryError("offline curve recomputation differs")
    return result


def _verify(bundle):
    if not isinstance(bundle, dict) or set(bundle) != {
            "version", "root", "artifacts", "source_digest"} or type(bundle["version"]) is not int or bundle["version"] != 1:
        raise DeliveryError("unsupported bundle schema")
    content = dict(root=bundle["root"], artifacts=bundle["artifacts"])
    if payload_digest(content) != bundle["source_digest"]:
        raise DeliveryError("bundle source digest mismatch")
    records = {}
    for record in bundle["artifacts"]:
        if set(record) != {"id", "kind", "digest", "payload"}:
            raise DeliveryError("invalid bundle artifact")
        validate_artifact(record)
        if record["id"] in records:
            raise DeliveryError("duplicate bundle artifact")
        records[record["id"]] = record
    visited, active = set(), set()

    def visit(identity):
        if identity in active:
            raise DeliveryError("cyclic bundle provenance")
        if identity in visited:
            return
        active.add(identity)
        record = records[identity]
        expected = _parent_types(record["kind"], record["payload"]["parents"])
        for role, parent in record["payload"]["parents"].items():
            if records[parent]["kind"] != expected[role]:
                raise DeliveryError("wrong bundle parent type")
            visit(parent)
        active.remove(identity)
        visited.add(identity)

    visit(bundle["root"])
    if visited != set(records):
        raise DeliveryError("bundle must contain exactly the root closure")
    kind = records[bundle["root"]]["kind"]
    if kind == "report":
        return _verify_report(records, bundle["root"])
    if kind == "path_report":
        return _verify_path_report(records, bundle["root"])
    raise DeliveryError("bundle root is not a probability report")


def verify_bundle(bundle):
    try:
        if isinstance(bundle, str):
            bundle = loads(bundle)
        return _verify(bundle)
    except DeliveryError:
        raise
    except Exception as exc:
        raise DeliveryError("invalid self-contained bundle") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description="Export or explicitly recompute a fixed report")
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("--db", required=True)
    export.add_argument("--report", required=True)
    export.add_argument("--out", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("bundle")
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            result = verify_bundle(Path(args.bundle).read_text(encoding="utf-8"))
            print(canonical_json(result))
        else:
            with ArchiveDatabase(args.db) as db:
                export_result(db, args.report, args.out)
            print(canonical_json({"exported": args.out, "report": args.report}))
        return 0
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
        print(canonical_json({"error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
