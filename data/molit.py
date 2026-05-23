"""국토교통부 아파트매매 실거래가 상세 자료 수집 및 SQLite 캐싱"""

import os
import sqlite3
import time
from datetime import datetime

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
        CREATE TABLE IF NOT EXISTS complex_info (
            complex_name    TEXT PRIMARY KEY,
            household_count INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS kapt_list (
            kaptCode TEXT PRIMARY KEY,
            kaptName TEXT,
            bjdCode  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_trades_complex
            ON trades (complex_name, area);
        CREATE INDEX IF NOT EXISTS idx_kapt_bjd
            ON kapt_list (bjdCode);
        CREATE TABLE IF NOT EXISTS metadata (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
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

def get_peak(trade_history: list[dict], end_year: int) -> dict | None:
    """end_year 이전(포함) 최고 거래금액과 거래일 반환.

    Returns:
        {"price": int, "date": "YYYY-MM-DD"} 또는 None (데이터 없음)
    """
    filtered = [t for t in trade_history if t["year"] <= end_year]
    if not filtered:
        return None
    peak = max(filtered, key=lambda t: t["price"])
    return {
        "price": peak["price"],
        "date": f"{peak['year']}-{peak['month']:02d}-{peak['day']:02d}",
    }


# ── 기능 5: 지역+면적 단지 목록 조회 ─────────────────────────────────────────

def get_complexes_by_area(area_type: int, lawd_cds: list[str]) -> list[str]:
    """해당 지역·면적 거래 이력이 있는 단지명 목록 반환 (MOLIT DB 기준)"""
    if area_type not in _AREA_RANGES:
        raise ValueError(f"지원하지 않는 area_type: {area_type}")
    area_min, area_max = _AREA_RANGES[area_type]
    conn = _get_db()
    try:
        if lawd_cds:
            ph = ",".join("?" * len(lawd_cds))
            rows = conn.execute(
                f"""SELECT DISTINCT complex_name FROM trades
                    WHERE area BETWEEN ? AND ? AND lawd_cd IN ({ph})""",
                (area_min, area_max) + tuple(lawd_cds),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT DISTINCT complex_name FROM trades
                   WHERE area BETWEEN ? AND ?""",
                (area_min, area_max),
            ).fetchall()
        return [r[0] for r in rows if r[0]]
    finally:
        conn.close()


# ── 기능 6: 최근 실거래가 조회 ────────────────────────────────────────────────

def get_latest_trade(trade_history: list[dict]) -> dict | None:
    """가장 최근 월의 최고 거래금액과 거래일 반환.

    같은 월 내 이상치(급매·친족 간 거래 등) 방지를 위해
    가장 최근 월의 거래 중 최고가를 사용한다.

    Returns:
        {"price": int, "date": "YYYY-MM-DD"} 또는 None (데이터 없음)
    """
    if not trade_history:
        return None
    latest = max(trade_history, key=lambda t: (t["year"], t["month"], t["day"]))
    latest_ym = (latest["year"], latest["month"])
    same_month = [t for t in trade_history if (t["year"], t["month"]) == latest_ym]
    best = max(same_month, key=lambda t: t["price"])
    return {
        "price": best["price"],
        "date": f"{best['year']}-{best['month']:02d}-{best['day']:02d}",
    }


# ── 기능 7: 세대수 조회 / 저장 ───────────────────────────────────────────────────

def get_household_count(complex_name: str) -> int:
    """단지명으로 저장된 세대수 반환 (없으면 0)"""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT household_count FROM complex_info WHERE complex_name=?",
            (complex_name,),
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def upsert_household_counts(name_count: dict) -> None:
    """단지명 → 세대수 매핑을 DB에 저장 (이미 있으면 갱신)"""
    if not name_count:
        return
    conn = _get_db()
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO complex_info (complex_name, household_count) VALUES (?, ?)",
            name_count.items(),
        )
        conn.commit()
    finally:
        conn.close()


def get_metadata(key: str) -> str | None:
    conn = _get_db()
    try:
        row = conn.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def set_metadata(key: str, value: str) -> None:
    conn = _get_db()
    try:
        conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
    finally:
        conn.close()


def invalidate_current_month(area_list: list[str]) -> None:
    """현재 월의 fetched_months 캐시 삭제 → 다음 수집 시 재조회"""
    ym = datetime.now().strftime("%Y%m")
    conn = _get_db()
    try:
        for area in area_list:
            for lawd_cd in AREA_CODE_MAP.get(area, []):
                conn.execute(
                    "DELETE FROM fetched_months WHERE lawd_cd=? AND deal_ymd=?",
                    (lawd_cd, ym),
                )
        conn.commit()
    finally:
        conn.close()
