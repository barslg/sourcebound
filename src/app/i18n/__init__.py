"""Backend i18n: error messages, citation addresses, sign-in privacy line."""

from __future__ import annotations

from app.i18n.citations import format_citation_address
from app.i18n.messages import LOCALES, error_message
from app.i18n.privacy import signin_privacy_line

__all__ = [
    "LOCALES",
    "error_message",
    "format_citation_address",
    "signin_privacy_line",
]
