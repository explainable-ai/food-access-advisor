import io
import zipfile
from datetime import datetime, timezone

import pytest

from data_sources.cta_gtfs import CTAGTFSClient


class Response:
    def __init__(self, content): self.content = content
    def raise_for_status(self): return None


class Session:
    def __init__(self, content): self.content = content
    def get(self, url, timeout): return Response(self.content)


def feed(missing=None):
    output = io.BytesIO()
    names = {"agency.txt", "stops.txt", "routes.txt", "trips.txt", "stop_times.txt"} - {missing}
    with zipfile.ZipFile(output, "w") as archive:
        for name in names:
            content = "stop_id,stop_name,stop_lat,stop_lon\n1,Main,41.8,-87.6\n" if name == "stops.txt" else "id\n1\n"
            archive.writestr(name, content)
    return output.getvalue()


def test_gtfs_stops_are_parsed_without_extracting_archive():
    batch = CTAGTFSClient(session=Session(feed()),
        clock=lambda: datetime(2026, 8, 29, tzinfo=timezone.utc)).fetch_stops()
    assert batch.records[0].entity_id == "1"
    assert batch.records[0].kind == "transit_stop"
    assert batch.citation.source_name == "Chicago Transit Authority"


def test_gtfs_rejects_incomplete_feed():
    with pytest.raises(ValueError, match="stop_times.txt"):
        CTAGTFSClient(session=Session(feed("stop_times.txt"))).fetch_stops()
