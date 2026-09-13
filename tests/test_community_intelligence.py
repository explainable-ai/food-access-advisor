from services.community_intelligence import (
    enrich_route_with_community_intelligence,
    enrich_stops_with_community_intelligence,
    load_community_preference_matrix,
)


class MissingPreferenceStore:
    bucket = "test-bucket"

    def read(self, key):
        raise RuntimeError(f"missing {key}")


class PreferenceStore:
    bucket = "test-bucket"

    def read(self, key):
        return [
            {
                "tract_fips": "17031010100",
                "requested_categories": ["protein"],
                "preference_tags": ["beans"],
                "request_count": 7,
                "confidence": 0.9,
            }
        ]


def test_missing_community_source_is_labelled_synthetic_demo():
    matrix = load_community_preference_matrix(MissingPreferenceStore())

    assert matrix["source"] == "synthetic_demo"
    assert matrix["data_classification"] == "synthetic_demo"
    assert "not infer" in matrix["guardrail"].lower()


def test_verified_s3_preferences_enrich_matching_stop():
    route = {
        "selected_stops": [
            {
                "stop_id": "17031010100",
                "tract_fips": "17031010100",
                "need_score": 90,
            }
        ]
    }

    enriched, summary = enrich_route_with_community_intelligence(
        route, PreferenceStore()
    )

    stop = enriched["selected_stops"][0]
    assert summary["source"] == "s3"
    assert summary["synthetic"] is False
    assert stop["community_requested_categories"] == ["protein"]
    assert stop["preference_tags"] == ["beans"]
    assert stop["community_intelligence"]["matched_on"] == "tract_fips"


def test_generic_fallback_is_nutrition_based_not_demographic():
    stops, summary = enrich_stops_with_community_intelligence(
        [
            {
                "stop_id": "unknown-stop",
                "need_score": 80,
                "race": "must_not_drive_preferences",
                "ethnicity": "must_not_drive_preferences",
            }
        ],
        {
            "source": "synthetic_demo",
            "source_key": None,
            "data_classification": "synthetic_demo",
            "confidence": 0.0,
            "rows": [],
            "reason": "test",
            "guardrail": "No demographics.",
        },
    )

    assert summary["synthetic"] is True
    assert stops[0]["community_requested_categories"] == [
        "fresh produce",
        "protein",
        "whole grain",
    ]
    assert stops[0]["community_intelligence"]["matched_on"] == "generic_nutrition_fallback"


def test_disabled_or_unavailable_sources_are_not_marked_synthetic():
    for source in ("disabled", "unavailable"):
        stops, summary = enrich_stops_with_community_intelligence(
            [{"stop_id": "A"}],
            {
                "source": source,
                "source_key": None,
                "data_classification": "unavailable",
                "confidence": 0.0,
                "rows": [],
                "reason": "test",
                "guardrail": "test",
            },
        )
        assert summary["synthetic"] is False
        assert stops[0]["community_intelligence"]["synthetic"] is False
