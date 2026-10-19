"""평가 세트(eval_set.json)로 챗봇 성능을 잰다.

실행:  C:\\Users\\2003g\\anaconda3\\envs\\DS\\python.exe eval\\run_eval.py <이름> [--full]
  <이름>  결과 파일 이름(예: baseline, v4). eval/results/<이름>.json 으로 저장
  --full  RAG 대신 문서 전체를 Gemini에 넣는 방식(노트북과 비슷한 기준선)
  --ids=Q11,Q12  일부 질문만 실행

지표
- 검색 재현율: 정답 쪽 중 검색된(답변 자료로 넘어간) 쪽 비율
- 인용 재현율: 정답 쪽 중 답변이 실제로 인용한 쪽 비율
- 핵심 내용 포함률: 정답 핵심어 묶음 중 답변에 하나라도 들어간 묶음 비율
- 유형 정확도: 질문 유형 분류가 expected_type과 같은 비율
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

import rag  # noqa: E402


def score(item, res):
    gold = {tuple(g) for g in item["gold"]}
    got = {(h["doc"], h["page"]) for h in res["sources"]}
    cited = {tuple(c) for c in res["cited"]}
    text = res["answer"]
    keys_hit = [any(k in text for k in group) for group in item["keys"]]
    return {
        "retrieval_recall": round(len(gold & got) / len(gold), 3) if gold else None,
        "citation_recall": round(len(gold & cited) / len(gold), 3) if gold else None,
        "key_coverage": round(sum(keys_hit) / len(keys_hit), 3),
        "missed_keys": [g[0] for g, hit in zip(item["keys"], keys_hit) if not hit],
        "missed_gold": sorted(rag.cite_label(*g) for g in gold - cited),
        "qtype": res.get("qtype"),
        "type_ok": (res.get("qtype") == item["expected_type"]) if item.get("expected_type") and res.get("qtype") else None,
    }


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "run"
    full = "--full" in sys.argv
    items = json.loads((ROOT / "eval" / "eval_set.json").read_text(encoding="utf-8"))
    ids = next((x.split("=", 1)[1].split(",") for x in sys.argv if x.startswith("--ids=")), None)
    if ids:  # 예: --ids=Q11,Q12
        items = [it for it in items if it["id"] in ids]
    index = rag.Index()
    out = []
    for it in items:
        t = time.time()
        res = rag.answer_full(it["question"]) if full else rag.answer(index, it["question"])
        s = score(it, res)
        s.update({"id": it["id"], "type": it["type"], "seconds": round(time.time() - t, 1),
                  "cited": [rag.cite_label(*c) for c in res["cited"]], "answer": res["answer"]})
        out.append(s)
        print(f"{it['id']:4} 유형 {s['qtype']}({'O' if s['type_ok'] else 'X'})  검색 {s['retrieval_recall']}  인용 {s['citation_recall']}  핵심 {s['key_coverage']}"
              f"  놓친 핵심 {s['missed_keys']}  놓친 쪽 {s['missed_gold']}  ({s['seconds']}s)", flush=True)

    def avg(key):
        vals = [o[key] for o in out if o[key] is not None]
        return round(sum(vals) / len(vals), 3)

    summary = {"name": name, "mode": "full" if full else "rag", "model": rag.LLM_MODEL,
               "retrieval_recall": avg("retrieval_recall"), "citation_recall": avg("citation_recall"),
               "key_coverage": avg("key_coverage"), "avg_seconds": avg("seconds"),
               "type_accuracy": round(sum(o["type_ok"] for o in out if o["type_ok"] is not None)
                                      / max(1, len([o for o in out if o["type_ok"] is not None])), 3)}
    print("평균", summary)
    (ROOT / "eval" / "results").mkdir(exist_ok=True)
    (ROOT / "eval" / "results" / f"{name}.json").write_text(
        json.dumps({"summary": summary, "items": out}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
