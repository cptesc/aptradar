"""네이버 부동산 매물 호가 크롤러 (fin.land.naver.com 응답 인터셉트 방식)

전략:
  1. complexClusters POST → bbox로 단지 목록(좌표·세대수 포함) 수집
  2. 좌표 기반 필터링으로 행정구역 외 단지 제거
  3. 각 단지 페이지 방문 → article/list 응답 인터셉트
  4. exclusiveSpace 범위로 필터링
"""

import asyncio
import json
import re

from playwright.async_api import async_playwright, BrowserContext, Page
from thefuzz import process as fuzz_process

from data.molit import upsert_household_counts

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_BASE = "https://fin.land.naver.com"
_ARTICLE_URL_TPL = "https://fin.land.naver.com/complexes/{complex_no}?articleNumber={article_no}"

_AREA_RANGES = {
    59: (55.0, 65.0),
    84: (80.0, 90.0),
}

# complexClusters API 쿼리용 bbox (약간 넓게 설정)
_AREA_BBOX: dict[str, dict] = {
    "인천 서구":   {"left": 126.648, "right": 126.820, "bottom": 37.470, "top": 37.620},
    "경기 부천시": {"left": 126.742, "right": 126.885, "bottom": 37.475, "top": 37.565},
    "경기 광명시": {"left": 126.851, "right": 126.926, "bottom": 37.415, "top": 37.490},
    "경기 의왕시": {"left": 126.955, "right": 127.050, "bottom": 37.320, "top": 37.420},
    "경기 안양시": {"left": 126.880, "right": 127.015, "bottom": 37.365, "top": 37.455},
    "서울시":      {"left": 126.730, "right": 127.185, "bottom": 37.425, "top": 37.705},
}

# 행정구역 실제 경계에 맞춘 좌표 필터 bbox
# 인천 서구: right를 126.760으로 제한 → 강서구(126.80+), 부평구 동측 제외
_AREA_STRICT_BBOX: dict[str, dict] = {
    "인천 서구":   {"left": 126.630, "right": 126.760, "bottom": 37.470, "top": 37.620},
    "경기 부천시": {"left": 126.742, "right": 126.885, "bottom": 37.475, "top": 37.565},
    "경기 광명시": {"left": 126.851, "right": 126.926, "bottom": 37.415, "top": 37.490},
    "경기 의왕시": {"left": 126.955, "right": 127.050, "bottom": 37.320, "top": 37.420},
    "경기 안양시": {"left": 126.880, "right": 127.015, "bottom": 37.365, "top": 37.455},
    "서울시":      {"left": 126.730, "right": 127.185, "bottom": 37.425, "top": 37.705},
}

_MATCH_THRESHOLD = 80
# 단지명 길이 비율이 이 미만이면 매칭 거부 (짧은 MOLIT 이름이 긴 네이버 이름에 부분 일치하는 오류 방지)
_MATCH_LEN_RATIO = 0.4

_FILTER_APT_SALE = {
    "tradeTypes": ["A1"], "realEstateTypes": ["A01"],
    "roomCount": [], "bathRoomCount": [], "optionTypes": [],
    "oneRoomShapeTypes": [], "moveInTypes": [],
    "filtersExclusiveSpace": False, "floorTypes": [], "directionTypes": [],
    "hasArticlePhoto": False, "isAuthorizedByOwner": False,
    "parkingTypes": [], "entranceTypes": [], "hasArticle": False,
}

_ARTICLE_TIMEOUT_MS = 8000


# ── 내부 유틸 ─────────────────────────────────────────────────────────────────

def _parse_floor(floor_info: str) -> int:
    try:
        return int(str(floor_info).split("/")[0].strip())
    except (ValueError, IndexError):
        return 0


# ── 단지 목록 조회 ─────────────────────────────────────────────────────────────

async def _get_complex_list(ctx: BrowserContext, bbox: dict) -> list[dict]:
    """complexClusters API → [{complex_no, lon, lat, household_count}, ...]"""
    resp = await ctx.request.post(
        f"{_BASE}/front-api/v1/complex/complexClusters",
        data=json.dumps({
            "filter": _FILTER_APT_SALE,
            "boundingBox": bbox,
            "precision": 13,
            "userChannelType": "PC",
        }),
        headers={
            "Content-Type": "application/json",
            "Referer": f"{_BASE}/map",
            "Accept": "application/json",
        },
    )
    if resp.status != 200:
        return []
    data = await resp.json()
    clusters = (data.get("result") or {}).get("clusters", [])
    result = []
    for c in clusters:
        if "complexNumber" not in c:
            continue
        coords = c.get("coordinates") or {}
        result.append({
            "complex_no": c["complexNumber"],
            "lon": float(coords.get("xCoordinate") or 0),
            "lat": float(coords.get("yCoordinate") or 0),
            "household_count": int(c.get("totalHouseholdNumber") or 0),
        })
    return result


# ── 단지 페이지 방문 + article/list 인터셉트 ──────────────────────────────────

def _filter_articles(
    items: list[dict],
    complex_no: int,
    area_min: float,
    area_max: float,
    household_count: int = 0,
) -> list[dict]:
    results = []
    for item in items:
        info = (item.get("representativeArticleInfo") or {})
        if info.get("tradeType") != "A1":
            continue
        space = (info.get("spaceInfo") or {})
        area = float(space.get("exclusiveSpace") or 0)
        if not (area_min <= area <= area_max):
            continue
        detail = (info.get("articleDetail") or {})
        price_info = (info.get("priceInfo") or {})
        article_no = info.get("articleNumber", "")
        deal_price_won = int(price_info.get("dealPrice") or 0)
        results.append({
            "complex_name": str(info.get("complexName") or "").strip(),
            "area": area,
            "floor": _parse_floor(detail.get("floorInfo", "")),
            "price": deal_price_won // 10000,
            "url": _ARTICLE_URL_TPL.format(complex_no=complex_no, article_no=article_no),
            "household_count": household_count,
        })
    return results


async def _visit_and_capture(
    page: Page,
    complex_no: int,
    area_min: float,
    area_max: float,
    household_count: int = 0,
) -> list[dict]:
    """단지 페이지 방문 → article/list 응답 캡처"""
    try:
        async with page.expect_response(
            lambda r: "complex/article/list" in r.url and r.status == 200,
            timeout=_ARTICLE_TIMEOUT_MS,
        ) as resp_info:
            await page.goto(
                f"{_BASE}/complexes/{complex_no}?tab=article",
                wait_until="commit",
                timeout=15000,
            )
        resp = await resp_info.value
        body = await resp.json()
        items = (body.get("result") or {}).get("list") or []
        return _filter_articles(items, complex_no, area_min, area_max, household_count)
    except Exception:
        return []


# ── 기능 1: 매물 호가 수집 ────────────────────────────────────────────────────

async def fetch_listings(area_name: str, area_type: int, limit: int | None = None) -> list[dict]:
    """지역 + 면적 기준 현재 매물 호가 수집

    Returns:
        [{"complex_name", "area", "floor", "price"(만원), "url", "household_count"}, ...]
    """
    if area_type not in _AREA_RANGES:
        raise ValueError(f"지원하지 않는 area_type: {area_type} (59 또는 84)")
    if area_name not in _AREA_BBOX:
        raise ValueError(f"_AREA_BBOX에 등록되지 않은 지역: {area_name}")

    area_min, area_max = _AREA_RANGES[area_type]
    bbox = _AREA_BBOX[area_name]
    strict = _AREA_STRICT_BBOX.get(area_name, bbox)
    results: list[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context(
            user_agent=_USER_AGENT,
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            extra_http_headers={"Accept-Language": "ko-KR,ko;q=0.9"},
        )
        await ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )

        try:
            page = await ctx.new_page()
            await page.route(
                "**/*.{png,jpg,jpeg,gif,svg,ico,woff,woff2,ttf,otf}",
                lambda r: r.abort(),
            )

            await page.goto(f"{_BASE}/", wait_until="load", timeout=30000)
            await asyncio.sleep(0.5)

            all_clusters = await _get_complex_list(ctx, bbox)

            # 행정구역 외 단지 제거 (클러스터 좌표 기반)
            clusters = [
                c for c in all_clusters
                if c["lon"] and c["lat"]
                and strict["left"] <= c["lon"] <= strict["right"]
                and strict["bottom"] <= c["lat"] <= strict["top"]
            ]

            if limit is not None:
                clusters = clusters[:limit]

            print(f"  {area_name}: 전체 {len(all_clusters)}개 → 좌표 필터 후 {len(clusters)}개")
            if not clusters:
                return []

            for cluster in clusters:
                try:
                    articles = await _visit_and_capture(
                        page,
                        cluster["complex_no"],
                        area_min,
                        area_max,
                        cluster["household_count"],
                    )
                    results.extend(articles)
                except Exception:
                    pass

        except Exception as e:
            print(f"[오류] {area_name} 크롤링 실패: {e}")
        finally:
            await browser.close()

    # 크롤링 결과에서 세대수 DB 저장
    name_count: dict[str, int] = {}
    for r in results:
        name = r.get("complex_name", "")
        count = r.get("household_count", 0)
        if name and count > 0:
            name_count[name] = count
    if name_count:
        upsert_household_counts(name_count)

    return results


# ── 기능 2: 단지명 정제 ───────────────────────────────────────────────────────

def normalize_name(name: str) -> str:
    """괄호·특수문자 제거, 공백 정규화"""
    name = re.sub(r"\([^)]*\)|\[[^\]]*\]", "", name)
    name = re.sub(r"[^\w\s]", "", name)
    return re.sub(r"\s+", " ", name).strip()


# ── 기능 3: DB 단지명 매칭 ────────────────────────────────────────────────────

def match_complex(
    crawled_name: str, db_names: list[str]
) -> tuple[str, int] | tuple[None, None]:
    """thefuzz 유사도 매칭: 점수 80% 이상 + 이름 길이 비율 40% 이상만 반환"""
    if not db_names:
        return None, None

    norm = normalize_name(crawled_name)
    norm_map = {normalize_name(d): d for d in db_names}
    result = fuzz_process.extractOne(norm, list(norm_map.keys()))
    if not result or result[1] < _MATCH_THRESHOLD:
        return None, None

    matched_norm, score = result[0], result[1]

    # 짧은 이름이 긴 이름에 부분 일치하는 오류 방지 (예: '중앙' → '신검단중앙역풍경채어바니티')
    len_ratio = min(len(norm), len(matched_norm)) / max(len(norm), len(matched_norm), 1)
    if len_ratio < _MATCH_LEN_RATIO:
        return None, None

    return norm_map[matched_norm], score
