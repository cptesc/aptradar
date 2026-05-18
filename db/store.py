"""로컬 데이터 저장소"""


def save_trades(complex_id: str, trades: list[dict]) -> None:
    """실거래 내역 저장"""
    pass


def load_trades(complex_id: str) -> list[dict]:
    """저장된 실거래 내역 로드"""
    pass


def save_result(result: dict) -> None:
    """분석 결과 저장"""
    pass


def load_results() -> list[dict]:
    """저장된 분석 결과 전체 로드"""
    pass
