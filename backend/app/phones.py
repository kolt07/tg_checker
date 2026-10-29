import re

import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat

_HEADER = {"phone", "номер", "телефон"}
_SPLIT = re.compile(r"[\r\n,;]+")


def expand_phone_inputs(values: list[str]) -> list[str]:
    """Розбити вставлені рядки на окремі номери. Пробіли всередині номера лишаються."""
    expanded: list[str] = []
    for value in values:
        for chunk in _SPLIT.split(value):
            part = chunk.strip()
            if not part or part.lower() in _HEADER:
                continue
            expanded.append(part)
    return expanded


def _digits(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


def _e164(number: str, region: str | None) -> str | None:
    try:
        parsed = phonenumbers.parse(number, region)
    except NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, PhoneNumberFormat.E164)


def normalize_phone(raw: str, default_region: str = "UA") -> str | None:
    """Повернути E.164 або None, якщо номер невалідний.

    Номер без «+» спершу читається в регіоні за замовчуванням (український 050…).
    Якщо так він невалідний, цифри пробуються як міжнародний номер із кодом країни (7904… → +7904…).
    """
    text = raw.strip()
    if not text or len(text) > 40:
        return None
    digits = _digits(text)
    if not digits:
        return None
    if text.startswith("+"):
        return _e164("+" + digits, None)
    if digits.startswith("00"):
        return _e164("+" + digits[2:], None)

    national = _e164(text, default_region)
    if national:
        return national
    international = _e164("+" + digits, None)
    if international:
        return international
    if len(digits) == 11 and digits.startswith("8"):
        return _e164("+7" + digits[1:], None)
    return None
