"""One citation-address formatter for every UI locale.

Page/paragraph ("p. 14, para 3"), timestamps ("12:04–12:31"), and
figures ("Figure 2") localise here and nowhere else.
"""

from __future__ import annotations

from typing import Any

_PAGE = {"en": "p.", "nb": "s.", "uk": "с."}
_PARA = {"en": "para", "nb": "avsnitt", "uk": "абз."}
_FIGURE = {"en": "Figure", "nb": "Figur", "uk": "Рисунок"}


def _locale(locale: str) -> str:
    return locale if locale in ("en", "nb", "uk") else "en"


def format_citation_address(address: dict[str, Any], locale: str) -> str:
    """Human address of one passage in the requested UI locale."""
    lang = _locale(locale)
    kind = str(address.get("kind", ""))
    if kind == "pdf":
        return f"{_PAGE[lang]} {address.get('page', '?')}, {_PARA[lang]} {address.get('para', '?')}"
    if kind in ("md", "web"):
        path = str(address.get("heading_path") or address.get("anchor") or "?")
        para = address.get("para")
        if para is None:
            return path
        return f"{path}, {_PARA[lang]} {para}"
    if kind == "av":
        return f"{address.get('t_start', '?')}–{address.get('t_end', '?')}"
    if kind == "figure":
        return f"{_FIGURE[lang]} {address.get('figure', '?')}"
    return str(address.get("address_key", "?"))
