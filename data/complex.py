"""아파트 단지 정보 처리"""


def match_complex(naver_name: str, molit_name: str) -> float:
    """단지명 유사도 매칭 (thefuzz 사용)"""
    pass


def build_complex_profile(naver_data: dict, molit_data: list[dict]) -> dict:
    """네이버 + 실거래 데이터를 단지 프로파일로 통합"""
    pass


def filter_by_area_type(trades: list[dict], area_type: int) -> list[dict]:
    """전용면적 타입으로 거래 필터링"""
    pass
