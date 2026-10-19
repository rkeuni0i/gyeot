"""학기별 정기 점검: 저장소의 모든 활성 자료를 다시 평가해 '사용 전 알림'을 만든다.

점검 주기: 182일(한 학기). store.meta의 last_periodic_review 기준.
자동 실행: 앱(app.py)을 열 때 점검 시기가 지났으면 백그라운드로 한 번 실행한다.
           앱을 열지 않아도 돌리려면 run_review.bat을 Windows 작업 스케줄러에 등록한다(README 참고).
수동 실행: C:\\Users\\2003g\\anaconda3\\envs\\DS\\python.exe doc_agent\\periodic_review.py [--force]

자료마다 하는 일
1. 참고자료 타당성 평가 에이전트로 다시 평가(오늘 날짜 기준 최신성, 자기 자신은 중복 비교에서 뺌) → reviews에 기록
2. 직전 평가와 비교해 알림(alerts) 생성
   - critical: 판정이 비추천으로 바뀜(필수 기준 미달 등)
   - warn    : 판정이 낮아짐 / 새로 0점이 된 기준 / 최신성 점수 하락 / 발행 3년 경과(법·절차·학교 규정)
               / 학교폭력 자료인데 등록된 법령 시행일이 자료 발행 연도보다 뒤(개정 조문 확인 필요)
   - info    : 이번 평가의 주의할 점(이미 열려 있는 같은 알림은 다시 만들지 않음)
3. 다음 점검일 기록
"""
import json
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent
ROOT = AGENT_DIR.parent
sys.path.insert(0, str(ROOT))

import rag  # noqa: E402
import store  # noqa: E402
from doc_agent import agent  # noqa: E402

LAW_FILE = ROOT / "data" / "학교폭력예방 및 대책에 관한 법률.jsonl"
LOCK = ROOT / "index" / "review.lock"
RANK = {"추천": 2, "조건부": 1, "비추천": 0}


def interval_days(con):
    return int(store.get_meta(con, "review_interval_days", store.REVIEW_INTERVAL_DAYS))


def status():
    """{'last': 마지막 점검 시각 또는 None, 'next_due': 다음 점검일, 'due': bool, 'running': bool}"""
    con = store.connect()
    store.init_db(con)
    last = store.get_meta(con, "last_periodic_review")
    days = interval_days(con)
    con.close()
    next_due = (datetime.strptime(last, "%Y-%m-%d %H:%M:%S") + timedelta(days=days)) if last else None
    return {"last": last, "next_due": next_due.strftime("%Y-%m-%d") if next_due else "지금",
            "due": next_due is None or datetime.now() >= next_due, "running": LOCK.exists(), "interval_days": days}


def law_effective():
    if not LAW_FILE.exists():
        return None
    first = json.loads(LAW_FILE.read_text(encoding="utf-8").splitlines()[0])
    e = first.get("effective")
    return f"{e[:4]}-{e[4:6]}-{e[6:]}" if e else None


def _open_messages(con, doc_id):
    return {(a["title"], a["message"]) for a in store.open_alerts(con, doc_id)}


def review_document(con, index, doc_id, doc):
    path = ROOT / "data" / doc["file"]
    if not path.exists():
        store.add_alert(con, doc_id, "critical", "PDF 파일 없음", f"data/{doc['file']}을 찾을 수 없어 점검하지 못했습니다.")
        return None
    prev = store.last_review(con, doc_id)
    report = agent.evaluate_pdf(path, index, save=False, exclude_doc=doc_id)
    review_id = store.add_review(con, doc_id, "periodic", report)
    have = _open_messages(con, doc_id)

    def alert(sev, title, msg):
        if (title, msg) not in have:
            store.add_alert(con, doc_id, sev, title, msg, review_id)
            have.add((title, msg))

    v = report["verdict"]
    if v == "비추천":
        alert("critical", "정기 점검: 비추천", " / ".join(report["reasons"]) + " — 사용 전 확인하고, 필요하면 사용 중지하세요.")
    if prev and prev.get("verdict") in RANK and RANK[v] < RANK[prev["verdict"]]:
        alert("warn", f"판정 하락: {prev['verdict']} → {v}", " / ".join(report["reasons"]))
    prev_scores = json.loads(prev["report_json"])["scores"] if prev and prev.get("report_json") else {}
    for cid, s in report["scores"].items():
        before = (prev_scores.get(cid) or {}).get("score")
        if s.get("weight") and s.get("score") == 0 and before not in (0, None):
            alert("warn", f"{s['name']}: 새로 0점", s.get("evidence", ""))
        if cid == "recency" and isinstance(before, int) and isinstance(s.get("score"), int) and s["score"] < before:
            alert("warn", "최신성 점수 하락", s.get("evidence", ""))
    year = doc.get("published_year")
    if year and doc["kind"] in ("guide", "school") and datetime.now().year - year >= 3:
        alert("warn", "발행 3년 경과", f"{year}년 자료입니다. 더 최신 판본이 있는지 확인하세요.")
    eff = law_effective()
    if eff and "학교폭력" in doc.get("topics", []) and year and int(eff[:4]) >= year and doc["kind"] in ("guide", "school"):
        alert("warn", "법령 개정 이후 확인 필요",
              f"학교폭력예방법 시행일({eff})이 자료 발행({year}년) 이후이거나 같은 해입니다. 개정된 조문과 자료 내용이 다른지 확인하세요.")
    for c in report["proposal"].get("cautions", [])[:3]:
        alert("info", "사용 시 유의사항", c)
    next_due = (datetime.now() + timedelta(days=interval_days(con))).strftime("%Y-%m-%d")
    store.upsert_document(con, doc_id, last_reviewed_at=store.now(), next_review_due=next_due)
    con.commit()
    return report


def run(force=False, log=print):
    st = status()
    if not force and not st["due"]:
        log(f"점검 시기가 아닙니다(다음 점검일 {st['next_due']}).")
        return []
    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 3 * 3600:
        log("이미 점검 중입니다.")
        return []
    LOCK.write_text(store.now(), encoding="utf-8")
    results = []
    try:
        index = rag.Index()
        con = store.connect()
        for doc_id, doc in list(rag.DOCS.items()):
            t = time.time()
            try:
                r = review_document(con, index, doc_id, doc)
                results.append((doc_id, r["verdict"] if r else "파일 없음"))
                log(f"[{doc['short']}] {results[-1][1]} ({time.time() - t:.0f}초)")
            except Exception as e:  # noqa: BLE001 — 한 자료가 실패해도 나머지는 점검
                store.add_alert(con, doc_id, "warn", "정기 점검 실패", str(e)[:300])
                con.commit()
                results.append((doc_id, f"실패: {e}"))
                log(f"[{doc['short']}] 실패: {e}")
        store.set_meta(con, "last_periodic_review", store.now())
        con.commit()
        con.close()
    finally:
        LOCK.unlink(missing_ok=True)
    return results


def start_background_if_due():
    """앱에서 호출: 점검 시기가 지났고 실행 중이 아니면 백그라운드 스레드로 시작. 시작했으면 True."""
    st = status()
    if not st["due"] or st["running"]:
        return False
    threading.Thread(target=run, kwargs={"log": lambda *_: None}, daemon=True).start()
    return True


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    run(force="--force" in sys.argv)
