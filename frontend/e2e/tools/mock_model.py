"""Local Responses-API mock for the review walk. Agent picked from the instructions head."""

import json
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.agents.reader import RUBRIC_ITEMS
from app.course.tree import BLOCK_TYPES

LOG = open(sys.argv[2] if len(sys.argv) > 2 else "tmp/mock-model.log", "a")
HELD_BEFORE: dict[str, int] = {}
CALLS = [0]
NB = "Dette er en norsk tekst også for ansatte, fordi det gjelder deres arbeid"


def spelled(count):
    return "".join(chr(ord("a") + int(digit)) for digit in str(count))


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


def reply_for(prompt, schema, instructions=""):
    CALLS[0] += 1
    head = (instructions or prompt)[:30]
    if head.startswith(("# Block", "# Teach writer", "# Example writer", "# Exercise writer")):
        # a real block takes seconds; without this pause a 10-step run ends
        # before the flow spec can observe it running and cancel it
        time.sleep(0.8)
    LOG.write(json.dumps({"call": CALLS[0], "head": head}) + "\n")
    LOG.flush()
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
                "gaps": [],
            }
        )
        for k in list(base):
            if isinstance(base[k], list) and k not in ("ranked", "gaps"):
                base[k] = []
        return base
    if head.startswith("# Blueprint"):
        topics = [
            {
                "plan_node_id": f"m1-t{i}",
                "title": title,
                "annotation": f"{title}: the key points the ward routine names.",
                "artefact_step": "The routine card gains one line.",
                "introduces_terms": [title.split()[0].lower()],
                "requires": [] if i == 1 else ["m1-t1"],
            }
            for i, title in ((1, "When to wash"), (2, "How long to rub"))
        ]
        return {
            "learner": "A ward nurse on the first shift.",
            "case": "A routine card the nurse fills in topic by topic.",
            "modules": [
                {
                    "plan_node_id": "m1",
                    "title": "Hand hygiene basics",
                    "purpose": "Learn the basics.",
                    "entry_knowledge": "None.",
                    "exit_knowledge": "The basics.",
                    "topics": topics,
                }
            ],
        }
    if head.startswith("# Lecture"):
        own = last_json(prompt).get("own", {})
        node = str(own.get("plan_node_id", "m1-t1"))
        title = str(own.get("title", "Topic"))
        return {
            "plan_node_id": node,
            "paragraphs": [
                f"{title} is the first thing a nurse learns on the ward.",
                "Hands are cleaned before touching a patient and after touching surroundings.",
                "```python\nseconds = 20\nprint(seconds)\n```",
                "Alcohol rub is used when hands are not visibly soiled.",
                "The routine card gains one line after this topic.",
            ],
            "artefact_state": "The routine card holds one line.",
        }
    if head.startswith("# Corrector"):
        return {"accepted": True, "notes": "The draft stands.", "corrected": None}
    if head.startswith("# Outline"):
        ids = re.findall(r'"id": ?"(S[0-9]+)"', prompt) or ["S1"]
        half = max(1, len(ids) // 2)
        first, second = ids[:half], ids[half:] or ids[:1]
        if not ids[half:]:
            first, second = ids, []

        def scen(tid, title, sections, requires):
            return {
                "plan_node_id": tid,
                "title": title,
                "annotation": f"{title}: what the nurse does and why.",
                "introduces_terms": [title.split()[0].lower()],
                "prepares_for": "The next step of the ward routine.",
                "requires": requires,
                "sections": sections,
            }

        topics = [scen("t1", "When to wash", first, [])]
        if second:
            topics.append(scen("t2", "How long to rub", second, ["t1"]))
        return {
            "learner": "A ward nurse on the first shift.",
            "case": "A ward nurse who washes at every moment the routine names.",
            "modules": [
                {
                    "plan_node_id": "m1",
                    "title": "Hand hygiene basics",
                    "purpose": "Learn the basics.",
                    "entry_knowledge": "None.",
                    "exit_knowledge": "The basics.",
                    "sections": ids,
                    "topics": topics,
                }
            ],
            "uncovered": [],
            "questions": [],
        }
    if head.startswith("# Architect"):
        d = last_json(prompt)
        # every block cites every passage: the plan covers 100 % of the content
        # passages (T11 R5 rejects a plan under 60 % with nothing in `uncovered`)
        all_handles = sorted(
            set(re.findall(r'"handle": ?"(P[0-9]+)"', prompt)), key=lambda h: int(h[1:])
        ) or ["P1"]
        scope = d.get("module_scope", {})
        scope_topics = scope.get("topics") or [{"plan_node_id": "t1", "title": "When to wash"}]
        roles = d["prescription"]["lesson"]
        test_roles = d["prescription"].get("module_test", ["test"])
        allowed = list(BLOCK_TYPES["lesson"])
        allowed_test = list(BLOCK_TYPES["module_test"])

        def btype(role, pool):
            pref = {"teach": "reading", "example": "reading"}.get(role, "choice")
            return pref if pref in pool else pool[0]

        def spec(role):
            if role != "example":
                return {"input": "", "action": "", "result": ""}
            return {
                "input": "The nurse enters the ward with unwashed hands.",
                "action": "She washes at the moment the routine names.",
                "result": "Her hands are clean before the first patient.",
            }

        def topic(row):
            tid, title = str(row["plan_node_id"]), str(row["title"])
            handles = list(row.get("handles") or all_handles)
            return {
                "plan_node_id": tid,
                "kind": "lesson",
                "title": title,
                "objectives": [f"Explain {title.lower()}"],
                "through_line_note": "Builds on the case.",
                "misconception": "Nurses think a quick rinse is enough.",
                "blocks": [
                    {
                        "plan_node_id": f"{tid}-b{i}",
                        "role": r,
                        "block_type": btype(r, allowed),
                        "brief": (
                            f"Why does {title.lower()} matter?"
                            if r in ("check", "practice", "test")
                            else f"What {title.lower()} means"
                        ),
                        "source_refs": [{"handle": h} for h in handles],
                        "example_spec": spec(r),
                    }
                    for i, r in enumerate(roles)
                ],
            }

        module_id = str(scope.get("plan_node_id", "m1"))
        test_types = ["choice", "matching", "spot_the_error"]
        test_topic = {
            "plan_node_id": f"{module_id}-test",
            "kind": "module_test",
            "title": "Module test",
            "objectives": ["Apply the routine"],
            "through_line_note": "Closes the module.",
            "misconception": "Nurses think a quick rinse is enough.",
            "blocks": [
                {
                    "plan_node_id": f"{module_id}-test-b{i}",
                    "role": r,
                    "block_type": (
                        test_types[i % len(test_types)]
                        if test_types[i % len(test_types)] in allowed_test
                        else allowed_test[0]
                    ),
                    "brief": "Which moment calls for washing?",
                    "source_refs": [{"handle": h} for h in all_handles],
                    "example_spec": spec(r),
                }
                for i, r in enumerate(test_roles)
            ],
        }
        mods = [
            {
                "plan_node_id": module_id,
                "title": str(scope.get("title", "Hand hygiene basics")),
                "purpose": str(scope.get("purpose", "Learn the basics.")),
                "entry_knowledge": str(scope.get("entry_knowledge", "None.")),
                "exit_knowledge": str(scope.get("exit_knowledge", "The basics.")),
                "topics": [*[topic(row) for row in scope_topics], test_topic],
            }
        ]
        n = sum(len(t["blocks"]) for t in mods[0]["topics"])
        topics = [t for m in mods for t in m["topics"]]
        return {
            "modules": mods,
            "estimate_inputs": {"blocks": n, "calls": n * 2, "tokens": 1000},
            "through_line": {
                "case": "A ward nurse",
                "handoffs": [
                    {
                        "topic_plan_node_id": t["plan_node_id"],
                        "note": f"The nurse carries {t['title'].lower()} into the next topic.",
                    }
                    for t in topics
                ],
            },
            "uncovered": [],
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
            # the first block of the first lesson is held on its first two readings
            # (the generation and the rework reread) and passes on the third, the
            # author's rewrite: the flow fixes one held block through the product
            hold = (node.endswith("-b0") or node == "t1-b1") and HELD_BEFORE.get(node, 0) < 2
            HELD_BEFORE[node] = HELD_BEFORE.get(node, 0) + 1
            v = "hold" if hold else "pass"
            LOG.write(json.dumps({"reader_node": node, "verdict": v}) + "\n")
            content = b.get("content") if isinstance(b.get("content"), dict) else {}
            span = "Staff apply"
            for key in ("body", "stem", "text", "scenario", "caption"):
                if isinstance(content.get(key), str) and content[key].strip():
                    span = str(content[key])[:30]
                    break
            spot = str(b.get("block_type", "")) == "spot_the_error"
            out.append(
                {
                    "plan_node_id": b["plan_node_id"],
                    "verdict": v,
                    "items": {
                        it: {
                            "verdict": "pass" if not hold else "hold",
                            "evidence": (
                                "not applicable" if it == "planted_error" and not spot else span
                            ),
                            "note": "cite the passage this span rests on" if hold else "",
                        }
                        for it in RUBRIC_ITEMS
                    },
                }
            )
        return {"verdicts": out}
    if head.startswith("# Translator"):
        segs = None
        for name in ("Segments",):
            try:
                segs = section(prompt, name)
            except Exception:
                pass
        if segs is None:
            line = next(ln for ln in reversed(prompt.splitlines()) if '[{"id"' in ln)
            segs, _ = json.JSONDecoder().raw_decode(line, line.index('[{"id"'))
        return {
            **base,
            "segments": [
                {"id": s["id"], "text": " ".join([NB, *carried(s["text"])])} for s in segs
            ],
        }
    try:
        sources = section(prompt, "Approved sources")["sources"]
        first = sources[0]
        quote = str(first["text"])[:40]
        h = str(first.get("handle", "P1"))
    except Exception:
        sources, quote, h = [], "x", "P1"
    try:
        skeleton = section(prompt, "Block skeleton")
    except Exception:
        skeleton = {}
    block_type = str(skeleton.get("block_type") or "reading")
    # a compliant writer cites every passage the plan assigned to the block
    # (or declares it in leftovers); the assignment check holds a writer that
    # drops one silently, so the mock cites each assigned handle verbatim
    assigned = [str(x) for x in skeleton.get("source_handles") or []]
    by_handle = {str(item.get("handle", "")): item for item in sources if isinstance(item, dict)}
    citations = [
        {"passage_id": a, "quote": str(by_handle[a]["text"])[:40]}
        for a in assigned
        if a in by_handle
    ] or [{"passage_id": h, "quote": quote}]
    rights = [str(item["text"])[:40] for item in by_handle.values() if item.get("text")] or [quote]
    return {
        "block_type": block_type,
        # a reading never names its sourcing (narration check), teaches in its own words and
        # shows no digit (number check): the call count that varies replies is spelled in letters
        "text": (
            f"Staff apply the ___ at work (take {spelled(CALLS[0])})."
            if block_type == "cloze"
            else f"Staff apply the routine at work (take {spelled(CALLS[0])})."
        ),
        "citations": citations,
        # the key is quoted from the passage in `answer`; every option is the writer's own
        # words in one shape, so nothing about an option's look gives the key away
        "answer": quote,
        "options": [
            {"text": "Wash at the moment the routine names", "correct": True},
            {"text": "Skip it when the shift is busy", "correct": False},
            {"text": "Rinse once at the end of the day", "correct": False},
        ],
        # three pairs (the matching floor); every right side is quoted from a passage
        "pairs": [{"left": f"Fact {i + 1}", "right": rights[i % len(rights)]} for i in range(3)],
        # a blank is one term taken verbatim from a passage
        "blanks": ["routine"],
        "items": [{"text": quote, "passage_id": h, "tier": "correct"}],
        "sentences": ["Alpha is apply.", "Beta is apply."],
        "handoff": "The learner now knows the routine and the case moved one step.",
        "leftovers": [],
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


# a one-pixel PNG: the image model's reply in e2e
PNG_1x1 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAA"
    "DUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path.endswith("/images/generations"):
            raw = json.dumps({"data": [{"b64_json": PNG_1x1}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        prompt = str(body.get("input", ""))
        instructions = str(body.get("instructions", ""))
        schema = body.get("text", {}).get("format", {}).get("schema", {})
        try:
            r = reply_for(prompt, schema, instructions)
            code = 200
            payload = completed(json.dumps(r))
        except Exception as e:
            code = 500
            payload = {"error": {"message": repr(e)}}
        LOG.write(
            json.dumps(
                {"head": (instructions or prompt)[:25], "code": code, "err": payload.get("error")}
            )
            + "\n"
        )
        LOG.flush()
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
