# Takehome

Extracts text and tabular data and determines the largest number.

## Setup

Requires **Python 3.12**.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
python main.py <pdf_path> [--debug]
```

**Arguments:**

| Argument | Description |
|---|---|
| `pdf_path` | Path to the PDF file to extract from. Defaults to `./inputs/page33.pdf` if omitted. |
| `--debug` | Write debug files and enable verbose logging (DEBUG level). |

**Examples:**

```bash
# Extract from the full document
python main.py ./inputs/complete.pdf
```

## Running Tests

```bash
pytest tests/ -v
```
