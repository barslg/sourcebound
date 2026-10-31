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
        "llm_unavailable": "The model is temporarily unavailable; try again.",
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
        "llm_unavailable": "Modellen er midlertidig utilgjengelig; prøv igjen.",
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
        "llm_unavailable": "Модель тимчасово недоступна; спробуйте ще раз.",
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


def error_keys() -> set[str]:
    """Message keys every locale must carry (parity gate)."""
    return set(ERROR_MESSAGES["en"])
