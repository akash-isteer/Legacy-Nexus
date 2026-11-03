"""Answers factual questions straight from the knowledge graph: exact, instant and free (no AI call).

"Who calls ACCOUNT?", "Which programs write PAYMENT_LEDGER?", "What does PAYMENT call?", "Which job runs
REPORT12?", "What breaks if CUSTOMER_ADDRESS changes?", "How many business rules are there?". Questions that
need explanation return None and go to retrieval + the AI model.
"""
from __future__ import annotations

import re

from app.engine.impact import analyze_impact

_NORM = re.compile(r"[^A-Z0-9]")
ROLE_WORDS = r"(?:programs?|components?|classes|modules?|jobs?|scripts?|procedures?|services?|code|parts?|files?)"


def _n(text: str) -> str:
    return _NORM.sub("", text.upper())


class GraphAnswerer:
    def __init__(self, graph: dict, lineage: dict, insights: dict | None = None, rules: list[dict] | None = None):
        self.graph, self.lineage = graph, lineage
        self.insights, self.rules = insights or {}, rules or []
        self.nodes = {n["id"]: n for n in graph["nodes"]}
        self.by_name: dict[str, list[str]] = {}
        for n in graph["nodes"]:
            names = {n["label"]}
            if n["type"] == "column":
                names.add(n["label"].split(".", 1)[-1])
            if n.get("file") and n["type"] in {"program", "job", "copybook"}:
                names.add(n["file"].split("/")[-1].rsplit(".", 1)[0])
            for name in names:
                self.by_name.setdefault(_n(name), []).append(n["id"])

    # ------------------------------------------------------------------ resolution
    def resolve(self, text: str, prefer: tuple[str, ...] = ()) -> str | None:
        key = _n(re.sub(r"^(the|a|an)\s+", "", text.strip(" ?.!'\"`"), flags=re.I))
        key = re.sub(r"(TABLE|PROGRAM|JOB|COLUMN|FIELD|PROCEDURE|API|CLASS|SERVICE|SCRIPT)$", "", key) or key
        hits = list(dict.fromkeys(self.by_name.get(key, [])))
        if prefer:
            preferred = [h for h in hits if h.split(":", 1)[0] in prefer]
            hits = preferred or hits
        tables = [h for h in hits if h.startswith("table:")]  # a table beats its same-named columns
        if len(tables) == 1:
            return tables[0]
        return hits[0] if len(hits) == 1 else None  # unknown or ambiguous: let retrieval handle it

    # ------------------------------------------------------------------ entry point
    def answer(self, question: str) -> dict | None:
        q = " ".join(question.strip().split())
        for handler in (self._count, self._unused, self._impact, self._callers, self._callees, self._runs,
                        self._writers, self._readers, self._data_of):
            result = handler(q)
            if result is not None:
                return result
        return None

    # ------------------------------------------------------------------ intents
    def _callers(self, q: str):
        m = re.search(rf"^(?:who|what|which {ROLE_WORDS})\s+(?:calls|invokes|uses|depends on)\s+(?P<x>.+?)\??$", q, re.I) \
            or re.search(r"^(?:who are the |what are the |list the )?callers of (?P<x>.+?)\??$", q, re.I)
        if not m:
            return None
        node = self.resolve(m.group("x"), ("program", "procedure", "external"))
        if not node:
            return None
        edges = [e for e in self.graph["edges"] if e["target"] == node and e["type"] in {"CALLS", "EXECUTES", "USES"}]
        return self._list(q, node, edges, "source", f"{self._label(node)} is called or used by", "Nothing in the uploaded code calls {x}.")

    def _callees(self, q: str):
        m = re.search(rf"^(?:what|which {ROLE_WORDS}) does (?P<x>.+?) (?:call|invoke|use)\??$", q, re.I)
        if not m:
            return None
        node = self.resolve(m.group("x"), ("program", "job"))
        if not node:
            return None
        edges = [e for e in self.graph["edges"] if e["source"] == node and e["type"] in {"CALLS", "EXECUTES"}]
        return self._list(q, node, edges, "target", f"{self._label(node)} calls", "{x} does not call any other component.")

    def _runs(self, q: str):
        m = re.search(r"^(?:which|what) (?:jobs?|batch(?: jobs?)?|scripts?) (?:runs?|executes?|starts?|calls?) (?P<x>.+?)\??$", q, re.I)
        if m:
            node = self.resolve(m.group("x"), ("program", "procedure"))
            if not node:
                return None
            edges = [e for e in self.graph["edges"] if e["target"] == node and e["type"] == "EXECUTES"]
            return self._list(q, node, edges, "source", f"{self._label(node)} is run by", "No batch job runs {x}.")
        m = re.search(r"^what does (?:the )?(?:job |script )?(?P<x>.+?) run\??$", q, re.I)
        if m:
            node = self.resolve(m.group("x"), ("job",))
            if not node or not node.startswith("job:"):
                return None
            edges = [e for e in self.graph["edges"] if e["source"] == node and e["type"] == "EXECUTES"]
            return self._list(q, node, edges, "target", f"Job {self._label(node)} runs", "Job {x} runs nothing found in the code.")
        return None

    def _writers(self, q: str):
        m = re.search(rf"^(?:which|what|who)(?: {ROLE_WORDS})? (?:writes?|updates?|inserts? into|modif(?:y|ies)|changes?|saves? to|deletes? from) (?P<x>.+?)\??$", q, re.I)
        if not m:
            return None
        node = self.resolve(m.group("x"), ("table", "column", "dataset"))
        if not node:
            return None
        edges = [e for e in self.graph["edges"] if e["target"] == node and e["type"] == "WRITES"]
        return self._list(q, node, edges, "source", f"{self._label(node)} is written by", "Nothing in the uploaded code writes {x}.")

    def _readers(self, q: str):
        m = re.search(rf"^(?:which|what|who)(?: {ROLE_WORDS})? (?:reads?|uses?|queries|query|selects? from|looks? up) (?P<x>.+?)\??$", q, re.I)
        if not m:
            return None
        node = self.resolve(m.group("x"), ("table", "column", "dataset"))
        if not node or node.split(":", 1)[0] not in {"table", "column", "dataset"}:
            return None
        edges = [e for e in self.graph["edges"] if e["source"] == node and e["type"] == "READ_BY"]
        return self._list(q, node, edges, "target", f"{self._label(node)} is read by", "Nothing in the uploaded code reads {x}.")

    def _data_of(self, q: str):
        m = re.search(r"^what (?:tables|data|database tables) does (?P<x>.+?) (?P<v>read|use|write|update|touch)\??$", q, re.I)
        if not m:
            return None
        node = self.resolve(m.group("x"), ("program", "procedure"))
        if not node:
            return None
        verb = m.group("v").lower()
        edges = []
        if verb in {"read", "use", "touch"}:
            edges += [e for e in self.graph["edges"] if e["target"] == node and e["type"] == "READ_BY" and e["source"].startswith("table:")]
        if verb in {"write", "update", "touch", "use"}:
            edges += [e for e in self.graph["edges"] if e["source"] == node and e["type"] == "WRITES" and e["target"].startswith("table:")]
        other = lambda e: e["source"] if e["target"] == node else e["target"]  # noqa: E731
        items = [(other(e), e) for e in edges]
        return self._render(q, node, items, f"{self._label(node)} {verb}s these tables", "{x} does not " + verb + " any table.")

    def _impact(self, q: str):
        m = re.search(r"^(?:what|which \w+) (?:breaks|would break|is affected|are affected|is impacted|are impacted)"
                      r" (?:if|when) (?:i change |we change )?(?P<x>.+?)(?: changes| is changed| change)?\??$", q, re.I) \
            or re.search(r"^(?:what is the )?impact of (?:changing )?(?P<x>.+?)\??$", q, re.I)
        if not m:
            return None
        node = self.resolve(m.group("x"))
        if not node:
            return None
        result = analyze_impact([node], self.graph, self.lineage)
        items = result["impacted"][:25]
        claims = []
        for i in items:
            ev = next((h for h in reversed(i["evidence"]) if h.get("file")), None)
            claims.append(self._claim(f"{i['label']} ({i['type']}, {i['severity'].lower()} impact, "
                                      f"{i['distance']} step{'s' if i['distance'] != 1 else ''} away)", ev))
        sev = result["severity_counts"]
        text = (f"Changing {self._label(node)} affects {len(result['impacted'])} component(s): "
                f"{sev['High']} high, {sev['Medium']} medium and {sev['Low']} low impact."
                if items else f"No other component depends on {self._label(node)}.")
        return self._result(q, text, claims, kind="impact", node=node)

    def _unused(self, q: str):
        if not re.search(r"\b(unused|dead code|not used|never used|retire|retirement candidates?)\b", q, re.I):
            return None
        if not re.search(r"^(which|what|list|show|are there|is there)", q, re.I):
            return None
        items = self.insights.get("unused_components", [])
        claims = [self._claim(f"{u['name']} ({u['type']}): {u['reason']}", {"file": u.get("file"), "line": u.get("line") or 1})
                  for u in items[:25]]
        text = f"{len(items)} component(s) are not used by anything in the uploaded code." if items else \
            "Every component is used by something in the uploaded code."
        return self._result(q, text, claims, kind="unused")

    def _count(self, q: str):
        m = re.search(r"^how many (programs?|tables?|jobs?|business rules?|rules?|apis?|procedures?|files?)\b", q, re.I)
        if not m:
            return None
        o = self.insights.get("overview", {})
        word = m.group(1).lower().rstrip("s")
        value = {"program": o.get("programs_analysed"), "table": o.get("tables"), "job": o.get("jobs"),
                 "business rule": o.get("business_rules"), "rule": o.get("business_rules"),
                 "api": o.get("api_operations", o.get("external_interfaces")), "procedure": o.get("procedures"),
                 "file": o.get("files")}.get(word)
        if value is None:
            return None
        return self._result(q, f"The application has {value} {m.group(1).lower()}.", [], kind="count")

    # ------------------------------------------------------------------ rendering
    def _label(self, node_id: str) -> str:
        return self.nodes.get(node_id, {}).get("label", node_id.split(":", 1)[-1])

    def _list(self, q, node, edges, end, intro, empty):
        items = [(e[end], e) for e in edges]
        return self._render(q, node, items, intro, empty)

    def _render(self, q, node, items, intro, empty):
        seen, claims, names = set(), [], []
        for other, e in items:
            if other in seen:
                continue
            seen.add(other)
            ev = (e.get("evidence") or [{}])[0]
            names.append(self._label(other))
            claims.append(self._claim(f"{self._label(other)} ({other.split(':', 1)[0]})", ev))
        if not names:
            return self._result(q, empty.format(x=self._label(node)), [], kind="list", node=node)
        listed = ", ".join(names[:12]) + (f" and {len(names) - 12} more" if len(names) > 12 else "")
        return self._result(q, f"{intro}: {listed}.", claims, kind="list", node=node)

    @staticmethod
    def _claim(text: str, ev: dict | None) -> dict:
        cites = []
        if ev and ev.get("file"):
            cites.append({"file": ev["file"], "line_start": ev.get("line") or 1, "line_end": ev.get("line") or 1,
                          "valid": True, "reason": None})
        return {"text": text, "citations": cites, "supported": bool(cites)}

    @staticmethod
    def _result(q: str, text: str, claims: list[dict], kind: str, node: str | None = None) -> dict:
        return {
            "question": q, "mode": "graph", "answer": text, "not_found": False, "abstain_reason": None,
            "confidence": 1.0, "confidence_label": "High",
            "confidence_breakdown": [{"name": "Exact answer from the knowledge graph", "weight": 1.0, "value": 1.0}],
            "claims": claims, "validation": {"citations_total": sum(len(c["citations"]) for c in claims),
                                             "citations_valid": sum(len(c["citations"]) for c in claims)},
            "evidence": [], "facts": [], "entities": [{"name": node.split(':', 1)[-1], "type": node.split(':', 1)[0]}] if node else [],
            "retrieval": {"mode": "graph", "chunks_sent": 0, "chars_sent": 0, "kind": kind},
            "usage": {"input_tokens": 0, "output_tokens": 0, "latency_ms": 0, "cached": False},
        }
