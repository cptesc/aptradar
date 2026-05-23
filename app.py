"""
APT Radar — Flask 웹 서버
"""

import os
import sys
import threading
import uuid
import webbrowser
from datetime import datetime
from urllib.parse import quote as urlquote

try:
    from flask import Flask, jsonify, render_template, request, send_file
except ImportError:
    sys.exit("Flask가 설치되어 있지 않습니다.\n  pip install flask")

from dotenv import load_dotenv

from config import TARGET_AREAS, PEAK_START_YEAR, AREA_CODE_MAP, AREA_GROUPS
from data.molit import get_trade_history, get_peak, get_latest_trade, get_complexes_by_area, fetch_all_trades, get_household_count
from data.kapt import sync_household_counts
from engine.calculator import calc_price_per_pyeong, calc_recovery_rate, classify_cycle
from engine.filter import apply_filter, sort_by_rank
from output.excel import export_to_excel
from output.telegram import send_file as tg_send_file
from output.telegram import send_report as tg_send_report

load_dotenv()
if sys.stdout:
    sys.stdout.reconfigure(encoding="utf-8")

app = Flask(__name__)

_jobs: dict[str, dict] = {}
_latest_data: dict = {}  # stores apt_list + trade_rate_max for on-demand Excel


# ── 파이프라인 ────────────────────────────────────────────────────────────────

def _build_apt_list(
    areas: list[str],
    area_type: int,
    peak_end_year: int,
) -> list[dict]:
    apt_list: list[dict] = []

    for area_name in areas:
        lawd_cds = AREA_CODE_MAP.get(area_name, [])
        if not lawd_cds:
            continue

        complex_names = get_complexes_by_area(area_type, lawd_cds)

        for cname in complex_names:
            history = get_trade_history(cname, area_type, lawd_cds=lawd_cds)
            if not history:
                continue

            peak = get_peak(history, peak_end_year)
            latest = get_latest_trade(history)

            peak_price = peak["price"] if peak else 0
            latest_price = latest["price"] if latest else 0
            area_m2 = history[-1]["area"]

            trade_rate = calc_recovery_rate(latest_price, peak_price) if peak_price else 0.0

            apt_list.append({
                "rank": 0,
                "complex_name": cname,
                "area_name": area_name,
                "area": area_m2,
                "latest_trade_price": latest_price,
                "latest_trade_date": latest["date"] if latest else "",
                "trade_rate": trade_rate,
                "peak_price": peak_price,
                "peak_date": peak["date"] if peak else "",
                "trade_count": len(history),
                "household_count": get_household_count(cname),
                "price_per_pyeong": calc_price_per_pyeong(latest_price, area_m2) if latest_price else 0,
                "cycle": classify_cycle(trade_rate),
                "url": f"https://fin.land.naver.com/map?searchKeyword={urlquote(cname)}",
            })

    return apt_list


def _pipeline_thread(job_id: str, params: dict) -> None:
    global _latest_data
    try:
        areas = params.get("areas") or list(TARGET_AREAS)
        area_type = int(params.get("area_type") or 84)
        filt = params.get("filters") or {}

        peak_end_year = int(filt.get("peak_end_year") or datetime.now().year)
        trade_rate_max = float(filt["trade_rate_max"]) if filt.get("trade_rate_max") is not None else None
        household_count_min = int(filt.get("household_count_min") or 0)
        latest_price_min = int(filt["latest_price_min"]) if filt.get("latest_price_min") is not None else None
        latest_price_max = int(filt["latest_price_max"]) if filt.get("latest_price_max") is not None else None

        # DB에 없는 지역·기간 자동 수집 (이미 수집된 건 스킵)
        start_ym = f"{PEAK_START_YEAR}01"
        end_ym = datetime.now().strftime("%Y%m")
        try:
            fetch_all_trades(areas, start_ym, end_ym)
        except Exception:
            pass  # 수집 실패해도 기존 DB 데이터로 분석 계속

        # K-apt에서 세대수 동기화 (DB에 없는 단지만 실질적으로 업데이트)
        for area_name in areas:
            try:
                sync_household_counts(area_name, area_type)
            except Exception:
                pass

        apt_list = _build_apt_list(areas, area_type, peak_end_year)

        # 웹 테이블: 활성화된 필터만 적용 (MIN_TRADE_COUNT 미적용)
        web_result = apt_list[:]
        if trade_rate_max is not None:
            web_result = [a for a in web_result if a["trade_rate"] <= trade_rate_max]
        if household_count_min > 0:
            web_result = [a for a in web_result if a["household_count"] == 0 or a["household_count"] >= household_count_min]
        if latest_price_min is not None:
            web_result = [a for a in web_result if a["latest_trade_price"] >= latest_price_min]
        if latest_price_max is not None:
            web_result = [a for a in web_result if a["latest_trade_price"] <= latest_price_max]
        ranked = sort_by_rank(web_result)

        _latest_data = {"apt_list": apt_list, "trade_rate_max": trade_rate_max}

        tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        tg_chat = os.getenv("TELEGRAM_CHAT_ID", "")
        if tg_token and tg_chat:
            try:
                tg_send_report(tg_token, tg_chat, ranked[:10])
            except Exception:
                pass

        _jobs[job_id] = {"status": "done", "result": ranked}

    except Exception as exc:
        _jobs[job_id] = {"status": "error", "error": str(exc)}


# ── 라우트 ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template(
        "index.html",
        area_groups=AREA_GROUPS,
        default_areas=list(TARGET_AREAS),
    )


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


@app.route("/api/send_telegram_message", methods=["POST"])
def api_send_telegram_message():
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    tg_chat = os.getenv("TELEGRAM_CHAT_ID", "")
    if not tg_token or not tg_chat:
        return jsonify({"error": "텔레그램 설정 없음 (.env에 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 필요)"}), 400
    if not _latest_data:
        return jsonify({"error": "분석 결과 없음"}), 400

    ranked = sort_by_rank(_latest_data["apt_list"])
    try:
        tg_send_report(tg_token, tg_chat, ranked[:10])
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify({"ok": True})


@app.route("/api/send_telegram", methods=["POST"])
def api_send_telegram():
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    tg_chat = os.getenv("TELEGRAM_CHAT_ID", "")
    if not tg_token or not tg_chat:
        return jsonify({"error": "텔레그램 설정 없음 (.env에 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 필요)"}), 400
    if not _latest_data:
        return jsonify({"error": "분석 결과 없음"}), 400

    apt_list = _latest_data["apt_list"]
    trade_rate_max = _latest_data["trade_rate_max"]
    ranked = sort_by_rank(apt_list)

    excel_all = sort_by_rank(apt_list)
    excel_filtered = sort_by_rank(apply_filter(
        apt_list,
        trade_rate_max=trade_rate_max if trade_rate_max is not None else 100.0,
    ))
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    xlsx_path = f"apt_radar_{ts}.xlsx"
    export_to_excel(excel_all, excel_filtered, xlsx_path)

    try:
        tg_send_report(tg_token, tg_chat, ranked[:10])
        tg_send_file(tg_token, tg_chat, xlsx_path)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify({"ok": True})


@app.route("/api/download")
def api_download():
    if not _latest_data:
        return jsonify({"error": "파일 없음"}), 404
    apt_list = _latest_data["apt_list"]
    trade_rate_max = _latest_data["trade_rate_max"]
    excel_all = sort_by_rank(apt_list)
    excel_filtered = sort_by_rank(apply_filter(
        apt_list,
        trade_rate_max=trade_rate_max if trade_rate_max is not None else 100.0,
    ))
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    xlsx_path = f"apt_radar_{ts}.xlsx"
    export_to_excel(excel_all, excel_filtered, xlsx_path)
    return send_file(
        os.path.abspath(xlsx_path),
        as_attachment=True,
        download_name=os.path.basename(xlsx_path),
    )


_HOST = "127.0.0.1"
_PORT = 5000

if __name__ == "__main__":
    threading.Timer(1.5, lambda: webbrowser.open(f"http://{_HOST}:{_PORT}")).start()
    app.run(host=_HOST, port=_PORT, threaded=True, use_reloader=False, debug=False)
