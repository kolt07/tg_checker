import base64
import binascii

from telethon.crypto import AuthKey
from telethon.sessions import StringSession

# Публічні IPv4 продакшн-DC Telegram. Ключ авторизації прив’язаний до DC.
PRODUCTION_DC_IPV4 = {
    1: "149.154.175.53",
    2: "149.154.167.51",
    3: "149.154.175.100",
    4: "149.154.167.91",
    5: "149.154.171.5",
}


def auth_key_to_string_session(auth_key: str, dc_id: int) -> str:
    if dc_id not in PRODUCTION_DC_IPV4:
        raise ValueError("dc_id має бути від 1 до 5")
    key = decode_auth_key(auth_key)
    session = StringSession()
    session.set_dc(dc_id, PRODUCTION_DC_IPV4[dc_id], 443)
    session.auth_key = AuthKey(key)
    saved = session.save()
    if not isinstance(saved, str) or not saved:
        raise ValueError("Не вдалося зібрати сесію з MTProto ключа")
    return saved


def decode_auth_key(value: str) -> bytes:
    text = "".join(value.split())
    if text[:2].lower() == "0x":
        text = text[2:]
    if len(text) == 512 and all(char in "0123456789abcdefABCDEF" for char in text):
        return bytes.fromhex(text)
    padded = text + "=" * (-len(text) % 4)
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            raw = decoder(padded.encode("ascii"))
        except (binascii.Error, ValueError):
            continue
        if len(raw) == 256:
            return raw
    raise ValueError("MTProto ключ має містити 256 байт у hex або base64")
