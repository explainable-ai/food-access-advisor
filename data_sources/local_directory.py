"""Explicit import path for pantry/market directories without a stable API."""

import csv
from datetime import datetime
from pathlib import Path

from data_sources.contracts import DataQualityReport, EvidenceStatus, ResourceEvidence, ResourceEvidenceBatch, SourceCitation, utc_now


def load_resource_csv(path: str | Path, *, source_name: str, dataset_name: str, official_url: str,
                      vintage: str, kind: str, clock=utc_now) -> ResourceEvidenceBatch:
    """Load a curator-provided CSV. Required columns: id,name,lat,lon."""
    path = Path(path)
    retrieved: datetime = clock()
    citation = SourceCitation(source_name=source_name, dataset_name=dataset_name, official_url=official_url,
        vintage=vintage, retrieved_at=retrieved, geographic_level="point", fields_used=["id", "name", "lat", "lon", "status"])
    records, excluded, row_count = [], 0, 0
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            row_count += 1
            try:
                entity_id, name = row["id"].strip(), row["name"].strip()
                lat, lon = float(row["lat"]), float(row["lon"])
                if not entity_id or not name:
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                excluded += 1
                continue
            records.append(ResourceEvidence(entity_id=entity_id, kind=kind, name=name, lat=lat, lon=lon,
                status=row.get("status") or None, source_citation=citation,
                attributes={k: v for k, v in row.items() if k not in {"id", "name", "lat", "lon", "status"}}))
    status = EvidenceStatus.PARTIAL if excluded else EvidenceStatus.COMPLETE
    return ResourceEvidenceBatch(source_id=f"local:{dataset_name}", records=records, citation=citation,
        quality=DataQualityReport(status=status, source_row_count=row_count, matched_rows=len(records), excluded_rows=excluded,
            warnings=["Curated import; freshness depends on the supplied vintage and source owner."]))
