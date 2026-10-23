import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
log_path = Path(r"C:\Users\2003g\.gemini\antigravity-ide\brain\4ec3fa1b-9871-4b27-ae32-e7f5b08ffedc\.system_generated\tasks\task-96.log")

log_lines = log_path.read_text(encoding="utf-8").splitlines()[:16]

v14_items = []
pattern = re.compile(r"^(Q\d+)\s+유형\s+(\w+)\(([OX])\)\s+검색\s+([\d.]+)\s+인용\s+([\d.]+)\s+핵심\s+([\d.]+)\s+근거 약함\s+(\d+)\s+놓친 핵심\s+(\[.*?\])\s+놓친 쪽\s+(\[.*?\])\s+\(([\d.]+)s\)")

for line in log_lines:
    m = pattern.search(line.strip())
    if m:
        qid, qtype, tok, ret, cit, key, weak, missed_k, missed_g, sec = m.groups()
        v14_items.append({
            "id": qid,
            "qtype": qtype,
            "type_ok": (tok == "O"),
            "retrieval_recall": float(ret),
            "citation_recall": float(cit),
            "key_coverage": float(key),
            "weak_cites": int(weak),
            "missed_keys": eval(missed_k),
            "missed_gold": eval(missed_g),
            "seconds": float(sec)
        })

v13_data = json.loads((ROOT / "eval" / "results" / "v13_case2.json").read_text(encoding="utf-8"))
v13_items = {it["id"]: it for it in v13_data["items"]}

print(f"{'ID':<5} | {'검색(v13 => v14)':<20} | {'인용(v13 => v14)':<20} | {'핵심(v13 => v14)':<20} | {'시간(v13 => v14)':<20} | {'약함'}")
print("-" * 105)
for it in v14_items:
    qid = it["id"]
    v13 = v13_items.get(qid, {})
    r13, r14 = v13.get("retrieval_recall"), it["retrieval_recall"]
    c13, c14 = v13.get("citation_recall"), it["citation_recall"]
    k13, k14 = v13.get("key_coverage"), it["key_coverage"]
    s13, s14 = v13.get("seconds"), it["seconds"]
    w14 = it["weak_cites"]
    print(f"{qid:<5} | {str(r13):<7} => {str(r14):<8} | {str(c13):<7} => {str(c14):<8} | {str(k13):<7} => {str(k14):<8} | {str(s13)+'s':<7} => {str(s14)+'s':<8} | {w14}")

def avg(lst, key):
    vals = [x[key] for x in lst if x.get(key) is not None]
    return round(sum(vals) / len(vals), 3)

v14_summary = {
    "n": len(v14_items),
    "retrieval_recall": avg(v14_items, "retrieval_recall"),
    "citation_recall": avg(v14_items, "citation_recall"),
    "key_coverage": avg(v14_items, "key_coverage"),
    "avg_seconds": avg(v14_items, "seconds"),
    "weak_cites_total": sum(x["weak_cites"] for x in v14_items),
    "type_accuracy": round(sum(x["type_ok"] for x in v14_items) / len(v14_items), 3),
}

# 13문항(Q8~Q10 학교 규정 제외) 비교
v13_ex_school = [v13_items[f"Q{i}"] for i in range(1, 17) if i not in (8, 9, 10)]
v14_ex_school = [it for it in v14_items if it["id"] not in ("Q8", "Q9", "Q10")]

v13_13_summary = {
    "retrieval_recall": avg(v13_ex_school, "retrieval_recall"),
    "citation_recall": avg(v13_ex_school, "citation_recall"),
    "key_coverage": avg(v13_ex_school, "key_coverage"),
    "avg_seconds": avg(v13_ex_school, "seconds"),
}
v14_13_summary = {
    "retrieval_recall": avg(v14_ex_school, "retrieval_recall"),
    "citation_recall": avg(v14_ex_school, "citation_recall"),
    "key_coverage": avg(v14_ex_school, "key_coverage"),
    "avg_seconds": avg(v14_ex_school, "seconds"),
}

# Q8~Q10 학교 규정만 비교
v13_school = [v13_items[f"Q{i}"] for i in (8, 9, 10)]
v14_school = [it for it in v14_items if it["id"] in ("Q8", "Q9", "Q10")]
v13_sch_summary = {
    "retrieval_recall": avg(v13_school, "retrieval_recall"),
    "citation_recall": avg(v13_school, "citation_recall"),
    "key_coverage": avg(v13_school, "key_coverage"),
}
v14_sch_summary = {
    "retrieval_recall": avg(v14_school, "retrieval_recall"),
    "citation_recall": avg(v14_school, "citation_recall"),
    "key_coverage": avg(v14_school, "key_coverage"),
}

print("=" * 105)
print("v13 전체 16문항 요약:", v13_data["summary"])
print("v14 전체 16문항 요약:", v14_summary)
print("-" * 105)
print("v13 학교규정 제외 13문항 요약:", v13_13_summary)
print("v14 학교규정 제외 13문항 요약:", v14_13_summary)
print("-" * 105)
print("v13 학교규정 Q8~Q10 3문항 요약:", v13_sch_summary)
print("v14 학교규정 Q8~Q10 3문항 요약:", v14_sch_summary)
