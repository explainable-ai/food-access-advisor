from services.context_engineering import (
    CONTEXT_SCHEMA_VERSION,
    build_context_envelope,
    record_agent_context,
)


def test_context_extracts_numeric_and_worded_request_constraints():
    context = build_context_envelope(
        "Take 200 pounds of produce, no dairy, in a four-hour rural window.",
        study_area=None,
        categories=["produce"],
        excluded_categories=["dairy"],
    )

    assert context.schema_version == CONTEXT_SCHEMA_VERSION
    assert context.request_intent.load_lbs == 200
    assert context.request_intent.time_window_hours == 4
    assert context.request_intent.study_area == "rural_fringe"
    assert context.request_intent.study_area_source == "inferred"
    assert context.request_intent.categories == ["produce"]
    assert context.request_intent.excluded_categories == ["dairy"]


def test_explicit_area_overrides_request_inference():
    context = build_context_envelope(
        "Plan a Chicago delivery with 50 lbs in 2 hrs.",
        study_area="rural_fringe",
        categories=[],
        excluded_categories=[],
    )

    assert context.request_intent.study_area == "rural_fringe"
    assert context.request_intent.study_area_source == "explicit"


def test_agent_context_records_identifiers_without_copying_payloads():
    context = build_context_envelope(
        "Plan 50 lbs in 2 hours.",
        study_area="chicago_neighborhoods",
        categories=[],
        excluded_categories=[],
    )

    record_agent_context(
        context,
        "scout",
        {
            "ranked_tracts": [
                {
                    "tract_fips": "17031010100",
                    "need_score": 88.5,
                    "dataset_version": "acs-2024",
                }
            ]
        },
    )

    view = context.agent_views["scout"]
    assert view.fact_ids == ["17031010100"]
    assert view.sources == ["acs-2024"]
    assert "need_score" not in view.model_dump()
    assert context.provenance[0].source_id == "acs-2024"


def test_context_defaults_unspecified_geography_to_chicago_and_discloses_it():
    context = build_context_envelope(
        "Plan 50 lbs in 2 hours.",
        study_area=None,
        categories=[],
        excluded_categories=[],
    )

    assert context.request_intent.study_area == "chicago_neighborhoods"
    assert context.request_intent.study_area_source == "default"
