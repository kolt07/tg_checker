def assign_lane_indexes(phones: list[str | None], lane_count: int) -> list[int | None]:
    """Розкласти номери по акаунтах по колу. Однаковий номер лишається на одному акаунті."""
    if lane_count < 1:
        raise ValueError("lane_count")
    owner: dict[str, int] = {}
    next_lane = 0
    assigned: list[int | None] = []
    for phone in phones:
        if not phone:
            assigned.append(None)
            continue
        lane = owner.get(phone)
        if lane is None:
            lane = next_lane % lane_count
            owner[phone] = lane
            next_lane += 1
        assigned.append(lane)
    return assigned
