"""
APT Radar — Flask 웹 서버

실행:
  python app.py

의존성 없으면:
  pip install flask
"""

import asyncio
import os
import sqlite3
import sys
import threading
import uuid
import webbrowser
from datetime import datetime

try:
    from flask import Flask, jsonify, render_template, request, send_file
except ImportError:
    sys.exit("Flask가 설치되어 있지 않습니다.\n  pip install flask")

from dotenv import load_dotenv

from config import TARGET_AREAS, PEAK_START_YEAR
from crawler.naver import fetch_listings, match_complex as naver_match
from data.molit import get_trade_history, get_peak, get_latest_trade
from engine.calculator import calc_price_per_pyeong, calc_recovery_rate, classify_cycle
from engine.filter import apply_filter, sort_by_rank
from output.excel import export_to_excel
from output.telegram import send_file as tg_send_file
from output.telegram import send_report as tg_send_report

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")

app = Flask(__name__)

_jobs: dict[str, dict] = {}
_latest_xlsx: str | None = None


# ── 파이프라인 ────────────────────────────────────────────────────────────────

async def _collect_listings(areas: list[str], area_type: int) -> dict[str, list[dict]]:
    """지역별 Naver 매물 수집 (순차 실행으로 rate limit 방지)"""
    result: dict[str, list[dict]] = {}
    for area_name in areas:
        try:
            result[area_name] = await fetch_listings(area_name, area_type)
        except ValueError:
            result[area_name] = []
    return result


async def _build_apt_list(
    areas: list[str],
    area_type: int,
    peak_start_year: int,
    price_min: int | None,
    price_max: int | None,
) -> list[dict]:
    listings_by_area = await _collect_listings(areas, area_type)
    apt_list: list[dict] = []

    for area_name, listings in listings_by_area.items():
        complexes: dict[str, list[dict]] = {}
        for item in listings:
            complexes.setdefault(item["complex_name"], []).append(item)

        for cname, items in complexes.items():
            best = min(items, key=lambda x: x["price"])
            ask = best["price"]

            if price_min is not None and ask < price_min:
                continue
            if price_max is not None and ask > price_max:
                continue

            history = get_trade_history(cname, area_type)
            if not history:
                try:
                    conn = sqlite3.connect(os.path.join("db", "apt_radar.db"))
                    db_names = [r[0] for r in conn.execute(
                        "SELECT DISTINCT complex_name FROM trades"
                    ).fetchall()]
                    conn.close()
                    matched, _ = naver_match(cname, db_names)
                    if matched:
                        history = get_trade_history(matched, area_type)
                except Exception:
                    pass

            peak = get_peak(history, peak_start_year)
            latest = get_latest_trade(history)
            peak_price = peak["price"] if peak else 0
            latest_price = latest["price"] if latest else 0
            area_m2 = best["area"]

            ask_rate = calc_recovery_rate(ask, peak_price) if peak_price else 0.0
            trade_rate = calc_recovery_rate(latest_price, peak_price) if peak_price else 0.0

            apt_list.append({
                "rank": 0,
                "complex_name": cname,
                "area_name": area_name,
                "area": area_m2,
                "ask_price": ask,
                "ask_rate": ask_rate,
                "latest_trade_price": latest_price,
                "latest_trade_date": latest["date"] if latest else "",
                "trade_rate": trade_rate,
                "peak_price": peak_price,
                "peak_date": peak["date"] if peak else "",
                "trade_count": len(history),
                "price_per_pyeong": calc_price_per_pyeong(ask, area_m2),
                "cycle": classify_cycle(ask_rate),
                "url": best["url"],
            })

    return apt_list


def _pipeline_thread(job_id: str, params: dict) -> None:
    global _latest_xlsx
    try:
        areas = params.get("areas") or list(TARGET_AREAS)
        area_type = int(params.get("area_type") or 84)
        filt = params.get("filters") or {}

        peak_start_year = int(filt.get("peak_start_year") or PEAK_START_YEAR)
        price_min = int(filt["ask_price_min"]) if filt.get("ask_price_min") else None
        price_max = int(filt["ask_price_max"]) if filt.get("ask_price_max") else None
        ask_rate_max = float(filt["ask_rate_max"]) if filt.get("ask_rate_max") is not None else None
        trade_rate_max = float(filt["trade_rate_max"]) if filt.get("trade_rate_max") is not None else None

        apt_list = asyncio.run(_build_apt_list(
            areas, area_type, peak_start_year, price_min, price_max
        ))

        # 웹 테이블: 활성화된 회복률 필터만 적용 (MIN_TRADE_COUNT 미적용)
        web_result = apt_list[:]
        if ask_rate_max is not None:
            web_result = [a for a in web_result if a["ask_rate"] <= ask_rate_max]
        if trade_rate_max is not None:
            web_result = [a for a in web_result if a["trade_rate"] <= trade_rate_max]
        ranked = sort_by_rank(web_result)

        # Excel Sheet1: 전체, Sheet2: 표준 필터(MIN_TRADE_COUNT 포함)
        excel_all = sort_by_rank(apt_list)
        excel_filtered = sort_by_rank(apply_filter(
            apt_list,
            ask_rate_max=ask_rate_max if ask_rate_max is not None else 100.0,
            trade_rate_max=trade_rate_max if trade_rate_max is not None else 100.0,
        ))

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        xlsx_path = f"apt_radar_{ts}.xlsx"
        export_to_excel(excel_all, excel_filtered, xlsx_path)
        _latest_xlsx = xlsx_path

        # 텔레그램
        tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        tg_chat = os.getenv("TELEGRAM_CHAT_ID", "")
        if tg_token and tg_chat:
            try:
                tg_send_report(tg_token, tg_chat, ranked[:10])
                tg_send_file(tg_token, tg_chat, xlsx_path)
            except Exception:
                pass

        _jobs[job_id] = {"status": "done", "result": ranked}

    except Exception as exc:
        _jobs[job_id] = {"status": "error", "error": str(exc)}


# ── 라우트 ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", areas=list(TARGET_AREAS))


@app.route("/api/search", methods=["POST"])
def api_search():
    job_id = uuid.uuid4().hex[:8]
    _jobs[job_id] = {"status": "running"}
    threading.Thread(
        target=_pipeline_thread,
        args=(job_id, request.json or {}),
        daemon=True,
    ).start()
    return jsonify({"job_id": job_id})


@app.route("/api/status/<job_id>")
def api_status(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    return jsonify(job)


@app.route("/api/download")
def api_download():
    if not _latest_xlsx or not os.path.exists(_latest_xlsx):
        return jsonify({"error": "파일 없음"}), 404
    return send_file(
        os.path.abspath(_latest_xlsx),
        as_attachment=True,
        download_name=os.path.basename(_latest_xlsx),
    )


if __name__ == "__main__":
    threading.Timer(1.5, lambda: webbrowser.open("http://localhost:5000")).start()
    app.run(debug=False, port=5000, use_reloader=False)
