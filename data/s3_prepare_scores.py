"""Prepare and publish tract features and priority scores from versioned S3 evidence.

Raw source objects are materialized into a temporary workspace and are never
written beneath the repository. Every input must carry a ``sha256`` metadata
value, and every generated artifact is uploaded with its own checksum.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import boto3

# Support both ``python -m data.s3_prepare_scores`` and the documented
# Windows invocation ``python .\data\s3_prepare_scores.py``.
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from config import PILOT_CITY, PILOT_RURAL_COUNTY
from tools.gap_scorer import DEFAULT_WEIGHTS, score_all_gaps


PIPELINE_FORMAT = "food-access-advisor-s3-score-pipeline-v1"
FEATURE_FORMAT = "food-access-advisor-tract-features-v1"
SCORE_FORMAT = "food-access-advisor-priority-scores-v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_extract(archive_path: Path, destination: Path) -> None:
    """Extract a ZIP without allowing members to escape the workspace."""
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if target != root and root not in target.parents:
                raise ValueError(f"unsafe ZIP member: {member.filename}")
        archive.extractall(destination)


class VerifiedS3Store:
    def __init__(self, client, bucket: str):
        self.client = client
        self.bucket = bucket

    def download(self, key: str, destination: Path) -> dict:
        head = self.client.head_object(Bucket=self.bucket, Key=key)
        metadata = {str(k).lower(): str(v) for k, v in head.get("Metadata", {}).items()}
        expected = metadata.get("sha256")
        if not expected:
            raise ValueError(f"s3://{self.bucket}/{key} has no sha256 metadata")
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, key, str(destination))
        actual = sha256_file(destination)
        if actual.lower() != expected.lower():
            destination.unlink(missing_ok=True)
            raise ValueError(f"checksum mismatch for s3://{self.bucket}/{key}")
        return {
            "key": key,
            "version_id": head.get("VersionId"),
            "bytes": head.get("ContentLength"),
            "sha256": actual,
        }

    def upload(self, path: Path, key: str, *, content_type: str) -> dict:
        digest = sha256_file(path)
        self.client.upload_file(
            str(path),
            self.bucket,
            key,
            ExtraArgs={
                "ContentType": content_type,
                "ServerSideEncryption": "AES256",
                "Metadata": {
                    "sha256": digest,
                    "pipeline-format": PIPELINE_FORMAT,
                    "data-classification": "public_aggregate",
                },
            },
        )
        head = self.client.head_object(Bucket=self.bucket, Key=key)
        if head.get("Metadata", {}).get("sha256") != digest:
            raise ValueError(f"uploaded checksum metadata is missing for s3://{self.bucket}/{key}")
        return {
            "key": key,
            "version_id": head.get("VersionId"),
            "bytes": head.get("ContentLength"),
            "sha256": digest,
        }


def _find_named(root: Path, name: str) -> Path:
    matches = list(root.rglob(name))
    if len(matches) != 1:
        raise ValueError(f"expected exactly one {name!r}, found {len(matches)}")
    return matches[0]


def _read_database(path: Path) -> tuple[list[dict], dict]:
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        rows = [dict(row) for row in connection.execute("SELECT * FROM tracts ORDER BY tract_fips")]
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
    if not rows:
        raise ValueError(f"prepared database contains no tracts: {path}")
    return rows, metadata


def _load_context(path: Path, atlas_geoids: set[str]) -> tuple[dict[str, dict], dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("context_format") != "food-access-advisor-urban-context-v1":
        raise ValueError("unsupported urban scoring context format")
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("urban scoring context contains no records")
    by_geoid = {str(row.get("tract_fips")): row for row in records}
    if len(by_geoid) != len(records):
        raise ValueError("urban scoring context contains duplicate tract GEOIDs")
    if set(by_geoid) != atlas_geoids:
        raise ValueError("urban scoring context does not match the prepared Atlas universe")
    return by_geoid, payload


def _load_resources(path: Path, expected_scope: str) -> tuple[list[dict], dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("scope") != expected_scope:
        raise ValueError(f"resource snapshot scope must be {expected_scope!r}")
    resources = payload.get("resources")
    if not isinstance(resources, list) or not resources:
        raise ValueError(f"{expected_scope} resource snapshot is empty")
    usable = [
        item for item in resources
        if item.get("lat") is not None and item.get("lon") is not None
    ]
    if not usable:
        raise ValueError(f"{expected_scope} resource snapshot has no geocoded resources")
    raw_boxes = payload.get("coverage_bboxes")
    if raw_boxes is None and payload.get("coverage_bbox") is not None:
        raw_boxes = [payload["coverage_bbox"]]
    try:
        actual_boxes = [tuple(float(value) for value in box) for box in raw_boxes]
    except (TypeError, ValueError):
        raise ValueError(f"{expected_scope} resource snapshot has invalid coverage metadata")
    if any(len(box) != 4 for box in actual_boxes):
        raise ValueError(f"{expected_scope} resource snapshot has invalid coverage metadata")
    config = PILOT_CITY if expected_scope == "urban" else PILOT_RURAL_COUNTY
    areas = config.get("resource_areas")
    required_boxes = [tuple(area["bbox"]) for area in areas] if areas else [tuple(config["bbox"])]
    for required in required_boxes:
        if not any(
            actual[0] <= required[0]
            and actual[1] <= required[1]
            and actual[2] >= required[2]
            and actual[3] >= required[3]
            for actual in actual_boxes
        ):
            raise ValueError(f"{expected_scope} resource snapshot does not cover the study area")
    return usable, payload


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _feature_and_score_payloads(
    region: str,
    database_path: Path,
    resource_path: Path,
    generated_at: str,
    *,
    context_path: Path | None = None,
) -> tuple[dict, dict]:
    rows, database_metadata = _read_database(database_path)
    if region == "chicago":
        context, context_manifest = _load_context(
            context_path, {str(row["tract_fips"]) for row in rows}
        )
        rows = [{**row, **context[str(row["tract_fips"])]} for row in rows]
        rows = [row for row in rows if row.get("is_chicago")]
        expected_count = 792
        resource_scope = "urban"
    else:
        context_manifest = None
        expected_count = int(PILOT_RURAL_COUNTY["expected_atlas_tract_count"])
        resource_scope = "rural"
    if len(rows) != expected_count:
        raise ValueError(f"{region} feature surface expected {expected_count} tracts, found {len(rows)}")
    resources, resource_manifest = _load_resources(resource_path, resource_scope)
    scores = score_all_gaps(rows, resources)
    feature_payload = {
        "feature_format": FEATURE_FORMAT,
        "region": region,
        "generated_at": generated_at,
        "tract_count": len(rows),
        "database_metadata": database_metadata,
        "context_generated_at": context_manifest.get("generated_at") if context_manifest else None,
        "records": rows,
    }
    score_payload = {
        "score_format": SCORE_FORMAT,
        "region": region,
        "generated_at": generated_at,
        "tract_count": len(scores),
        "weights": DEFAULT_WEIGHTS.normalized(),
        "resource_snapshot": {
            "scope": resource_manifest.get("scope"),
            "generated_at": resource_manifest.get("generated_at") or resource_manifest.get("refreshed_at"),
            "resource_count": len(resources),
        },
        "scores": scores,
    }
    return feature_payload, score_payload


def run_pipeline(args, *, s3_client=None) -> dict:
    from data import prep_acs, prep_atlas

    generated_at = datetime.now(timezone.utc).isoformat()
    client = s3_client or boto3.client("s3", region_name=args.region)
    store = VerifiedS3Store(client, args.bucket)
    managed_workspace = None
    if args.work_dir:
        workspace = Path(args.work_dir).resolve()
        workspace.mkdir(parents=True, exist_ok=True)
    else:
        managed_workspace = tempfile.TemporaryDirectory(prefix="food-access-evidence-")
        workspace = Path(managed_workspace.name)
    try:
        downloads = workspace / "downloads"
        source_paths = {
            "sram": downloads / "sram.zip",
            "gazetteer": downloads / "gazetteer.zip",
            "acs": downloads / "acs.json",
            "urban_context": downloads / "urban_context.json",
            "urban_resources": downloads / "urban_resources.json",
            "rural_resources": downloads / "rural_resources.json",
        }
        keys = {
            "sram": args.sram_key,
            "gazetteer": args.gazetteer_key,
            "acs": args.acs_key,
            "urban_context": args.urban_context_key,
            "urban_resources": args.urban_resource_key,
            "rural_resources": args.rural_resource_key,
        }
        input_manifest = {
            name: store.download(keys[name], path) for name, path in source_paths.items()
        }

        extracted = workspace / "extracted"
        safe_extract(source_paths["sram"], extracted / "sram")
        safe_extract(source_paths["gazetteer"], extracted / "gazetteer")
        sram_general = _find_named(extracted / "sram", "SRAM General Tract Characteristics Data.csv")
        sram_root = sram_general.parent
        centroid_path = _find_named(extracted / "gazetteer", "2020_Gaz_tracts_national.txt")

        frame, access_method, source_files = prep_atlas._load_data(sram_root, "SRAM", "driving")
        centroids = prep_atlas._load_centroids(centroid_path)
        output_dir = workspace / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        databases = {
            "urban": output_dir / "atlas_pilot_city.db",
            "rural": output_dir / "atlas_rural_fringe.db",
        }
        original_database_paths = {
            name: prep_atlas.REGIONS[name]["db_path"] for name in ("urban", "rural")
        }
        try:
            for name in ("urban", "rural"):
                # prep_atlas intentionally owns the region definitions. Only its
                # output destination is redirected into this ephemeral workspace.
                prep_atlas.REGIONS[name]["db_path"] = databases[name]
                prep_atlas.prepare_region_database(
                    frame,
                    name,
                    "SRAM",
                    sram_root,
                    centroids,
                    access_method=access_method,
                    source_files=source_files,
                    require_coordinates=True,
                )
        finally:
            for name, original in original_database_paths.items():
                prep_atlas.REGIONS[name]["db_path"] = original

        _, by_county, snapshot_sha = prep_acs.load_snapshot(source_paths["acs"], args.acs_year)
        acs_client = prep_acs.ACSClient(args.acs_year)
        for name, config in (("urban", PILOT_CITY), ("rural", PILOT_RURAL_COUNTY)):
            evidence = prep_acs.snapshot_evidence(acs_client, by_county, config["county_fips"])
            prep_acs.enrich_database(
                databases[name],
                evidence,
                args.acs_year,
                allow_extra_evidence=bool(config.get("rural_only")),
                allowed_extra_geoids=config.get("atlas_excluded_tract_fips", ()),
                snapshot_file=f"s3://{args.bucket}/{args.acs_key}",
                snapshot_sha256=snapshot_sha,
            )

        artifacts = {}
        for region_name, database_name, resource_name, context in (
            ("chicago", "urban", "urban_resources", source_paths["urban_context"]),
            ("rural", "rural", "rural_resources", None),
        ):
            features, scores = _feature_and_score_payloads(
                region_name,
                databases[database_name],
                source_paths[resource_name],
                generated_at,
                context_path=context,
            )
            feature_path = output_dir / f"tract_features_{region_name}.json"
            score_path = output_dir / f"priority_scores_{region_name}.json"
            _write_json(feature_path, features)
            _write_json(score_path, scores)
            artifacts[f"features_{region_name}"] = feature_path
            artifacts[f"scores_{region_name}"] = score_path
        artifacts["database_urban"] = databases["urban"]
        artifacts["database_rural"] = databases["rural"]

        report = {
            "pipeline_format": PIPELINE_FORMAT,
            "status": "prepared" if args.no_upload else "published",
            "generated_at": generated_at,
            "bucket": args.bucket,
            "raw_acs_committed_to_git": False,
            "inputs": input_manifest,
            "outputs": {},
        }
        content_types = {
            "database_urban": "application/vnd.sqlite3",
            "database_rural": "application/vnd.sqlite3",
        }
        for name, path in artifacts.items():
            key = f"{args.output_prefix.strip('/')}/{path.name}"
            if args.no_upload:
                report["outputs"][name] = {
                    "path": str(path), "key": key, "sha256": sha256_file(path)
                }
            else:
                report["outputs"][name] = store.upload(
                    path, key, content_type=content_types.get(name, "application/json")
                )
        report_path = output_dir / "tract_score_pipeline_report.json"
        _write_json(report_path, report)
        if not args.no_upload:
            report["report"] = store.upload(
                report_path,
                f"{args.output_prefix.strip('/')}/{report_path.name}",
                content_type="application/json",
            )
        if args.report:
            destination = Path(args.report).resolve()
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(report_path, destination)
        return report
    finally:
        if managed_workspace:
            managed_workspace.cleanup()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", default=os.getenv("EVIDENCE_BUCKET"), required=not os.getenv("EVIDENCE_BUCKET"))
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "us-east-1"))
    parser.add_argument("--evidence-date", default="2026-09-05")
    parser.add_argument("--acs-year", type=int, default=2024)
    parser.add_argument("--sram-key")
    parser.add_argument("--gazetteer-key")
    parser.add_argument("--acs-key")
    parser.add_argument("--urban-context-key")
    parser.add_argument("--urban-resource-key", default="resource-cache/urban.json")
    parser.add_argument("--rural-resource-key", default="resource-cache/rural.json")
    parser.add_argument("--output-prefix")
    parser.add_argument("--work-dir")
    parser.add_argument("--report")
    parser.add_argument("--no-upload", action="store_true")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    date = args.evidence_date
    args.sram_key = args.sram_key or f"raw/{date}/usda/sram_2025.zip"
    args.gazetteer_key = args.gazetteer_key or f"raw/{date}/census/2020_tract_gazetteer.zip"
    args.acs_key = args.acs_key or f"raw/{date}/census/acs_2024_approved_counties_snapshot.json"
    args.urban_context_key = args.urban_context_key or f"prepared/{date}/urban_scoring_context.json"
    args.output_prefix = args.output_prefix or f"prepared/{date}/tract-priority"
    report = run_pipeline(args)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
