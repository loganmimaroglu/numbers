import json
import logging
import pathlib
import time
from contextlib import contextmanager
from typing import Literal, TypedDict

import pymupdf.layout  # noqa: F401 — activate PyMuPDF-Layout
import pymupdf4llm

from patterns import (
    CONTEXT_WINDOW,
    HEADER_UNIT_PATTERNS,
    INLINE_BARE_PATTERN,
    INLINE_DOLLAR_PATTERN,
    find_header_multiplier,
    is_number,
    parse_number,
    resolve_multiplier,
)

logger = logging.getLogger(__name__)


class ExtractedNumber(TypedDict):
    value: float
    raw: str
    multiplier_label: str | None
    multiplier: int
    adjusted_value: float
    row_label: str
    column: str
    section: str | None
    page: int | None
    source: str | None
    source_type: Literal["table", "narrative", "table_narrative"]
    context: str | None


def _make_result(
    *,
    value: float,
    raw: str,
    multiplier_label: str | None,
    multiplier: int,
    row_label: str,
    column: str,
    source_type: Literal["table", "narrative", "table_narrative"],
    section: str | None = None,
    page: int | None = None,
    source: str | None = None,
    context: str | None = None,
) -> ExtractedNumber:
    """Build an ExtractedNumber dict with computed adjusted_value."""
    return {
        "value": value,
        "raw": raw,
        "multiplier_label": multiplier_label,
        "multiplier": multiplier,
        "adjusted_value": value * multiplier,
        "row_label": row_label,
        "column": column,
        "section": section,
        "page": page,
        "source": source,
        "source_type": source_type,
        "context": context,
    }


@contextmanager
def _log_timing(label: str):
    """Context manager that logs elapsed time at DEBUG level."""
    t0 = time.perf_counter()
    yield
    logger.debug("%s: %.2fs", label, time.perf_counter() - t0)


def get_box_text(box: dict) -> str:
    """Extract all text from a box's textlines or table extract."""
    parts = []
    for tl in box.get("textlines") or []:
        for span in tl.get("spans", []):
            parts.append(span.get("text", ""))
    if box.get("table"):
        for row in box["table"].get("extract", []):
            for cell in row:
                if cell:
                    parts.append(cell)
    return " ".join(parts)


def resolve_column_headers(rows: list[list]) -> list[str] | None:
    """Determine column headers from the first few rows of a table.

    Handles simple single-header-row tables and multi-row headers where
    a fiscal year spans sub-columns (Quantity, Unit Cost, Total Cost).
    Returns a flat list of column header strings, one per column.
    """
    if not rows:
        return None

    col_count = max(len(r) for r in rows)

    # Find the first row that contains actual numeric data
    data_start = None
    for i, row in enumerate(rows):
        for cell in row:
            if cell and is_number(cell.split("\n")[0]):
                data_start = i
                break
        if data_start is not None:
            break

    if data_start is None or data_start == 0:
        return None

    header_rows = rows[:data_start]

    # Simple case: single header row
    if len(header_rows) == 1:
        return [(cell or "").replace("\n", " ").strip() for cell in header_rows[0]]

    # Multi-row headers: build composite names by combining parent + sub headers.
    # Forward-fill None/empty cells in upper rows (merged cell spans).
    filled = []
    for row in header_rows:
        padded = list(row) + [None] * (col_count - len(row))
        filled.append(padded)

    for row in filled:
        last = None
        for j in range(len(row)):
            if row[j] and row[j].strip():
                last = row[j].replace("\n", " ").strip()
            else:
                row[j] = last

    # Combine all header rows into one label per column
    headers = []
    for j in range(col_count):
        parts = []
        seen = set()
        for row in filled:
            val = (row[j] or "").strip()
            if val and val not in seen:
                parts.append(val)
                seen.add(val)
        headers.append(" / ".join(parts) if parts else f"col_{j}")

    return headers


def extract_inline_numbers(text: str) -> list[ExtractedNumber]:
    """Find inline numbers like '$9.6 billion', '$6M', '2.0 million' in text."""
    found = []
    dollar_spans = []

    for match in INLINE_DOLLAR_PATTERN.finditer(text):
        num_str = match.group(1).replace(",", "")
        scale = resolve_multiplier(match.group(2))
        if not scale:
            continue
        label, factor = scale
        value = float(num_str)
        start = max(0, match.start() - CONTEXT_WINDOW)
        end = min(len(text), match.end() + CONTEXT_WINDOW)
        context = text[start:end].replace("\n", " ").strip()
        found.append(_make_result(
            value=value, raw=match.group(0).strip(),
            multiplier_label=label, multiplier=factor,
            row_label="inline", column="narrative",
            source_type="narrative", context=context,
        ))
        dollar_spans.append((match.start(), match.end()))

    for match in INLINE_BARE_PATTERN.finditer(text):
        # Skip if this match overlaps with a dollar-pattern match
        if any(ds <= match.start() < de for ds, de in dollar_spans):
            continue
        num_str = match.group(1).replace(",", "")
        scale = resolve_multiplier(match.group(2))
        if not scale:
            continue
        label, factor = scale
        value = float(num_str)
        start = max(0, match.start() - CONTEXT_WINDOW)
        end = min(len(text), match.end() + CONTEXT_WINDOW)
        context = text[start:end].replace("\n", " ").strip()
        found.append(_make_result(
            value=value, raw=match.group(0).strip(),
            multiplier_label=label, multiplier=factor,
            row_label="inline", column="narrative",
            source_type="narrative", context=context,
        ))

    return found


def extract_from_text(
    text: str,
    section: str | None = None,
    page: int | None = None,
    source: str | None = None,
) -> list[ExtractedNumber]:
    """Extract inline numbers from narrative text and attach provenance fields.

    Wraps extract_inline_numbers() with section/page/source metadata.
    Testable with plain strings: extract_from_text("budget is 9.6 billion")
    """
    results = []
    for inline in extract_inline_numbers(text):
        results.append({**inline, "section": section, "page": page, "source": source})
    return results


def extract_from_table(
    rows: list[list],
    multiplier_label: str | None = None,
    multiplier: int = 1,
    section: str | None = None,
    page: int | None = None,
    source: str | None = None,
) -> list[ExtractedNumber]:
    """Extract numbers from structured table rows.

    Takes row data (the table["extract"] format) and handles: header resolution,
    data-row iteration, sub-row splitting, decimal heuristic, row-level multiplier
    override. Also scans cells for inline numbers.

    Testable with list-of-lists:
        extract_from_table(
            [["", "FY2023"], ["Item A", "1,234.5"]],
            multiplier_label="Million", multiplier=1_000_000,
        )
    """
    results = []

    headers = resolve_column_headers(rows)
    if not headers:
        return results

    # Find where data rows start (first row with a number)
    data_start = 0
    for i, row in enumerate(rows):
        for cell in row:
            if cell and is_number(cell.split("\n")[0]):
                data_start = i
                break
        if data_start > 0:
            break

    for row in rows[data_start:]:
        # Column 0 is typically the row label
        row_label = (row[0] or "").replace("\n", " ").strip() if row else ""

        # Check if the row label itself declares a multiplier
        # e.g. "(Hours in Thousands)" — overrides the decimal heuristic
        row_mult = find_header_multiplier(row_label)

        for col_idx in range(1, len(row)):
            cell = row[col_idx] if col_idx < len(row) else None
            if not cell:
                continue

            # A cell can contain multiple values separated by newlines
            # (e.g. "1\n1" for Equipment + Total sub-rows)
            sub_labels = row_label.split("\n") if "\n" in (row[0] or "") else [row_label]
            sub_values = cell.split("\n")

            for vi, val_text in enumerate(sub_values):
                val_text = val_text.strip()
                if not is_number(val_text):
                    continue

                parsed_val = parse_number(val_text)
                if parsed_val is None:
                    continue

                col_header = headers[col_idx] if col_idx < len(headers) else f"col_{col_idx}"
                sub_label = sub_labels[vi].strip() if vi < len(sub_labels) else sub_labels[-1].strip()

                # If the row label itself declares a multiplier (e.g.
                # "(Hours in Thousands)"), use that unconditionally.
                # Otherwise, whole numbers (no decimal) are typically
                # counts/headcounts — skip the table-level multiplier.
                if row_mult:
                    effective_label, effective_factor = row_mult
                elif "." in val_text:
                    effective_label, effective_factor = multiplier_label, multiplier
                else:
                    effective_label, effective_factor = None, 1

                results.append(_make_result(
                    value=parsed_val, raw=val_text,
                    multiplier_label=effective_label, multiplier=effective_factor,
                    row_label=sub_label, column=col_header,
                    source_type="table",
                    section=section, page=page, source=source,
                ))

    # Also scan all table cells for inline numbers in narrative text
    for row in rows:
        for cell in row:
            if not cell:
                continue
            for inline in extract_inline_numbers(cell):
                results.append({
                    **inline,
                    "row_label": "inline",
                    "column": "table narrative",
                    "source_type": "table_narrative",
                    "section": section,
                    "page": page,
                    "source": source,
                })

    return results


def mult_for_y(
    mult_positions: list[tuple[float, str, int]], y: float
) -> tuple[str, int] | None:
    """Return the most recent (label, factor) declared above y-position."""
    result = None
    for uy, label, factor in mult_positions:
        if uy <= y:
            result = (label, factor)
    return result


def extract_from_pages(pages: list[dict], source: str) -> list[ExtractedNumber]:
    """Extract numbers from pre-parsed page data (pymupdf4llm JSON structure).

    Walks boxes, resolves page-level multipliers, handles banner table promotion,
    delegates to extract_from_text() and extract_from_table().
    Testable with synthetic page dicts.
    """
    results = []

    for page in pages:
        page_num = page["page_number"]
        boxes = sorted(page.get("boxes", []), key=lambda b: b["y0"])

        # Find multiplier declarations and their y-positions from non-table boxes.
        # Table-embedded multipliers (like "Cash ($M)") apply only to that table.
        mult_positions = []
        section_name = None
        for box in boxes:
            if box["boxclass"] == "table":
                continue
            text = get_box_text(box)
            scale = find_header_multiplier(text)
            if scale:
                label, factor = scale
                mult_positions.append((box["y0"], label, factor))

        for box in boxes:
            bc = box["boxclass"]

            if bc == "section-header":
                text = get_box_text(box)
                # Skip section headers that are only a multiplier declaration
                stripped = text
                for p in HEADER_UNIT_PATTERNS:
                    stripped = p.sub("", stripped)
                if not find_header_multiplier(text) or stripped.strip():
                    section_name = text

            # Extract inline numbers from narrative text boxes
            if bc == "text":
                text = get_box_text(box)
                results.extend(extract_from_text(
                    text, section=section_name, page=page_num, source=source,
                ))

            elif bc == "table" and box.get("table"):
                table = box["table"]
                rows = table["extract"]
                if not rows:
                    continue

                # Check if this table has any data rows (cells with numbers)
                has_data = any(
                    cell and is_number(cell.split("\n")[0])
                    for row in rows for cell in row if cell
                )

                # Check for multiplier embedded in table cells/headers
                table_text = " ".join(cell for row in rows for cell in row if cell)
                table_mult = find_header_multiplier(table_text)

                if table_mult and not has_data:
                    # Pure metadata/banner table (e.g. Fund/Unit/FY info).
                    # Promote its multiplier to page-level so data tables below can use it.
                    label, factor = table_mult
                    mult_positions.append((box["y0"], label, factor))
                    continue

                # Use table-embedded multiplier if found, otherwise fall back to
                # page-level, otherwise default to 1 (no scaling).
                if table_mult:
                    mult_label, mult_factor = table_mult
                else:
                    scale = mult_for_y(mult_positions, box["y0"])
                    if scale:
                        mult_label, mult_factor = scale
                    else:
                        mult_label, mult_factor = None, 1
                        # Log first cell of first data row for identification
                        table_name = next(
                            ((row[0] or "").replace("\n", " ").strip() for row in rows if row and row[0]),
                            "unknown",
                        )
                        logger.info("no multiplier for table '%s' [page %d]", table_name, page_num)

                results.extend(extract_from_table(
                    rows,
                    multiplier_label=mult_label,
                    multiplier=mult_factor,
                    section=section_name,
                    page=page_num,
                    source=source,
                ))

    return results


def extract_from_pdf(path: str) -> list[ExtractedNumber]:
    """Extract all numeric values from tables and narrative text in a PDF.

    Thin wrapper: calls pymupdf4llm, then delegates to extract_from_pages().
    For debug output, use the CLI (main.py --debug).
    """
    with _log_timing("to_json"):
        data = pymupdf4llm.to_json(path, page_chunks=True)
    with _log_timing("json.loads"):
        parsed = json.loads(data)
    with _log_timing("extraction"):
        source = pathlib.Path(path).name
        results = extract_from_pages(parsed["pages"], source)
    return results
