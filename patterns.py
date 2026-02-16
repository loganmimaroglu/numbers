import logging
import re

logger = logging.getLogger(__name__)

# --- Constants ---
CONTEXT_WINDOW = 30  # characters of surrounding text captured for inline matches

# --- Multiplier definitions (single source of truth) ---
# Each entry: (raw_pattern_str, label, factor)
# To add a new scale, just add a row here. Inline regexes are built automatically.
MULTIPLIERS = [
    (r"thousands?|k", "Thousand", 1_000),
    (r"millions?|m", "Million", 1_000_000),
    (r"billions?|b", "Billion", 1_000_000_000),
    (r"trillions?|t", "Trillion", 1_000_000_000_000),
]

# Compiled patterns for resolve_multiplier lookups
_MULTIPLIER_COMPILED = [
    (re.compile(pat, re.IGNORECASE), label, factor)
    for pat, label, factor in MULTIPLIERS
]

# Build a single alternation from all multiplier patterns for inline regexes
_mult_alt = "|".join(pat for pat, _, _ in MULTIPLIERS)

# Patterns for detecting multipliers in headers and table cells.
# Covers: "(Dollars in Millions)", "($ Millions)", "($M)", "Cash ($M)", etc.
HEADER_UNIT_PATTERNS = [
    # "(Dollars in Millions)", "(Amounts in Thousands)"
    re.compile(rf"\(\w+\s+in\s+({_mult_alt})\)", re.IGNORECASE),
    # "($ IN MILLIONS)", "($ IN THOUSANDS)" — TWCF budget format
    re.compile(rf"\(\s*\$\s+IN\s+({_mult_alt})\)", re.IGNORECASE),
    # "($ Millions)", "($Millions)", "($ M)"
    re.compile(rf"\(\$\s*({_mult_alt})\)", re.IGNORECASE),
    # "($M)" embedded in longer text like "Cash ($M)" or "Financial Performance ($M)"
    re.compile(rf"\(\s*\$\s*({_mult_alt})\s*\)", re.IGNORECASE),
]

# Inline patterns built from the same multiplier definitions
# "$9.6 billion", "$ 9.6 billion", "$6M", "$6.5B"
INLINE_DOLLAR_PATTERN = re.compile(
    rf"\$\s*([\d,]+\.?\d*)\s*({_mult_alt})\b", re.IGNORECASE
)
# "2.0 million", "9.6 billion" (no dollar sign, full words only to avoid false positives)
_mult_words = "|".join(pat for pat, _, _ in MULTIPLIERS if len(pat) > 2)
INLINE_BARE_PATTERN = re.compile(
    rf"([\d,]+\.?\d*)\s+({_mult_words})\b", re.IGNORECASE
)

# Matches table cell numbers: 8,137.477, .000, (.001), (48.843), 169,611.1
NUMBER_PATTERN = re.compile(r"^\s*\(?\s*[\d,]+\.?\d*\s*\)?\s*$")


def resolve_multiplier(text: str) -> tuple[str, int] | None:
    """Match a scale word/abbreviation and return (label, factor) or None."""
    text = text.strip()
    for pattern, label, factor in _MULTIPLIER_COMPILED:
        if pattern.fullmatch(text):
            return label, factor
    return None


def find_header_multiplier(text: str) -> tuple[str, int] | None:
    """Extract multiplier from header text.

    Handles: '(Dollars in Millions)', '($ Millions)', '($M)', 'Cash ($M)', etc.
    Returns (label, factor) or None.
    """
    for pattern in HEADER_UNIT_PATTERNS:
        match = pattern.search(text)
        if match:
            return resolve_multiplier(match.group(1))
    return None


def is_number(text: str) -> bool:
    """Check if text looks like a numeric value."""
    return bool(NUMBER_PATTERN.match(text))


def parse_number(text: str) -> float | None:
    """Parse an accounting-formatted number string into a float."""
    text = text.strip()
    if not text:
        return None
    negative = "(" in text and ")" in text
    cleaned = text.replace("(", "").replace(")", "").replace(",", "").strip()
    try:
        value = float(cleaned)
        return -value if negative else value
    except ValueError:
        logger.debug("parse_number failed: could not parse %r", text)
        return None
