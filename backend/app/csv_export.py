import csv
import io
import json
from datetime import datetime
from typing import Any


HEADERS = [
    "номер",
    "статус",
    "telegram_id",
    "username",
    "ім'я",
    "прізвище",
    "присутність",
    "був_онлайн",
    "premium",
    "verified",
    "bot",
    "scam",
    "fake",
    "біо",
    "деталі",
]


def _bit(value: object) -> str:
    return "1" if value else "0"


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def check_to_json(document: dict[str, Any]) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2)


def rows_to_csv(rows: list[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    buffer.write("\ufeff")
    writer = csv.writer(buffer)
    writer.writerow(HEADERS)
    for row in rows:
        writer.writerow(
            [
                _text(row.get("phone") or row.get("input_phone")),
                _text(row.get("status")),
                _text(row.get("telegram_id")),
                _text(row.get("username")),
                _text(row.get("first_name")),
                _text(row.get("last_name")),
                _text(row.get("presence")),
                _text(row.get("was_online")),
                _bit(row.get("premium")),
                _bit(row.get("verified")),
                _bit(row.get("bot")),
                _bit(row.get("scam")),
                _bit(row.get("fake")),
                _text(row.get("bio")),
                _text(row.get("error_detail")),
            ]
        )
    return buffer.getvalue()
