"""Safe parser for CTA's official static GTFS ZIP feed."""

import csv
import io
import zipfile
from datetime import datetime

import requests

from data_sources.contracts import DataQualityReport, EvidenceStatus, ResourceEvidence, ResourceEvidenceBatch, SourceCitation, utc_now

CTA_GTFS_URL = "https://www.transitchicago.com/downloads/sch_data/google_transit.zip"
REQUIRED_MEMBERS = {"agency.txt", "stops.txt", "routes.txt", "trips.txt", "stop_times.txt"}


class CTAGTFSClient:
    def __init__(self, *, session: requests.Session | None = None, timeout_seconds: float = 60.0, clock=utc_now):
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds
        self.clock = clock

    def fetch_stops(self) -> ResourceEvidenceBatch:
        response = self.session.get(CTA_GTFS_URL, timeout=self.timeout_seconds)
        response.raise_for_status()
        retrieved: datetime = self.clock()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            missing_members = sorted(REQUIRED_MEMBERS - set(archive.namelist()))
            if missing_members:
                raise ValueError(f"CTA GTFS feed missing required files: {', '.join(missing_members)}")
            with archive.open("stops.txt") as raw:
                rows = list(csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig")))

        fields = ["stop_id", "stop_name", "stop_lat", "stop_lon", "location_type", "parent_station"]
        citation = SourceCitation(source_name="Chicago Transit Authority", dataset_name="GTFS Scheduled Service Data",
            dataset_id="cta-static-gtfs", official_url=CTA_GTFS_URL, vintage=retrieved.date().isoformat(),
            retrieved_at=retrieved, geographic_level="transit stop", fields_used=fields)
        records, excluded = [], 0
        for row in rows:
            stop_id, name = (row.get("stop_id") or "").strip(), (row.get("stop_name") or "").strip()
            try:
                lat, lon = float(row["stop_lat"]), float(row["stop_lon"])
            except (KeyError, TypeError, ValueError):
                excluded += 1
                continue
            if not stop_id or not name:
                excluded += 1
                continue
            records.append(ResourceEvidence(entity_id=stop_id, kind="transit_stop", name=name, lat=lat, lon=lon,
                status="scheduled", source_citation=citation, attributes={k: row.get(k) for k in fields if k in row}))
        status = EvidenceStatus.PARTIAL if excluded else EvidenceStatus.COMPLETE
        return ResourceEvidenceBatch(source_id="cta_gtfs", records=records, citation=citation,
            quality=DataQualityReport(status=status, source_row_count=len(rows), matched_rows=len(records),
                excluded_rows=excluded, warnings=["Static schedules do not include unexpected short-term reroutes."]))
