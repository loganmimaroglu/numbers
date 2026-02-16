"""Tests for the extraction pipeline — exercises public functions without needing PDF files."""

import pytest

from patterns import (
    find_header_multiplier,
    parse_number,
    resolve_multiplier,
)
from extract import (
    extract_from_pages,
    extract_from_table,
    extract_from_text,
    extract_inline_numbers,
    mult_for_y,
    resolve_column_headers,
)


# --- parse_number (T3: parametrized) ---

@pytest.mark.parametrize("text, expected", [
    ("1,234", 1234.0),
    ("1,234.5", 1234.5),
    ("(48.843)", -48.843),
    ("0", 0.0),
    (".001", 0.001),
    ("", None),
    ("  ", None),
    ("1,754,801", 1754801.0),
])
def test_parse_number(text, expected):
    assert parse_number(text) == expected


# --- resolve_multiplier (T3: parametrized) ---

@pytest.mark.parametrize("text, expected", [
    ("Million", ("Million", 1_000_000)),
    ("million", ("Million", 1_000_000)),
    ("Millions", ("Million", 1_000_000)),
    ("m", ("Million", 1_000_000)),
    ("M", ("Million", 1_000_000)),
    ("K", ("Thousand", 1_000)),
    ("billions", ("Billion", 1_000_000_000)),
    ("B", ("Billion", 1_000_000_000)),
    ("Thousand", ("Thousand", 1_000)),
    ("Trillion", ("Trillion", 1_000_000_000_000)),
    ("widgets", None),
    ("  Million  ", ("Million", 1_000_000)),
])
def test_resolve_multiplier(text, expected):
    assert resolve_multiplier(text) == expected


# --- find_header_multiplier (T3: parametrized) ---

@pytest.mark.parametrize("text, expected", [
    ("(Dollars in Millions)", ("Million", 1_000_000)),
    ("(Amounts in Thousands)", ("Thousand", 1_000)),
    ("($ IN MILLIONS)", ("Million", 1_000_000)),
    ("($M)", ("Million", 1_000_000)),
    ("Cash ($M)", ("Million", 1_000_000)),
    ("Financial Performance ($M)", ("Million", 1_000_000)),
    ("Total Budget", None),
    ("($B)", ("Billion", 1_000_000_000)),
])
def test_find_header_multiplier(text, expected):
    assert find_header_multiplier(text) == expected


# --- extract_inline_numbers ---

class TestExtractInlineNumbers:
    def test_dollar_billion(self):
        results = extract_inline_numbers("budget is $9.6 billion for FY2025")
        assert len(results) == 1
        r = results[0]
        assert r["value"] == 9.6
        assert r["multiplier_label"] == "Billion"
        assert r["multiplier"] == 1_000_000_000
        assert r["adjusted_value"] == 9_600_000_000

    def test_dollar_m_compact(self):
        results = extract_inline_numbers("allocated $6M for the project")
        assert len(results) == 1
        assert results[0]["value"] == 6.0
        assert results[0]["adjusted_value"] == 6_000_000

    def test_bare_million(self):
        results = extract_inline_numbers("approximately 2.0 million units")
        assert len(results) == 1
        assert results[0]["value"] == 2.0
        assert results[0]["adjusted_value"] == 2_000_000

    def test_no_match(self):
        assert extract_inline_numbers("no numbers here") == []

    def test_dollar_with_comma(self):
        results = extract_inline_numbers("total of $1,234.5 million allocated")
        assert len(results) == 1
        assert results[0]["value"] == 1234.5

    def test_context_captured(self):
        results = extract_inline_numbers("The total budget is $9.6 billion for defense")
        assert len(results) == 1
        assert "context" in results[0]
        assert "billion" in results[0]["context"]

    # T4: dollar-prefix deduplication
    def test_dollar_not_double_counted(self):
        """'$9.6 billion' should not produce both a dollar and bare match."""
        results = extract_inline_numbers("$9.6 billion")
        assert len(results) == 1

    # T4: multiple matches in one string
    def test_multiple_matches(self):
        text = "Revenue was $5.2 billion and expenses were $3.1 million"
        results = extract_inline_numbers(text)
        assert len(results) == 2
        values = sorted(r["adjusted_value"] for r in results)
        assert values == [3_100_000, 5_200_000_000]

    def test_source_type_is_narrative(self):
        results = extract_inline_numbers("$9.6 billion")
        assert results[0]["source_type"] == "narrative"


# --- extract_from_text ---

class TestExtractFromText:
    def test_provenance_fields(self):
        results = extract_from_text(
            "allocated $5.2 billion",
            section="Defense",
            page=3,
            source="budget.pdf",
        )
        assert len(results) == 1
        r = results[0]
        assert r["section"] == "Defense"
        assert r["page"] == 3
        assert r["source"] == "budget.pdf"
        assert r["row_label"] == "inline"
        assert r["column"] == "narrative"
        assert r["adjusted_value"] == 5_200_000_000

    def test_no_inline_numbers(self):
        results = extract_from_text("no numbers", section="Intro", page=1, source="x.pdf")
        assert results == []


# --- extract_from_table ---

class TestExtractFromTable:
    def test_basic_with_multiplier(self, simple_table_rows, million_multiplier):
        results = extract_from_table(
            simple_table_rows,
            **million_multiplier,
            section="Procurement",
            page=5,
            source="budget.pdf",
        )
        assert len(results) == 1
        r = results[0]
        assert r["value"] == 1234.5
        assert r["multiplier_label"] == "Million"
        assert r["multiplier"] == 1_000_000
        assert r["adjusted_value"] == 1_234_500_000
        assert r["row_label"] == "Widget A"
        assert r["column"] == "FY2023"
        assert r["section"] == "Procurement"
        assert r["page"] == 5
        assert r["source_type"] == "table"

    def test_decimal_heuristic_whole_number(self, million_multiplier):
        """Whole numbers (no decimal) should skip the table multiplier."""
        rows = [
            ["Item", "FY2023"],
            ["Headcount", "150"],
        ]
        results = extract_from_table(rows, **million_multiplier)
        assert len(results) == 1
        r = results[0]
        assert r["value"] == 150.0
        assert r["multiplier_label"] is None
        assert r["multiplier"] == 1
        assert r["adjusted_value"] == 150.0

    def test_decimal_heuristic_decimal_number(self, million_multiplier):
        """Decimal numbers should get the table multiplier."""
        rows = [
            ["Item", "FY2023"],
            ["Budget", "150.5"],
        ]
        results = extract_from_table(rows, **million_multiplier)
        assert len(results) == 1
        assert results[0]["adjusted_value"] == 150_500_000

    def test_row_multiplier_override(self, million_multiplier):
        """Row label with multiplier (e.g. 'Hours in Thousands') overrides heuristic."""
        rows = [
            ["Item", "FY2023"],
            ["(Hours in Thousands)", "150"],
        ]
        results = extract_from_table(rows, **million_multiplier)
        assert len(results) == 1
        r = results[0]
        assert r["multiplier_label"] == "Thousand"
        assert r["multiplier"] == 1_000
        assert r["adjusted_value"] == 150_000

    def test_inline_numbers_in_table_cells(self):
        """Table cells containing narrative text like '$5.2 billion' are found."""
        rows = [
            ["Item", "FY2023", "Notes"],
            ["Widget A", "100.0", "Total of $5.2 billion allocated"],
        ]
        results = extract_from_table(rows)
        inline_results = [r for r in results if r["row_label"] == "inline"]
        assert len(inline_results) >= 1
        assert inline_results[0]["adjusted_value"] == 5_200_000_000
        assert inline_results[0]["source_type"] == "table_narrative"

    def test_no_headers_returns_empty(self):
        """Table with no discernible headers returns no results."""
        rows = [["100", "200"]]
        results = extract_from_table(rows)
        assert results == []

    def test_provenance_fields(self):
        rows = [
            ["Item", "FY2023"],
            ["Widget", "10.5"],
        ]
        results = extract_from_table(
            rows, section="Sec", page=7, source="test.pdf",
        )
        for r in results:
            if r["row_label"] != "inline":
                assert r["section"] == "Sec"
                assert r["page"] == 7
                assert r["source"] == "test.pdf"

    # T4: sub-row splitting
    def test_sub_row_splitting(self, million_multiplier):
        """Cells with newlines produce separate results per sub-row."""
        rows = [
            ["Item", "FY2023"],
            ["Equipment\nTotal", "100.5\n200.5"],
        ]
        results = extract_from_table(rows, **million_multiplier)
        structured = [r for r in results if r["source_type"] == "table"]
        assert len(structured) == 2
        assert structured[0]["value"] == 100.5
        assert structured[1]["value"] == 200.5
        # Note: row_label newlines are replaced with spaces before sub-label
        # splitting, so both sub-rows get the combined label
        assert structured[0]["row_label"] == "Equipment Total"
        assert structured[1]["row_label"] == "Equipment Total"

    # T4: empty table
    def test_empty_table(self):
        results = extract_from_table([])
        assert results == []


# --- resolve_column_headers ---

class TestResolveColumnHeaders:
    def test_single_header_row(self):
        rows = [
            ["Item", "FY2023", "FY2024"],
            ["Widget", "100.0", "200.0"],
        ]
        headers = resolve_column_headers(rows)
        assert headers == ["Item", "FY2023", "FY2024"]

    def test_multi_row_headers(self):
        rows = [
            ["", "FY2023", "FY2023", "FY2024", "FY2024"],
            ["Item", "Qty", "Cost", "Qty", "Cost"],
            ["Widget", "10", "100.0", "20", "200.0"],
        ]
        headers = resolve_column_headers(rows)
        assert headers is not None
        assert len(headers) == 5
        # First column should be "Item"
        assert headers[0] == "Item"
        # Sub-columns should combine parent + child
        assert "FY2023" in headers[1]
        assert "Qty" in headers[1]

    def test_empty_rows(self):
        assert resolve_column_headers([]) is None

    def test_no_data_rows(self):
        rows = [["Header A", "Header B"]]
        assert resolve_column_headers(rows) is None


# --- mult_for_y (T1/T4) ---

class TestMultForY:
    def test_picks_nearest_above(self):
        positions = [(10.0, "Thousand", 1_000), (50.0, "Million", 1_000_000)]
        assert mult_for_y(positions, 60.0) == ("Million", 1_000_000)
        assert mult_for_y(positions, 30.0) == ("Thousand", 1_000)

    def test_nothing_above(self):
        positions = [(50.0, "Million", 1_000_000)]
        assert mult_for_y(positions, 10.0) is None

    def test_empty_positions(self):
        assert mult_for_y([], 10.0) is None

    def test_exact_position(self):
        positions = [(10.0, "Thousand", 1_000)]
        assert mult_for_y(positions, 10.0) == ("Thousand", 1_000)


# --- extract_from_pages (T5: integration) ---

class TestExtractFromPages:
    @staticmethod
    def _make_text_box(text, y0, boxclass="text"):
        return {
            "boxclass": boxclass,
            "y0": y0,
            "textlines": [{"spans": [{"text": text}]}],
        }

    @staticmethod
    def _make_table_box(rows, y0):
        return {
            "boxclass": "table",
            "y0": y0,
            "table": {"extract": rows},
            "textlines": [],
        }

    def test_page_level_multiplier_applies_to_table(self):
        """A multiplier declared in text above a table should apply to that table."""
        pages = [{
            "page_number": 1,
            "boxes": [
                self._make_text_box("(Dollars in Millions)", y0=10.0),
                self._make_table_box(
                    [["Item", "FY2023"], ["Budget", "150.5"]],
                    y0=50.0,
                ),
            ],
        }]
        results = extract_from_pages(pages, source="test.pdf")
        table_results = [r for r in results if r["source_type"] == "table"]
        assert len(table_results) == 1
        assert table_results[0]["multiplier_label"] == "Million"
        assert table_results[0]["adjusted_value"] == 150_500_000

    def test_banner_table_promotes_multiplier(self):
        """A metadata-only table with multiplier but no data promotes to page level."""
        pages = [{
            "page_number": 1,
            "boxes": [
                # Banner table: has multiplier text but no numeric data
                self._make_table_box(
                    [["Fund", "Unit", "($M)"]],
                    y0=10.0,
                ),
                # Data table below — should pick up the promoted Million multiplier
                self._make_table_box(
                    [["Item", "FY2023"], ["Budget", "99.5"]],
                    y0=50.0,
                ),
            ],
        }]
        results = extract_from_pages(pages, source="test.pdf")
        table_results = [r for r in results if r["source_type"] == "table"]
        assert len(table_results) == 1
        assert table_results[0]["multiplier_label"] == "Million"
        assert table_results[0]["adjusted_value"] == 99_500_000

    def test_section_tracking(self):
        """Section headers are tracked and applied to subsequent boxes."""
        pages = [{
            "page_number": 1,
            "boxes": [
                self._make_text_box("Defense Budget", y0=5.0, boxclass="section-header"),
                self._make_text_box("(Dollars in Millions)", y0=10.0),
                self._make_table_box(
                    [["Item", "FY2023"], ["Jets", "500.0"]],
                    y0=50.0,
                ),
            ],
        }]
        results = extract_from_pages(pages, source="test.pdf")
        table_results = [r for r in results if r["source_type"] == "table"]
        assert len(table_results) == 1
        assert table_results[0]["section"] == "Defense Budget"

    def test_narrative_text_extraction(self):
        """Inline numbers in text boxes are extracted."""
        pages = [{
            "page_number": 2,
            "boxes": [
                self._make_text_box("The total is $5.2 billion", y0=10.0),
            ],
        }]
        results = extract_from_pages(pages, source="test.pdf")
        assert len(results) == 1
        assert results[0]["source_type"] == "narrative"
        assert results[0]["adjusted_value"] == 5_200_000_000
        assert results[0]["page"] == 2
