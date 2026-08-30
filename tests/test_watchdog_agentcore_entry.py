"""Contract tests for the bounded AgentCore Watchdog entrypoint."""

import asyncio
import json

import pytest

import watchdog_agentcore_entry as entry


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"prompt": None},
        {"prompt": []},
        {"prompt": {}},
        {"prompt": "   "},
    ],
)
def test_handler_rejects_non_string_or_empty_prompts(payload):
    with pytest.raises(ValueError, match="payload|prompt"):
        asyncio.run(entry.handler(payload))


def test_handler_sends_only_compact_deterministic_result_to_reporter(monkeypatch):
    captured = {}
    run = {
        "status": "complete",
        "pending": 2,
        "checked": 2,
        "possible_change": 1,
        "still_needed": 1,
        "by_type": {"site": {"checked": 2, "possible_change": 1, "still_needed": 1}},
        "source_health": [{"source_id": "osm_resources", "scope": "urban", "status": "complete", "record_count": 500, "change_count": 2}],
        "result_count": 1,
        "results": [{"tract_fips": "17031000100", "recommendation_type": "site", "status": "possible_change", "nearest_kind": "grocery", "nearest_distance_miles": 0.4}],
        "error_count": 0,
        "errors": [],
    }

    class ReporterResult:
        message = {"content": [{"text": "One tract may have changed and needs verification."}]}

    def reporter(prompt):
        captured["prompt"] = prompt
        return ReporterResult()

    monkeypatch.setattr(entry, "run_watchdog_pass", lambda: run)
    monkeypatch.setattr(entry, "refresh_additional_sources", lambda: [{
        "source_id": "cta_gtfs",
        "scope": "urban",
        "status": "complete",
        "record_count": 1000,
        "changes": [{"large": "raw change must not be forwarded"}],
    }])
    monkeypatch.setattr(entry, "build_watchdog_reporter", lambda: reporter)

    result = asyncio.run(entry.handler({"prompt": "Run the pass."}))

    report_json = captured["prompt"].split("\n", 1)[1]
    report = json.loads(report_json)
    assert report["supplemental_sources"][0]["change_count"] == 1
    assert "raw change must not be forwarded" not in captured["prompt"]
    assert report["results"] == run["results"]
    assert result["reporting_status"] == "complete"
    assert result["summary"].startswith("One tract")


def test_no_pending_skips_bedrock_reporter(monkeypatch):
    monkeypatch.setattr(entry, "refresh_additional_sources", lambda: [])
    monkeypatch.setattr(entry, "run_watchdog_pass", lambda: {
        "status": "no_pending", "pending": 0, "checked": 0,
        "possible_change": 0, "still_needed": 0,
    })
    monkeypatch.setattr(
        entry,
        "build_watchdog_reporter",
        lambda: (_ for _ in ()).throw(AssertionError("reporter should not be built")),
    )

    result = asyncio.run(entry.handler({"prompt": "Run.", "refresh_additional_sources": False}))

    assert result["reporting_status"] == "skipped_no_pending"
    assert result["summary"].startswith("No pending")


def test_reporting_failure_does_not_erase_completed_state_updates(monkeypatch):
    monkeypatch.setattr(entry, "refresh_additional_sources", lambda: [])
    monkeypatch.setattr(entry, "run_watchdog_pass", lambda: {
        "status": "complete", "pending": 1, "checked": 1,
        "possible_change": 0, "still_needed": 1,
        "by_type": {}, "source_health": [], "result_count": 0,
        "results": [], "error_count": 0, "errors": [],
    })

    def fail_reporter(_prompt):
        raise RuntimeError("Bedrock unavailable")

    monkeypatch.setattr(entry, "build_watchdog_reporter", lambda: fail_reporter)
    result = asyncio.run(entry.handler({"prompt": "Run.", "refresh_additional_sources": False}))

    assert result["reporting_status"] == "failed"
    assert "Checked 1 of 1" in result["summary"]
    assert result["watchdog_run"]["errors"][-1]["stage"] == "reporting"
