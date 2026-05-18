"""사이클 지표 계산"""


def calc_peak_price(trades: list[dict], start_year: int) -> float:
    """전고점 가격 계산"""
    pass


def calc_current_price(trades: list[dict]) -> float:
    """현재(최근) 거래가 산출"""
    pass


def calc_cycle_ratio(current: float, peak: float) -> float:
    """전고점 대비 현재가 비율 (%) 계산"""
    pass


def classify_cycle(ratio: float, thresholds: dict) -> str:
    """비율로 RED / YELLOW / GREEN 분류"""
    pass
