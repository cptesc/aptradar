"""텔레그램 알림"""

import os
import requests


def send_message(token: str, chat_id: str, text: str) -> None:
    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        timeout=10,
    )


def send_report(token: str, chat_id: str, data: list[dict]) -> None:
    if not data:
        send_message(token, chat_id, "분석 결과가 없습니다.")
        return

    emoji = {"RED": "🔴", "YELLOW": "🟡", "GREEN": "🟢"}
    lines = ["<b>🏠 APT Radar 분석 결과</b>", ""]
    for apt in data:
        e = emoji.get(apt.get("cycle", ""), "")
        trade_rate = apt.get("trade_rate")
        rate_str = f"{trade_rate:.1f}% {e}" if trade_rate is not None else "-"
        lines += [
            f"{apt['rank']}위 <b>{apt['complex_name']}</b> ({apt.get('area_name', '')})",
            f"  평당가 {apt['price_per_pyeong']:,}만원 | 최근실거래 {apt['latest_trade_price']:,}만원",
            f"  실거래회복률 {rate_str}",
            "",
        ]
    send_message(token, chat_id, "\n".join(lines))


def send_file(token: str, chat_id: str, filepath: str) -> None:
    filename = os.path.basename(filepath)
    with open(filepath, "rb") as f:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendDocument",
            data={"chat_id": chat_id},
            files={"document": (filename, f,
                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            timeout=60,
        )
