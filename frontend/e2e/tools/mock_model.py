"""Local Responses-API mock for the review walk. Agent picked from the prompt head."""

import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.agents.reader import RUBRIC_ITEMS
from app.course.tree import BLOCK_TYPES

LOG = open(sys.argv[2] if len(sys.argv) > 2 else "tmp/mock-model.log", "a")
HELD_BEFORE: dict[str, int] = {}
CALLS = [0]
NB = "Dette er en norsk tekst også for ansatte, fordi det gjelder deres arbeid"


def fill(s):
    t = s.get("type")
    if "enum" in s:
        return s["enum"][0]
    if "anyOf" in s:
        return fill(s["anyOf"][0])
    if t == "object" or "properties" in s:
        return {k: fill(v) for k, v in s.get("properties", {}).items()}
    if t == "array":
        return [fill(s.get("items", {}))]
    if t == "integer":
        return 1
    if t == "number":
        return 0.5
    if t == "boolean":
        return True
    if isinstance(t, list):
        return fill({**s, "type": t[0]})
    return "x"


def section(prompt, name):
    m = prompt.index(f"## {name}\n") + len(name) + 4
    rest = prompt[m:]
    end = rest.find("\n\n## ")
    return json.loads(rest[:end] if end != -1 else rest)


def last_json(prompt):
    i = prompt.rfind("\n{")
    return json.loads(prompt[i + 1 :])


def carried(text):
    """What a translation must keep verbatim: numbers, [P1] markers and gaps."""
    return re.findall(r"\[P\d+\]|___|\d+(?:[.,]\d+)?", text)


def reply_for(prompt, schema):
    CALLS[0] += 1
    head = prompt[:30]
    base = fill(schema)
    if head.startswith("# Researcher"):
        handles = sorted(
            set(re.findall(r'"handle": ?"(P[0-9]+)"', prompt)), key=lambda h: int(h[1:])
        )
        base.update(
            {
                "ranked": [
                    {"handle": h, "rank": i + 1, "score": 0.9} for i, h in enumerate(handles)
                ],
                "web_candidates": [],
            }
        )
        for k in list(base):
            if isinstance(base[k], list) and k not in ("ranked", "web_candidates"):
                base[k] = []
        return base
    if head.startswith("# Architect"):
        d = last_json(prompt)
        handle = (re.findall(r'"handle": ?"(P[0-9]+)"', prompt) or ["P1"])[0]
        roles = d["prescription"]["lesson"]
        allowed = list(BLOCK_TYPES["lesson"])

        def btype(role):
            pref = {
                "teach": "reading",
                "example": "reading",
                "check": "choice",
                "practice": "spot_the_error",
                "test": "choice",
            }[role]
            return pref if pref in allowed else allowed[0]

        def topic(tid, title):
            return {
                "plan_node_id": tid,
                "kind": "lesson",
                "title": title,
                "objectives": [f"Explain {title.lower()}"],
                "through_line_note": "Builds on the case.",
                "blocks": [
                    {
                        "plan_node_id": f"{tid}-b{i}",
                        "role": r,
                        "block_type": btype(r),
                        "brief": (
                            f"Why does {title.lower()} matter?"
                            if r in ("check", "practice", "test")
                            else f"What {title.lower()} means"
                        ),
                        "source_refs": [{"handle": handle}],
                    }
                    for i, r in enumerate(roles)
                ],
            }

        mods = [
            {
                "plan_node_id": "m1",
                "title": "Hand hygiene basics",
                "purpose": "Learn the basics.",
                "entry_knowledge": "None.",
                "exit_knowledge": "The basics.",
                "topics": [topic("t1", "When to wash"), topic("t2", "How long to rub")],
            }
        ]
        n = sum(len(t["blocks"]) for t in mods[0]["topics"])
        return {
            "modules": mods,
            "estimate_inputs": {"blocks": n, "calls": n * 2, "tokens": 1000},
            "through_line": {"case": "A ward nurse", "handoffs": []},
        }
    if head.startswith("# Saboteur"):
        task = json.loads(prompt.split("## Task input (typed, assembled by code)\n", 1)[1])
        mut = str(task["sentence"]).replace(" is ", " is not ", 1)
        if mut == task["sentence"]:
            mut = "It is not the case that " + task["sentence"]
        return {"sentence": task["sentence"], "kind": task["kind"], "mutated": mut}
    if head.startswith("# Reader"):
        blocks = section(prompt, "Block skeleton")["blocks"]
        out = []
        for b in blocks:
            node = str(b["plan_node_id"])
            hold = node.endswith("-b0") and HELD_BEFORE.get(node, 0) < 2
            HELD_BEFORE[node] = HELD_BEFORE.get(node, 0) + 1
            v = "hold" if hold else "pass"
            LOG.write(json.dumps({"reader_node": node, "verdict": v}) + "\n")
            out.append(
                {
                    "plan_node_id": b["plan_node_id"],
                    "verdict": v,
                    "items": {
                        it: {"verdict": "pass" if not hold else "hold", "evidence": f"quoted {it}"}
                        for it in RUBRIC_ITEMS
                    },
                }
            )
        return {"verdicts": out}
    if head.startswith("# Continuity"):
        return {"handoffs": [], "issues": []}
    if head.startswith("# Translator"):
        segs = None
        for name in ("Segments",):
            try:
                segs = section(prompt, name)
            except Exception:
                pass
        if segs is None:
            segs = json.loads(re.findall(r"\[\{\"id\".*\}\]", prompt)[-1])
        return {
            **base,
            "segments": [
                {"id": s["id"], "text": " ".join([NB, *carried(s["text"])])}
                for s in segs
            ],
        }
    try:
        first = section(prompt, "Approved sources")["sources"][0]
        quote = str(first["text"])[:40]
        h = str(first.get("handle", "P1"))
    except Exception:
        quote, h = "x", "P1"
    try:
        block_type = str(section(prompt, "Block skeleton").get("block_type") or "reading")
    except Exception:
        block_type = "reading"
    return {
        "block_type": block_type,
        "text": f"Staff apply the cited passage at work (take {CALLS[0]}).",
        "citations": [{"passage_id": h, "quote": quote}],
        "answer": "key",
        "options": [{"text": "Yes", "correct": True}, {"text": "No", "correct": False}],
        "pairs": [{"left": "a", "right": "b"}],
        "blanks": ["x"],
        "items": [{"text": "i", "passage_id": h, "tier": "high"}],
        "sentences": ["Alpha is apply.", "Beta is apply."],
    }


def completed(text):
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 1761878400,
        "status": "completed",
        "model": "muse-spark-1.3",
        "output": [
            {
                "type": "message",
                "id": "msg_1",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
        "usage": {
            "input_tokens": 100,
            "output_tokens": 10,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 110,
        },
    }


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        prompt = str(body.get("input", ""))
        schema = body.get("text", {}).get("format", {}).get("schema", {})
        try:
            r = reply_for(prompt, schema)
            code = 200
            payload = completed(json.dumps(r))
        except Exception as e:
            code = 500
            payload = {"error": {"message": repr(e)}}
        LOG.write(
            json.dumps({"head": prompt[:25], "code": code, "err": payload.get("error")}) + "\n"
        )
        LOG.flush()
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
