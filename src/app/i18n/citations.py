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


def format_citation_short(address: dict[str, Any], locale: str) -> str:
    """The chip-sized address: the last heading only; other kinds as the full one."""
    kind = str(address.get("kind", ""))
    if kind not in ("md", "web"):
        return format_citation_address(address, locale)
    path = str(address.get("heading_path") or address.get("anchor") or "?")
    last = path.rsplit(">", 1)[-1].strip() or path
    para = address.get("para")
    if para is None:
        return last
    return f"{last}, {_PARA[_locale(locale)]} {para}"


def display_title(title: str, address: dict[str, Any]) -> str:
    """The source title a reader sees: a URL title becomes the page's first heading."""
    if not title.lower().startswith(("http://", "https://")):
        return title
    path = str(address.get("heading_path") or "")
    first = path.split(">", 1)[0].strip()
    return first or title
