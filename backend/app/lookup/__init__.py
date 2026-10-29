from app.lookup.base import DeleteDeferred, FloodPause, SessionFatal, SessionLockLost
from app.lookup.import_contacts import ImportContactsLookup
from app.lookup.resolve_phone import ResolvePhoneLookup

__all__ = [
    "DeleteDeferred",
    "FloodPause",
    "ImportContactsLookup",
    "ResolvePhoneLookup",
    "SessionFatal",
    "SessionLockLost",
    "get_strategy",
]


def get_strategy(name: str) -> ResolvePhoneLookup | ImportContactsLookup:
    if name == "resolve_phone":
        return ResolvePhoneLookup()
    if name == "import_contacts":
        return ImportContactsLookup(enrich=False)
    if name == "import_and_enrich":
        return ImportContactsLookup(enrich=True)
    raise ValueError(name)
