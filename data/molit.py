"""국토교통부 아파트매매 실거래가 상세 자료 수집 및 SQLite 캐싱"""

import os
import sqlite3
import time

import requests
from dotenv import load_dotenv

from config import AREA_CODE_MAP

load_dotenv()

_API_URL = (
    "https://apis.data.go.kr"
    "/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
)
_DB_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "db", "apt_radar.db")
)
_MAX_RETRIES = 3
_PAGE_SIZE = 1000

# area_type → (전용면적 하한, 상한) ㎡
_AREA_RANGES = {
    59: (55.0, 65.0),
    84: (80.0, 90.0),
}


# ── DB 초기화 ─────────────────────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trades (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            lawd_cd      TEXT    NOT NULL,
            deal_ymd     TEXT    NOT NULL,
            complex_name TEXT,
            area         REAL,
            floor        INTEGER,
            price        INTEGER,
            year         INTEGER,
            month        INTEGER,
            day          INTEGER
        );
        CREATE TABLE IF NOT EXISTS fetched_months (
            lawd_cd  TEXT NOT NULL,
            deal_ymd TEXT NOT NULL,
            PRIMARY KEY (lawd_cd, deal_ymd)
        );
        CREATE INDEX IF NOT EXISTS idx_trades_complex
            ON trades (complex_name, area);
    """)
    return conn


# ── 내부 유틸 ─────────────────────────────────────────────────────────────────

def _ym_range(start_ym: str, end_ym: str) -> list[str]:
    """'YYYYMM' 범위의 모든 월 문자열 리스트 생성"""
    result = []
    y, m = int(start_ym[:4]), int(start_ym[4:])
    ey, em = int(end_ym[:4]), int(end_ym[4:])
    while (y, m) <= (ey, em):
        result.append(f"{y}{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return result


def _parse_row(row: dict) -> dict:
    return {
        "complex_name": str(row.get("aptNm", "")).strip(),
        "area":  float(row.get("excluUseAr", 0) or 0),
        "floor": int(row.get("floor", 0) or 0),
        "price": int(str(row.get("dealAmount", "0")).replace(",", "").strip() or 0),
        "year":  int(row.get("dealYear", 0) or 0),
        "month": int(row.get("dealMonth", 0) or 0),
        "day":   int(row.get("dealDay", 0) or 0),
    }


# ── 기능 1: 월별 실거래가 수집 ────────────────────────────────────────────────

def fetch_trades(lawd_cd: str, deal_ymd: str) -> list[dict]:
    """법정동코드 + 년월(YYYYMM)로 실거래 내역 API 조회 (최대 3회 재시도)

    Returns:
        [{"complex_name", "area", "floor", "price", "year", "month", "day"}, ...]
    """
    api_key = os.getenv("MOLIT_API_KEY", "")
    results: list[dict] = []
    page = 1

    while True:
        params = {
            "serviceKey": api_key,
            "LAWD_CD": lawd_cd,
            "DEAL_YMD": deal_ymd,
            "pageNo": page,
            "numOfRows": _PAGE_SIZE,
            "_type": "json",
        }

        body: dict = {}
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = requests.get(_API_URL, params=params, timeout=10)
                resp.raise_for_status()
                body = resp.json()
                break
            except Exception as exc:
                if attempt == _MAX_RETRIES:
                    raise RuntimeError(
                        f"API 호출 실패 [{lawd_cd}/{deal_ymd} p{page}]: {exc}"
                    )
                time.sleep(1)

        resp_body = body.get("response", {}).get("body", {})
        raw_items = resp_body.get("items") or {}
        rows = raw_items.get("item", []) if isinstance(raw_items, dict) else []
        if isinstance(rows, dict):   # 결과 1건이면 dict로 반환됨
            rows = [rows]

        results.extend(_parse_row(r) for r in rows)

        total = int(resp_body.get("totalCount", 0) or 0)
        if not rows or page * _PAGE_SIZE >= total:
            break
        page += 1

    return results


# ── 기능 2: 지역+기간 전체 수집 + SQLite 캐싱 ────────────────────────────────

def fetch_all_trades(
    area_list: list[str], start_ym: str, end_ym: str
) -> None:
    """지역 리스트 전체를 기간 범위로 수집해 DB에 저장.
    이미 수집된 (lawd_cd, 년월)은 API 호출 없이 스킵.
    """
    conn = _get_db()
    months = _ym_range(start_ym, end_ym)

    try:
        for area in area_list:
            lawd_cds = AREA_CODE_MAP.get(area, [])
            if not lawd_cds:
                print(f"[경고] AREA_CODE_MAP에 '{area}' 없음, 스킵")
                continue

            for lawd_cd in lawd_cds:
                for ym in months:
                    already = conn.execute(
                        "SELECT 1 FROM fetched_months WHERE lawd_cd=? AND deal_ymd=?",
                        (lawd_cd, ym),
                    ).fetchone()
                    if already:
                        continue

                    trades = fetch_trades(lawd_cd, ym)
                    if trades:
                        conn.executemany(
                            """INSERT INTO trades
                               (lawd_cd, deal_ymd, complex_name, area, floor,
                                price, year, month, day)
                               VALUES (:lawd_cd, :deal_ymd, :complex_name, :area,
                                       :floor, :price, :year, :month, :day)""",
                            [{**t, "lawd_cd": lawd_cd, "deal_ymd": ym} for t in trades],
                        )
                    conn.execute(
                        "INSERT OR IGNORE INTO fetched_months VALUES (?, ?)",
                        (lawd_cd, ym),
                    )
                    conn.commit()
                    print(f"  수집 완료: {area} / {lawd_cd} / {ym} ({len(trades)}건)")
    finally:
        conn.close()


# ── 기능 3: 단지+면적 기준 이력 조회 ─────────────────────────────────────────

def get_trade_history(
    complex_name: str,
    area_type: int,
    lawd_cds: list[str] | None = None,
) -> list[dict]:
    """단지명 + area_type으로 DB에서 거래 이력 반환.

    lawd_cds: 지역 법정동코드 리스트 — 지정 시 해당 지역 데이터만 반환.
              None이면 전체 지역 검색 (지역 오매칭 주의).
    """
    if area_type not in _AREA_RANGES:
        raise ValueError(f"지원하지 않는 area_type: {area_type} (59 또는 84만 허용)")

    area_min, area_max = _AREA_RANGES[area_type]
    conn = _get_db()
    try:
        if lawd_cds:
            ph = ",".join("?" * len(lawd_cds))
            rows = conn.execute(
                f"""SELECT complex_name, area, floor, price, year, month, day
                    FROM trades
                    WHERE complex_name = ? AND area BETWEEN ? AND ?
                      AND lawd_cd IN ({ph})
                    ORDER BY year, month, day""",
                (complex_name, area_min, area_max) + tuple(lawd_cds),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT complex_name, area, floor, price, year, month, day
                   FROM trades
                   WHERE complex_name = ? AND area BETWEEN ? AND ?
                   ORDER BY year, month, day""",
                (complex_name, area_min, area_max),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── 기능 4: 전고점 계산 ───────────────────────────────────────────────────────

def get_peak(trade_history: list[dict], start_year: int) -> dict | None:
    """start_year 이후 최고 거래금액과 거래일 반환.

    Returns:
        {"price": int, "date": "YYYY-MM-DD"} 또는 None (데이터 없음)
    """
    filtered = [t for t in trade_history if t["year"] >= start_year]
    if not filtered:
        return None
    peak = max(filtered, key=lambda t: t["price"])
    return {
        "price": peak["price"],
        "date": f"{peak['year']}-{peak['month']:02d}-{peak['day']:02d}",
    }


# ── 기능 5: 최근 실거래가 조회 ────────────────────────────────────────────────

def get_latest_trade(trade_history: list[dict]) -> dict | None:
    """가장 최근 거래금액과 거래일 반환.

    Returns:
        {"price": int, "date": "YYYY-MM-DD"} 또는 None (데이터 없음)
    """
    if not trade_history:
        return None
    latest = max(
        trade_history,
        key=lambda t: (t["year"], t["month"], t["day"]),
    )
    return {
        "price": latest["price"],
        "date": f"{latest['year']}-{latest['month']:02d}-{latest['day']:02d}",
    }
