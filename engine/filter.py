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


def sort_by_rank(apt_list: list[dict], sort_key: str = "trade_rate") -> list[dict]:
    """sort_key 기준으로 정렬 후 rank 부여.

    sort_key: "trade_rate"(회복률↑), "ppp_asc"(평당가↑), "ppp_desc"(평당가↓)
    trade_rate=None인 단지는 항상 맨 뒤로.
    """
    if sort_key == "ppp_asc":
        key_fn = lambda x: x.get("price_per_pyeong") or 0
        reverse = False
    elif sort_key == "ppp_desc":
        key_fn = lambda x: x.get("price_per_pyeong") or 0
        reverse = True
    else:
        key_fn = lambda x: x.get("trade_rate") if x.get("trade_rate") is not None else 9999
        reverse = False

    ranked = sorted(apt_list, key=key_fn, reverse=reverse)
    for i, apt in enumerate(ranked, 1):
        apt["rank"] = i
    return ranked
