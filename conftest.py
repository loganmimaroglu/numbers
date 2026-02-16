import pytest


@pytest.fixture
def simple_table_rows():
    """Basic table with header row and one data row."""
    return [
        ["Item", "FY2023"],
        ["Widget A", "1,234.5"],
    ]


@pytest.fixture
def million_multiplier():
    """Common million multiplier kwargs for extract_from_table."""
    return {"multiplier_label": "Million", "multiplier": 1_000_000}
