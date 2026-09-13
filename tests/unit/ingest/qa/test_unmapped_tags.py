"""PHASE_1.md §6: the unmapped-tag report, ranked by frequency."""

from shortlist.ingest.companyfacts import UnmappedTag
from shortlist.ingest.qa.unmapped_tags import high_frequency_tags, render_markdown


def test_high_frequency_tags_filters_by_threshold() -> None:
    tags = [
        UnmappedTag("acme", "Common", count=10),
        UnmappedTag("acme", "Rare", count=1),
    ]

    result = high_frequency_tags(tags, threshold=5)

    assert result == (UnmappedTag("acme", "Common", count=10),)


def test_render_markdown_handles_empty_report() -> None:
    markdown = render_markdown(())
    assert "No unmapped tags" in markdown


def test_render_markdown_includes_every_tag() -> None:
    tags = [UnmappedTag("acme", "Foo", count=3), UnmappedTag("acme", "Bar", count=1)]

    markdown = render_markdown(tags)

    assert "Foo" in markdown
    assert "Bar" in markdown
    assert "acme" in markdown


def test_render_markdown_annotates_derivation_input_tags() -> None:
    # LiabilitiesAndStockholdersEquity is genuinely unclaimed by any alias
    # chain (still counted, per the test above), but it's consumed by
    # derive.py's total_liabilities derivation rather than simply missing an
    # alias — the report must say so rather than presenting it as untriaged.
    tags = [
        UnmappedTag("us-gaap", "LiabilitiesAndStockholdersEquity", count=1026),
        UnmappedTag("us-gaap", "ResearchAndDevelopmentExpense", count=50),
    ]

    markdown = render_markdown(tags)

    assert "LiabilitiesAndStockholdersEquity (consumed as a derivation input" in markdown
    assert "ResearchAndDevelopmentExpense (consumed" not in markdown
