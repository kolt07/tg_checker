from dataclasses import dataclass


@dataclass(frozen=True)
class LookupLimits:
    resolve_interval_seconds: float
    import_chunk_size: int
    import_pause_seconds: float
    enrich_interval_seconds: float
    max_import_retries: int


def clamp_resolve_interval(seconds: float) -> float:
    """contacts.resolvePhone: не частіше ніж один виклик на 3 секунди."""
    return max(3.0, float(seconds))


def clamp_import_chunk_size(size: int) -> int:
    return max(1, min(int(size), 20))


def clamp_import_pause(seconds: float) -> float:
    return max(1.0, float(seconds))


def clamp_enrich_interval(seconds: float) -> float:
    return max(0.5, float(seconds))


def build_limits(
    *,
    resolve_interval_seconds: float = 3.0,
    import_chunk_size: int = 5,
    import_pause_seconds: float = 5.0,
    enrich_interval_seconds: float = 1.0,
    max_import_retries: int = 2,
) -> LookupLimits:
    return LookupLimits(
        resolve_interval_seconds=clamp_resolve_interval(resolve_interval_seconds),
        import_chunk_size=clamp_import_chunk_size(import_chunk_size),
        import_pause_seconds=clamp_import_pause(import_pause_seconds),
        enrich_interval_seconds=clamp_enrich_interval(enrich_interval_seconds),
        max_import_retries=max(0, int(max_import_retries)),
    )


def limits_from_settings(settings: object) -> LookupLimits:
    return build_limits(
        resolve_interval_seconds=getattr(settings, "resolve_interval_seconds"),
        import_chunk_size=getattr(settings, "import_chunk_size"),
        import_pause_seconds=getattr(settings, "import_pause_seconds"),
        enrich_interval_seconds=getattr(settings, "enrich_interval_seconds"),
        max_import_retries=getattr(settings, "max_import_retries"),
    )


def limits_for_timeout(settings: object, timeout: float | None) -> LookupLimits:
    base = limits_from_settings(settings)
    if timeout is None:
        return base
    seconds = float(timeout)
    return build_limits(
        resolve_interval_seconds=seconds,
        import_chunk_size=base.import_chunk_size,
        import_pause_seconds=seconds,
        enrich_interval_seconds=seconds,
        max_import_retries=base.max_import_retries,
    )
