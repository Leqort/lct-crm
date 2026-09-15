"""Text normalisation shared by the import matcher and the catalog services.

SPEC §9 is explicit that normalisation must not live inside the Excel parser:
future integrations (SPEC-04) write into the same catalogs and must fold names
exactly the same way, otherwise they will create near-duplicate universities.
"""

from __future__ import annotations

import re

_WHITESPACE_RE = re.compile(r"\s+")
# Quotation marks used interchangeably in Russian office documents.
_QUOTES_RE = re.compile(r"[«»“”„‘’\"']")
_PUNCT_RE = re.compile(r"[.,;:\-–—()]+")


def clean_text(value: str | None) -> str | None:
    """Trim and collapse whitespace; empty string becomes ``None``.

    This is what we actually *store*: the customer's file is the source of
    truth for spelling, we only tidy up stray spaces.
    """
    if value is None:
        return None
    text = _WHITESPACE_RE.sub(" ", str(value)).strip()
    return text or None


def normalize_name(value: str | None) -> str:
    """Fold a name into its comparison form.

    Case-insensitive, whitespace-collapsed, quotation marks and light
    punctuation dropped, `ё` unified with `е`. Used for exact matching and as
    the input to trigram similarity — never stored as the display value.
    """
    if value is None:
        return ""
    text = str(value).casefold().replace("ё", "е")
    text = _QUOTES_RE.sub(" ", text)
    text = _PUNCT_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def normalize_person_name(value: str | None) -> str:
    """Fold a person's full name.

    Same rules as `normalize_name`; kept as a separate function because the
    matching policy for people is expected to diverge (initials, name order).
    """
    return normalize_name(value)


def split_multi_value(value: str | None) -> list[str]:
    """Split a cell holding several values separated by `;` or `,`.

    Used for «Ответственные от ВУЗа», where the customer's catalog packs
    multiple people into one cell (SPEC §6, mapping table).
    """
    if value is None:
        return []
    parts = re.split(r"[;,\n]+", str(value))
    return [cleaned for part in parts if (cleaned := clean_text(part))]
