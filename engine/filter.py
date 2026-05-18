"""신뢰도 필터 및 정렬"""

from config import MIN_TRADE_COUNT


def apply_filter(
    apt_list: list[dict],
    ask_rate_max: float,
    trade_rate_max: float,
) -> list[dict]:
    """조건에 맞는 단지만 반환.

    제외 조건:
      - trade_count < MIN_TRADE_COUNT
      - ask_rate > ask_rate_max
      - trade_rate > trade_rate_max
    """
    return [
        apt for apt in apt_list
        if apt.get("trade_count", 0) >= MIN_TRADE_COUNT
        and apt.get("ask_rate", 0) <= ask_rate_max
        and apt.get("trade_rate", 0) <= trade_rate_max
    ]


def sort_by_rank(apt_list: list[dict]) -> list[dict]:
    """평당가 내림차순 정렬 후 rank(1위~) 컬럼 추가"""
    ranked = sorted(apt_list, key=lambda x: x.get("price_per_pyeong", 0), reverse=True)
    for i, apt in enumerate(ranked, 1):
        apt["rank"] = i
    return ranked
