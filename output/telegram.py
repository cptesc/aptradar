"""텔레그램 알림 출력"""


async def send_message(token: str, chat_id: str, text: str) -> None:
    """텍스트 메시지 전송"""
    pass


async def send_report(token: str, chat_id: str, data: list[dict]) -> None:
    """분석 결과 요약 리포트 전송"""
    pass


async def send_file(token: str, chat_id: str, filepath: str) -> None:
    """파일(Excel 등) 전송"""
    pass
