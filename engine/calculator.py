"""사이클 지표 계산"""

from config import CYCLE_THRESHOLDS

_PYEONG = 3.3058  # 1평 = 3.3058㎡


def calc_price_per_pyeong(price: int, area_m2: float) -> int:
    """호가(만원) + 전용면적(㎡) → 평당가(만원/평, 정수)"""
    return round(price / (area_m2 / _PYEONG))


def calc_recovery_rate(current_price: int, peak_price: int) -> float:
    """전고점 대비 현재가 회복률(%, 소수점 1자리)"""
    if not peak_price:
        return 0.0
    return round(current_price / peak_price * 100, 1)


def classify_cycle(recovery_rate: float) -> str:
    """회복률로 RED / YELLOW / GREEN 분류 (config.CYCLE_THRESHOLDS 기준)"""
    if recovery_rate >= CYCLE_THRESHOLDS["RED"]:
        return "RED"
    if recovery_rate >= CYCLE_THRESHOLDS["YELLOW"]:
        return "YELLOW"
    return "GREEN"
