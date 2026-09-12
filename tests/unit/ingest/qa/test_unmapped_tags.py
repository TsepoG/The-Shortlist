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
