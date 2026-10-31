"""Sign-in privacy line in the three UI locales."""

from __future__ import annotations

PRIVACY_LINES: dict[str, str] = {
    "en": "We use your email only to sign you in.",
    "nb": "Vi bruker e-posten din bare til å logge deg inn.",
    "uk": "Ми використовуємо вашу електронну пошту лише для входу.",
}


def signin_privacy_line(locale: str) -> str:
    """Privacy line for the sign-in screen; English fallback."""
    return PRIVACY_LINES.get(locale, PRIVACY_LINES["en"])
