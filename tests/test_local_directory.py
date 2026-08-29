from datetime import datetime, timezone

from data_sources.local_directory import load_resource_csv


def test_curated_directory_preserves_source_and_excludes_bad_rows(tmp_path):
    path = tmp_path / "pantries.csv"
    path.write_text("id,name,lat,lon,status,phone\n1,Pantry,41.8,-87.6,open,555\n2,Bad,,,open,\n")
    batch = load_resource_csv(path, source_name="Local partner", dataset_name="Pantry directory",
        official_url="https://example.org/directory", vintage="2026-08", kind="food_pantry",
        clock=lambda: datetime(2026, 8, 29, tzinfo=timezone.utc))
    assert len(batch.records) == 1
    assert batch.records[0].attributes["phone"] == "555"
    assert batch.quality.excluded_rows == 1
