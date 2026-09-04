"""Content guardrails for Chicago Site Advisor evidence briefs."""

from pathlib import Path


def test_site_brief_requires_atlas_product_and_publication_vintage():
    source = (
        Path(__file__).parent.parent / "tools" / "site_evidence_brief.py"
    ).read_text(encoding="utf-8")
    assert "USDA 2025 SNAP-authorized Retailer Access" in source
    assert "Map (SRAM)" in source
    assert "Food Access Research Atlas" in source
    assert "2025 publication vintage" in source
