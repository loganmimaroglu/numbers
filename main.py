import argparse
import json
import logging
import pathlib

import pymupdf.layout  # noqa: F401 — activate PyMuPDF-Layout before pymupdf4llm
import pymupdf4llm

from extract import _log_timing, extract_from_pages

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract numbers from budget PDFs")
    parser.add_argument("pdf_path", nargs="?", default="./inputs/complete.pdf")
    parser.add_argument("--debug", action="store_true", help="Write raw markdown/json debug files")
    parser.add_argument(
        "--output-dir", type=pathlib.Path, default="./tmp",
        help="Directory for debug output files (default: ./tmp)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.WARNING)

    output_dir = args.output_dir

    if args.debug:
        output_dir.mkdir(parents=True, exist_ok=True)
        with _log_timing("to_markdown"):
            md_text = pymupdf4llm.to_markdown(args.pdf_path, page_chunks=False)
        output_dir.joinpath("tmp_raw.md").write_text(md_text, encoding="utf-8")

    with _log_timing("to_json"):
        raw_json = pymupdf4llm.to_json(args.pdf_path, page_chunks=True)

    if args.debug:
        output_dir.joinpath("tmp_raw.json").write_text(raw_json, encoding="utf-8")

    with _log_timing("json.loads"):
        parsed = json.loads(raw_json)

    with _log_timing("extraction"):
        source = pathlib.Path(args.pdf_path).name
        numbers = extract_from_pages(parsed["pages"], source)

    if args.debug:
        # Save as JSON for programmatic use
        output_dir.joinpath("tmp.json").write_text(
            json.dumps(numbers, indent=2), encoding="utf-8"
        )

        # Save readable markdown summary
        lines = ["# Extracted Numbers\n"]
        for n in numbers:
            if n.get("multiplier"):
                mult = f"x{n['multiplier']:,}"
                adj = f", adjusted={n['adjusted_value']:,.0f}" if n.get("adjusted_value") else ""
            else:
                mult = "NO MULTIPLIER"
                adj = ""
            line = (
                f"- **{n['raw']}** ({mult}{adj}) — "
                f"{n['row_label']} / {n['column']} "
                f"[page {n['page']}, {n['section']}]"
            )
            if n.get("context"):
                line += f"\n  > ...{n['context']}..."
            lines.append(line)
        output_dir.joinpath("tmp.md").write_text("\n".join(lines), encoding="utf-8")

    # Print warnings for numbers without multipliers
    for n in numbers:
        if not n.get("multiplier"):
            print(f"  WARNING: no multiplier for '{n['raw']}' — {n['row_label']} / {n['column']} [page {n['page']}, {n['section']}]")

    # Find largest numbers
    largest_raw = max(numbers, key=lambda n: abs(n["value"]))
    has_adjusted = [n for n in numbers if n.get("adjusted_value") is not None]
    largest_adj = max(has_adjusted, key=lambda n: abs(n["adjusted_value"])) if has_adjusted else None

    print(f"Extracted {len(numbers)} numbers from {args.pdf_path}")
    if args.debug:
        print(f"  {output_dir}/tmp.json     — structured data")
        print(f"  {output_dir}/tmp.md       — readable summary")
        print(f"  {output_dir}/tmp_raw.md   — raw pymupdf4llm markdown")
        print(f"  {output_dir}/tmp_raw.json — raw pymupdf4llm json")
    print()
    print(f"Largest raw: {largest_raw['raw']} [page {largest_raw['page']}]")
    if largest_adj:
        print(f"Largest adjusted: {largest_adj['adjusted_value']:,.0f} [page {largest_adj['page']}]")


if __name__ == "__main__":
    main()
