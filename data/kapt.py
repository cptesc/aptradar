"""공동주택 단지 목록 + 기본정보 API (K-apt) 연동"""

import os
import re
import time

import requests
from dotenv import load_dotenv
from thefuzz import process as fuzz_process

from config import AREA_CODE_MAP
from data.molit import _get_db, get_complexes_by_area, upsert_household_counts

load_dotenv()

_LIST_URL = "https://apis.data.go.kr/1613000/AptListService3/getTotalAptList3"
_BASS_URL = "https://apis.data.go.kr/1613000/AptBasisInfoServiceV4/getAphusBassInfoV4"
_MATCH_THRESHOLD = 80
_MATCH_LEN_RATIO = 0.4


# ── 전체 단지 목록 캐시 ────────────────────────────────────────────────────────

def _is_kapt_list_cached() -> bool:
    conn = _get_db()
    try:
        return conn.execute("SELECT COUNT(*) FROM kapt_list").fetchone()[0] > 0
    finally:
        conn.close()


def _cache_kapt_list() -> int:
    """전체 단지 목록 API → SQLite 저장 (최초 1회). Returns: 저장 건수"""
    key = os.getenv("KAPT_API_KEY", "")
    conn = _get_db()
    total_saved = 0
    try:
        r = requests.get(_LIST_URL, params={
            "serviceKey": key, "pageNo": 1, "numOfRows": 1, "_type": "json",
        }, timeout=10)
        total = int(r.json()["response"]["body"]["totalCount"] or 0)
        if total == 0:
            return 0

        pages = (total + 999) // 1000
        for page in range(1, pages + 1):
            r = requests.get(_LIST_URL, params={
                "serviceKey": key, "pageNo": page, "numOfRows": 1000, "_type": "json",
            }, timeout=15)
            items = r.json()["response"]["body"]["items"]
            if not items:
                break
            rows = [
                (x["kaptCode"], x.get("kaptName", ""), x.get("bjdCode", ""))
                for x in items
            ]
            conn.executemany(
                "INSERT OR IGNORE INTO kapt_list (kaptCode, kaptName, bjdCode) VALUES (?, ?, ?)",
                rows,
            )
            conn.commit()
            total_saved += len(rows)
            print(f"  [K-apt] 단지 목록 {page}/{pages} 페이지 ({total_saved}건)")
            time.sleep(0.1)
    except Exception as e:
        print(f"[K-apt] 목록 수집 실패: {e}")
    finally:
        conn.close()
    return total_saved


def _get_kapt_for_lawd(lawd_cd: str) -> list[dict]:
    """lawd_cd(5자리)로 K-apt 단지 목록 반환 (bjdCode 앞5자리 매칭)"""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT kaptCode, kaptName FROM kapt_list WHERE bjdCode LIKE ?",
            (lawd_cd + "%",),
        ).fetchall()
        return [{"kaptCode": r[0], "kaptName": r[1]} for r in rows]
    finally:
        conn.close()


# ── fuzzy 매칭 ────────────────────────────────────────────────────────────────

def _normalize(name: str) -> str:
    name = re.sub(r"\([^)]*\)|\[[^\]]*\]", "", name)
    name = re.sub(r"[^\w\s]", "", name)
    return re.sub(r"\s+", " ", name).strip()


def _fuzzy_match(target: str, candidates: list[str]) -> str | None:
    if not candidates:
        return None
    norm = _normalize(target)
    norm_map = {_normalize(c): c for c in candidates}
    result = fuzz_process.extractOne(norm, list(norm_map.keys()))
    if not result or result[1] < _MATCH_THRESHOLD:
        return None
    matched_norm = result[0]
    len_ratio = min(len(norm), len(matched_norm)) / max(len(norm), len(matched_norm), 1)
    if len_ratio < _MATCH_LEN_RATIO:
        return None
    return norm_map[matched_norm]


# ── 세대수 조회 ────────────────────────────────────────────────────────────────

def _fetch_household_count(kapt_code: str) -> int:
    key = os.getenv("KAPT_API_KEY", "")
    try:
        r = requests.get(_BASS_URL, params={
            "serviceKey": key, "kaptCode": kapt_code, "_type": "json",
        }, timeout=10)
        item = r.json().get("response", {}).get("body", {}).get("item", {})
        return int(item.get("kaptdaCnt") or 0)
    except Exception:
        return 0


# ── 메인 함수 ─────────────────────────────────────────────────────────────────

def sync_household_counts(area_name: str, area_type: int) -> int:
    """K-apt에서 세대수 가져와 MOLIT 단지명으로 매핑 후 DB 저장.
    Returns: 저장된 단지 수
    """
    if not _is_kapt_list_cached():
        print("[K-apt] 전체 단지 목록 최초 수집 중 (약 23회 API 호출)...")
        _cache_kapt_list()

    lawd_cds = AREA_CODE_MAP.get(area_name, [])
    if not lawd_cds:
        return 0

    molit_names = get_complexes_by_area(area_type, lawd_cds)
    if not molit_names:
        return 0

    # lawd_cd별 K-apt 단지 수집
    kapt_list: list[dict] = []
    for lawd_cd in lawd_cds:
        kapt_list.extend(_get_kapt_for_lawd(lawd_cd))

    if not kapt_list:
        return 0

    # K-apt 이름 → MOLIT 이름 fuzzy 매칭
    matched: dict[str, str] = {}  # molit_name → kaptCode
    for row in kapt_list:
        kapt_name = str(row.get("kaptName") or "").strip()
        kapt_code = str(row.get("kaptCode") or "").strip()
        if not kapt_name or not kapt_code:
            continue
        molit_name = _fuzzy_match(kapt_name, molit_names)
        if molit_name and molit_name not in matched:
            matched[molit_name] = kapt_code

    if not matched:
        return 0

    # 이미 DB에 세대수가 있는 단지는 스킵
    from data.molit import get_household_count
    new_matched = {
        name: code for name, code in matched.items()
        if get_household_count(name) == 0
    }

    # 새로 매칭된 단지만 세대수 조회 + DB 저장
    name_count: dict[str, int] = {}
    total = len(new_matched)
    for i, (molit_name, kapt_code) in enumerate(new_matched.items(), 1):
        print(f"  [K-apt] 세대수 조회 {i}/{total}: {molit_name}", flush=True)
        count = _fetch_household_count(kapt_code)
        if count > 0:
            name_count[molit_name] = count
        time.sleep(0.05)

    if name_count:
        upsert_household_counts(name_count)

    return len(name_count)
