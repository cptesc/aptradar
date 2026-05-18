import sys
import asyncio
sys.stdout.reconfigure(encoding="utf-8")

from data.molit import get_trade_history, get_peak, get_latest_trade
from crawler.naver import fetch_listings, match_complex
from engine.calculator import calc_price_per_pyeong, calc_recovery_rate, classify_cycle
from engine.filter import apply_filter, sort_by_rank
from config import PEAK_START_YEAR, MIN_TRADE_COUNT

AREA = "인천 서구"
AREA_TYPE = 84


async def main():
    # 1. Naver 호가 수집 (단지 3개 제한)
    print("Naver 호가 수집 중...\n")
    listings = await fetch_listings(AREA, AREA_TYPE, limit=15)
    print(f"  호가 {len(listings)}건 수집됨\n")

    # 2. 단지별로 묶기 (complex_name 기준)
    complexes: dict[str, list[dict]] = {}
    for item in listings:
        complexes.setdefault(item["complex_name"], []).append(item)

    # 3. 각 단지별 지표 계산
    apt_list: list[dict] = []

    for complex_name, items in complexes.items():
        # 가장 저렴한 호가 대표값으로 사용
        best = min(items, key=lambda x: x["price"])

        # MOLIT DB에서 실거래 이력 조회 (단지명 정확 매칭 시도)
        history = get_trade_history(complex_name, AREA_TYPE)

        # 직접 매칭 실패 시 fuzzy 매칭
        if not history:
            import sqlite3, os
            conn = sqlite3.connect(os.path.join("db", "apt_radar.db"))
            db_names = [r[0] for r in conn.execute(
                "SELECT DISTINCT complex_name FROM trades"
            ).fetchall()]
            conn.close()
            matched, score = match_complex(complex_name, db_names)
            if matched:
                history = get_trade_history(matched, AREA_TYPE)

        trade_count = len(history)
        peak = get_peak(history, PEAK_START_YEAR)
        latest = get_latest_trade(history)

        peak_price = peak["price"] if peak else 0
        latest_trade_price = latest["price"] if latest else 0

        ask_price = best["price"]
        area = best["area"]

        ask_rate = calc_recovery_rate(ask_price, peak_price) if peak_price else 0.0
        trade_rate = calc_recovery_rate(latest_trade_price, peak_price) if peak_price else 0.0
        price_per_pyeong = calc_price_per_pyeong(ask_price, area)
        cycle = classify_cycle(ask_rate)

        apt_list.append({
            "rank": 0,
            "complex_name": complex_name,
            "area": area,
            "ask_price": ask_price,
            "ask_rate": ask_rate,
            "latest_trade_price": latest_trade_price,
            "trade_rate": trade_rate,
            "peak_price": peak_price,
            "trade_count": trade_count,
            "price_per_pyeong": price_per_pyeong,
            "cycle": cycle,
            "url": best["url"],
        })

    def print_table(rows: list[dict], title: str) -> None:
        print(f"\n[{title}]  총 {len(rows)}개")
        print(f"{'순위':>3}  {'단지명':<30}  {'면적':>6}  {'호가':>7}  {'평당가':>7}  {'호가회복률':>8}  {'실거래회복률':>9}  {'전고점':>7}  {'거래건수':>6}  {'사이클'}")
        print("-" * 120)
        for apt in rows:
            print(
                f"{apt['rank']:>3}  "
                f"{apt['complex_name']:<30}  "
                f"{apt['area']:>6.1f}  "
                f"{apt['ask_price']:>7,}  "
                f"{apt['price_per_pyeong']:>7,}  "
                f"{apt['ask_rate']:>8.1f}%  "
                f"{apt['trade_rate']:>9.1f}%  "
                f"{apt['peak_price']:>7,}  "
                f"{apt['trade_count']:>6}  "
                f"{apt['cycle']}"
            )

    # 4. 전체 정렬 후 상위 5
    ranked = sort_by_rank(apt_list)
    print_table(ranked[:5], "전체 (필터 없음, 상위 5)")

    # 5. 신뢰도 필터 적용 후 상위 5 (호가/실거래 회복률 100% 이하, 거래건수 10건 이상)
    filtered = apply_filter(apt_list, ask_rate_max=100.0, trade_rate_max=100.0)
    ranked_filtered = sort_by_rank(filtered)
    print_table(ranked_filtered[:5], f"필터 적용 (거래건수≥{MIN_TRADE_COUNT}, 회복률≤100%, 상위 5)")


asyncio.run(main())
