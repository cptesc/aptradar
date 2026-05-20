"""신뢰도 필터 및 정렬"""

from config import MIN_TRADE_COUNT


def apply_filter(
    apt_list: list[dict],
    trade_rate_max: float,
) -> list[dict]:
    """조건에 맞는 단지만 반환.

    제외 조건:
      - trade_count < MIN_TRADE_COUNT
      - trade_rate > trade_rate_max
    """
    return [
        apt for apt in apt_list
        if apt.get("trade_count", 0) >= MIN_TRADE_COUNT
        and apt.get("trade_rate", 0) <= trade_rate_max
    ]


def sort_by_rank(apt_list: list[dict]) -> list[dict]:
    """실거래 회복률 오름차순 정렬 (저점 단지가 1위)"""
    ranked = sorted(apt_list, key=lambda x: x.get("trade_rate", 999))
    for i, apt in enumerate(ranked, 1):
        apt["rank"] = i
    return ranked
