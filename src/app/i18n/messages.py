"""Localised backend error messages (en/nb/uk, equal key sets)."""

from __future__ import annotations

LOCALES: tuple[str, ...] = ("en", "nb", "uk")

ERROR_MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "bad_request": "The request is invalid.",
        "unauthenticated": "Sign in to continue.",
        "forbidden": "You do not have access to this.",
        "not_found": "Nothing was found here.",
        "conflict": "This was changed underneath you; reload and retry.",
        "unprocessable": "This item is held for review and cannot be used yet.",
        "unsupported_media": "This file type is not supported yet.",
        "rate_limited": "You have used today's allowance; try again tomorrow.",
        "llm_unavailable": "The model is temporarily unavailable; try again.",
        "cap_reached": "The spending cap was reached; raise the cap to continue.",
        "misconfigured": "The service is misconfigured.",
        "internal": "Something went wrong.",
    },
    "nb": {
        "bad_request": "Forespørselen er ugyldig.",
        "unauthenticated": "Logg inn for å fortsette.",
        "forbidden": "Du har ikke tilgang til dette.",
        "not_found": "Her ble det ikke funnet noe.",
        "conflict": "Dette ble endret mens du arbeidet; last inn på nytt og prøv igjen.",
        "unprocessable": "Dette elementet holdes tilbake for kontroll og kan ikke brukes ennå.",
        "unsupported_media": "Denne filtypen støttes ikke ennå.",
        "rate_limited": "Du har brukt opp dagens kvote; prøv igjen i morgen.",
        "llm_unavailable": "Modellen er midlertidig utilgjengelig; prøv igjen.",
        "cap_reached": "Utgiftstaket er nådd; hev taket for å fortsette.",
        "misconfigured": "Tjenesten er feilkonfigurert.",
        "internal": "Noe gikk galt.",
    },
    "uk": {
        "bad_request": "Неправильний запит.",
        "unauthenticated": "Увійдіть, щоб продовжити.",
        "forbidden": "У вас немає доступу до цього.",
        "not_found": "Тут нічого не знайдено.",
        "conflict": "Це змінили, поки ви працювали; оновіть і повторіть.",
        "unprocessable": "Цей елемент затримано для перевірки, його поки не можна використовувати.",
        "unsupported_media": "Цей тип файлів поки не підтримується.",
        "rate_limited": "Ви вичерпали добовий ліміт; спробуйте завтра.",
        "llm_unavailable": "Модель тимчасово недоступна; спробуйте ще раз.",
        "cap_reached": "Ліміт витрат досягнуто; підвищте ліміт, щоб продовжити.",
        "misconfigured": "Службу налаштовано неправильно.",
        "internal": "Щось пішло не так.",
    },
}


def error_message(code: str, locale: str) -> str:
    """Backend message for one error code in one UI locale.

    Unknown codes and locales fall back to English, never to a gap.
    """
    table = ERROR_MESSAGES.get(locale, ERROR_MESSAGES["en"])
    message = table.get(code)
    if message is not None:
        return message
    return ERROR_MESSAGES["en"].get(code, ERROR_MESSAGES["en"]["internal"])


LANGUAGE_NAMES: dict[str, dict[str, str]] = {
    "en": {"en": "English", "nb": "Norwegian", "uk": "Ukrainian"},
    "nb": {"en": "engelsk", "nb": "norsk", "uk": "ukrainsk"},
    "uk": {"en": "англійською", "nb": "норвезькою", "uk": "українською"},
}

FALLBACK_NOTICES: dict[str, str] = {
    "en": "This block is not yet translated into {target}; showing the {source} original.",
    "nb": "Denne blokken er ikke oversatt til {target} ennå; viser originalen på {source}.",
    "uk": "Цей блок ще не перекладено мовою {target}; показано оригінал мовою {source}.",
}


def language_name(code: str, locale: str) -> str:
    """Name of one language in one UI locale; an unknown code stays as written."""
    names = LANGUAGE_NAMES.get(locale, LANGUAGE_NAMES["en"])
    return names.get(code, code)


def fallback_notice(*, target_language: str, source_language: str) -> str | None:
    """Localised fallback notice naming both languages; None when the language is the source."""
    if target_language == source_language:
        return None
    locale = target_language if target_language in FALLBACK_NOTICES else "en"
    return FALLBACK_NOTICES[locale].format(
        target=language_name(target_language, locale),
        source=language_name(source_language, locale),
    )
