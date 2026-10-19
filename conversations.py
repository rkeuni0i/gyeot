"""이전 대화 저장·불러오기 (logs/conversations/<대화ID>.json).

대화 하나 = 질문·답변 목록(답변에는 근거 표·원문을 다시 그릴 수 있는 검색 결과까지 포함).
사이드바 '이전 대화'에서 제목(첫 질문)으로 보여 주고, 누르면 그대로 다시 연다. 이 컴퓨터에만 저장된다.
"""
import json
import uuid
from datetime import datetime
from pathlib import Path

DIR = Path(__file__).resolve().parent / "logs" / "conversations"


def _plain(o):
    """numpy 숫자 등 JSON이 모르는 값을 바꾼다."""
    if hasattr(o, "item"):
        return o.item()
    if isinstance(o, (set, tuple)):
        return list(o)
    return str(o)


def new_id():
    return datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]


def title_of(messages):
    first = next((m["content"] for m in messages if m["role"] == "user"), "새 대화")
    first = " ".join(first.split())
    return first[:28] + ("…" if len(first) > 28 else "")


def save(conv_id, messages):
    DIR.mkdir(parents=True, exist_ok=True)
    path = DIR / f"{conv_id}.json"
    created = json.loads(path.read_text(encoding="utf-8"))["created_at"] if path.exists() else datetime.now().isoformat(timespec="seconds")
    data = {"id": conv_id, "title": title_of(messages), "created_at": created,
            "updated_at": datetime.now().isoformat(timespec="seconds"), "messages": messages}
    path.write_text(json.dumps(data, ensure_ascii=False, default=_plain), encoding="utf-8")


def list_all(limit=30):
    """최근에 쓴 대화부터 [{id, title, updated_at, n}]."""
    if not DIR.exists():
        return []
    out = []
    for p in DIR.glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append({"id": d["id"], "title": d["title"], "updated_at": d["updated_at"],
                    "n": sum(m["role"] == "user" for m in d["messages"])})
    return sorted(out, key=lambda x: x["updated_at"], reverse=True)[:limit]


def load(conv_id):
    return json.loads((DIR / f"{conv_id}.json").read_text(encoding="utf-8"))["messages"]


def delete(conv_id):
    p = DIR / f"{conv_id}.json"
    if p.exists():
        p.unlink()
