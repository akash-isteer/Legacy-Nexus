"""Grounded question answering over a legacy repository.

Pipeline: entity linking -> hybrid retrieval (BM25 + entity/graph boosts) -> verified graph facts ->
abstain check -> LLM with strict JSON contract -> citation & identifier validation -> calibrated confidence.
Only small line-numbered excerpts are sent to the model. Never whole files.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol

from app.engine.retrieval import BM25Index
from app.llm.providers import LLMConfig, LLMError, complete_json

NOT_FOUND = "Not found in the provided code."

SYSTEM_PROMPT = """You are LegacyNexus, an analyst that explains legacy enterprise applications (COBOL, JCL, DB2 SQL, copybooks, APIs).

Rules you must follow:
1. Use ONLY the FACTS and SOURCE EXCERPTS in the user message. Never use outside knowledge about this codebase.
2. Every factual claim must cite at least one source as {"file", "line_start", "line_end"} using the exact file paths and line numbers shown in the excerpts or facts.
3. Use only program, paragraph, table, column, field, job and file names that appear in the excerpts or facts. Never invent names.
4. If the excerpts do not contain enough evidence to answer, set "not_found" to true, set "answer" to "Not found in the provided code." and return an empty "claims" list. Do not guess.
5. Be concise and specific: name the program, paragraph, table and field involved.
6. "confidence" is your probability (0.0-1.0) that the answer is fully supported by the cited lines.
7. A CONVERSATION SO FAR section, when present, only tells you what a follow-up question refers to. Never cite it or treat it as evidence.

Respond with ONE JSON object and nothing else, in exactly this shape:
{"answer": "string", "not_found": false, "confidence": 0.0, "claims": [{"text": "string", "citations": [{"file": "path", "line_start": 1, "line_end": 1}]}]}"""

GENERIC_WORDS = {
    "COBOL", "JCL", "SQL", "DB2", "API", "APIS", "CICS", "IBM", "JSON", "HTTP", "REST", "MQ", "DDL", "VSAM", "IMS",
    "NOTE", "THEN", "WHEN", "CALL", "CALLS", "MOVE", "COMPUTE", "PERFORM", "SELECT", "UPDATE", "INSERT", "DELETE",
    "FROM", "WHERE", "INTO", "EVALUATE", "TRUE", "FALSE", "NULL", "ELSE", "OTHER", "EXEC", "FETCH", "CURSOR",
    "TABLE", "FIELD", "PROGRAM", "PARAGRAPH", "DIVISION", "SECTION", "LINKAGE", "STORAGE", "WORKING", "PROCEDURE",
    "DATA", "FILE", "BATCH", "STEP", "DATASET", "OPEN", "CLOSE", "READ", "WRITE", "GOBACK", "STOP", "RUN", "ADD",
}


@dataclass
class QASettings:
    top_k: int = 8
    context_char_budget: int = 14_000
    min_retrieval_score: float = 1.5
    min_confidence: float = 0.35


RRF_K = 60  # Reciprocal Rank Fusion constant (standard value from Cormack et al.)


class SemanticSearch(Protocol):
    """Vector search over the project's chunks. Provided by the service layer when an index is ready."""

    min_score: float
    weight: float
    label: str

    def search(self, question: str, top_k: int) -> list[tuple[str, float]]: ...


@dataclass
class Retrieval:
    evidence: list[dict]
    entities: list[dict]
    facts: list[dict]
    keyword_top: float
    semantic_top: float | None  # None when semantic search was not used
    strength: float  # 0..1, combined retrieval strength used for gating and confidence
    mode: str  # "hybrid" | "keyword"
    warning: str | None = None

    @property
    def matched(self) -> bool:
        return bool(self.evidence)


def _norm(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text.upper())


class QAEngine:
    def __init__(self, chunks: list[dict], symbols: list[dict], graph: dict, lineage: dict, files: dict[str, dict]):
        self.chunks = chunks
        self.index = BM25Index(chunks)
        self.symbols = symbols
        self.graph = graph
        self.lineage = lineage
        self.files = files  # path -> {line_count, ...}
        self.basenames: dict[str, list[str]] = {}
        for path in files:
            self.basenames.setdefault(path.split("/")[-1].upper(), []).append(path)
        self.symbol_names = {_norm(s["name"]) for s in symbols} | {_norm(b) for b in self.basenames} | {
            _norm(b.rsplit(".", 1)[0]) for b in self.basenames}
        self.by_norm: dict[str, list[dict]] = {}
        for s in symbols:
            if len(s["name"]) >= 3:
                self.by_norm.setdefault(_norm(s["name"]), []).append(s)
            if s["type"] == "column":  # people say CUSTOMER_ADDRESS, not CUSTOMER_MASTER.CUSTOMER_ADDRESS
                short = s["name"].split(".", 1)[1]
                self.by_norm.setdefault(_norm(short), []).append(s)
                self.symbol_names.add(_norm(short))

    # ------------------------------------------------------------------ retrieval
    def link_entities(self, question: str) -> list[dict]:
        found: dict[str, dict] = {}
        tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9_.-]*[A-Za-z0-9]", question)
        candidates = set()
        for t in tokens:
            candidates.add(_norm(t))
            if "." in t:
                candidates.add(_norm(t.rsplit(".", 1)[0]))
        for i in range(len(tokens) - 1):  # "account master" -> ACCOUNT_MASTER
            candidates.add(_norm(tokens[i] + tokens[i + 1]))
        for c in candidates:
            for s in self.by_norm.get(c, []):
                found.setdefault(s["id"] + s["name"], s)
        return list(found.values())

    def _boosts(self, entities: list[dict]) -> dict[str, float]:
        names = {e["name"] for e in entities}
        programs = {e["name"] for e in entities if e["type"] == "program"} | {
            e.get("program") for e in entities if e.get("program")}
        neighbours = set()
        component_ids = {e["id"] for e in entities}
        for edge in self.graph["edges"]:
            if edge["source"] in component_ids and edge["target"].startswith("program:"):
                neighbours.add(edge["target"].split(":", 1)[1])
            if edge["target"] in component_ids and edge["source"].startswith("program:"):
                neighbours.add(edge["source"].split(":", 1)[1])
        boosts = {}
        for c in self.chunks:
            b = 3.0 * len(names & set(c["symbols"]))
            if c.get("program") in programs:
                b += 1.5
            elif c.get("program") in neighbours:
                b += 0.75
            if b:
                boosts[c["id"]] = b
        return boosts

    def facts_for(self, entities: list[dict], limit: int = 14) -> list[dict]:
        ids = {e["id"] for e in entities}
        tables = {e["id"].split(":", 1)[1].split(".")[0] for e in entities if e["type"] == "column"}
        ids |= {f"table:{t}" for t in tables}
        facts = []
        verbs = {"CALLS": "calls", "READ_BY": "is read by", "WRITES": "writes", "EXECUTES": "runs",
                 "EXPOSED_AS": "is exposed as", "INCLUDED_IN": "is included in", "REFERENCES": "references"}
        for e in self.graph["edges"]:
            if e["type"] == "HAS_COLUMN" or not e["evidence"]:
                continue
            if e["source"] in ids or e["target"] in ids:
                ev = e["evidence"][0]
                facts.append({"text": f"{e['source'].split(':', 1)[1]} {verbs.get(e['type'], e['type'].lower())} "
                                      f"{e['target'].split(':', 1)[1]}", "file": ev["file"], "line": ev["line"]})
        field_ids = {e["id"] for e in entities if e["type"] in {"field", "condition", "column"}}
        for e in self.lineage["edges"]:
            if e["type"] == "DATA" and (e["source"] in field_ids or e["target"] in field_ids) and e["evidence"]:
                ev = e["evidence"][0]
                facts.append({"text": f"{e['source'].split(':', 1)[1]} flows to {e['target'].split(':', 1)[1]} ({e.get('how')})",
                              "file": ev["file"], "line": ev["line"]})
        seen, unique = set(), []
        for f in facts:
            if f["text"] not in seen:
                seen.add(f["text"])
                unique.append(f)
        return unique[:limit]

    def retrieve(self, question: str, settings: QASettings, semantic: SemanticSearch | None = None) -> Retrieval:
        """Hybrid retrieval: BM25 (+ entity and graph boosts) fused with vector search via Reciprocal Rank Fusion."""
        entities = self.link_entities(question)
        pool = max(20, settings.top_k * 3)
        keyword_hits = self.index.search(question, self._boosts(entities), top_k=pool)
        keyword_top = keyword_hits[0][1] if keyword_hits else 0.0

        semantic_hits: list[tuple[str, float]] = []
        semantic_top: float | None = None
        warning = None
        if semantic is not None:
            try:
                raw = semantic.search(question, pool)
                semantic_top = raw[0][1] if raw else 0.0
                semantic_hits = [(cid, sim) for cid, sim in raw if sim >= semantic.min_score]
            except Exception as exc:  # embedding service down: degrade to keyword search, never fail the question
                warning = f"Semantic search unavailable, used keyword search only. {exc}"
                semantic = None

        by_id = {c["id"]: c for c in self.chunks}
        fused: dict[str, dict] = {}
        for rank, (chunk, score) in enumerate(keyword_hits):
            fused[chunk["id"]] = {"rrf": 1.0 / (RRF_K + rank + 1), "keyword_score": round(score, 2),
                                  "similarity": None}
        weight = semantic.weight if semantic else 0.0
        for rank, (cid, sim) in enumerate(semantic_hits):
            if cid not in by_id:
                continue
            entry = fused.setdefault(cid, {"rrf": 0.0, "keyword_score": None, "similarity": None})
            entry["rrf"] += weight / (RRF_K + rank + 1)
            entry["similarity"] = round(sim, 3)

        ranked = sorted(fused.items(), key=lambda kv: -kv[1]["rrf"])
        selected, used = [], 0
        for cid, info in ranked:
            if len(selected) >= settings.top_k:
                break
            chunk = by_id[cid]
            text = self._numbered(chunk)
            if used + len(text) > settings.context_char_budget:
                remaining = settings.context_char_budget - used
                if remaining < 400:
                    break
                text = text[:remaining].rsplit("\n", 1)[0]
            found_by = ("both" if info["keyword_score"] is not None and info["similarity"] is not None
                        else "keyword" if info["keyword_score"] is not None else "semantic")
            selected.append({**chunk, "score": info["keyword_score"], "similarity": info["similarity"],
                             "found_by": found_by, "numbered": text,
                             "end_line": chunk["start_line"] + text.count("\n")})
            used += len(text)

        strength = self._keyword_strength(keyword_top, bool(entities))
        if semantic is not None and semantic_top is not None:
            strength = max(strength, self._semantic_strength(semantic_top, semantic.min_score))
        return Retrieval(evidence=selected, entities=entities, facts=self.facts_for(entities),
                         keyword_top=keyword_top, semantic_top=semantic_top, strength=round(strength, 3),
                         mode="hybrid" if semantic is not None else "keyword", warning=warning)

    @staticmethod
    def _keyword_strength(top_score: float, linked: bool) -> float:
        return min(1.0, top_score / (top_score + 6.0) * 1.6 + (0.15 if linked else 0.0))

    @staticmethod
    def _semantic_strength(similarity: float, min_score: float) -> float:
        """Map cosine similarity to 0..1: below the threshold scales to 0.5, then reaches 1.0 at threshold + 0.3."""
        if min_score <= 0:
            return max(0.0, min(1.0, similarity))
        if similarity < min_score:
            return max(0.0, 0.5 * similarity / min_score)
        return min(1.0, 0.5 + 0.5 * (similarity - min_score) / 0.3)

    def _passes_gate(self, r: Retrieval, settings: QASettings, semantic: SemanticSearch | None) -> bool:
        if not r.evidence:
            return False
        if r.keyword_top >= settings.min_retrieval_score or r.entities:
            return True
        return semantic is not None and r.semantic_top is not None and r.semantic_top >= semantic.min_score

    @staticmethod
    def _numbered(chunk: dict) -> str:
        return "\n".join(f"{chunk['start_line'] + i:>5} | {ln}" for i, ln in enumerate(chunk["text"].split("\n")))

    # ------------------------------------------------------------------ answer
    def answer(self, question: str, settings: QASettings, llm: LLMConfig | None,
               semantic: SemanticSearch | None = None, complete=None, history: str = "",
               retrieval_query: str | None = None, resolved_question: str | None = None) -> dict:
        """Retrieve evidence and, when a model is configured, ask it for a cited answer.

        ``complete(cfg, system, user, sources)`` sends the prompt (the LLM gateway in the application:
        cache, budgets, routing, failover, scrubbing, usage log); defaults to a direct provider call.
        For a follow-up, ``retrieval_query`` is what to search with, ``resolved_question`` its stand-alone
        reading and ``history`` the earlier turns (see services.conversation).
        """
        r = self.retrieve(retrieval_query or question, settings, semantic)
        evidence, facts = r.evidence, r.facts
        base = {
            "question": question,
            "entities": [{"name": e["name"], "type": e["type"]} for e in r.entities][:12],
            "facts": facts,
            "evidence": [self._evidence_view(c) for c in evidence],
            "retrieval": {"mode": r.mode, "chunks_sent": len(evidence),
                          "chars_sent": sum(len(c["numbered"]) for c in evidence),
                          "top_score": round(r.keyword_top, 2),
                          "top_similarity": None if r.semantic_top is None else round(r.semantic_top, 3),
                          "strength": r.strength,
                          "found_by": {k: sum(1 for c in evidence if c["found_by"] == k)
                                       for k in ("keyword", "semantic", "both")}},
        }
        if r.warning:
            base["warning"] = r.warning
        if not self._passes_gate(r, settings, semantic):
            return {**base, **self._abstain("No code matched the question closely enough.", mode="abstained")}
        if llm is None or not llm.configured:
            return {**base, **self._evidence_only(evidence, facts, r.strength)}

        prompt = self._prompt(question, evidence, facts, history, resolved_question)
        sources =[{"file": c["file"], "start_line": c["start_line"], "end_line": c["end_line"]} for c in evidence]
        try:
            if complete is not None:
                result = complete(llm, SYSTEM_PROMPT, prompt, sources)
            else:
                result = complete_json(llm, SYSTEM_PROMPT, prompt)
        except LLMError as exc:
            out = self._evidence_only(evidence, facts, r.strength)
            out["warning"] = f"AI model call failed, showing static-analysis evidence instead. {exc}"
            return {**base, **out}

        parsed = parse_json(result.text)
        if parsed is None:
            out = self._abstain("The model did not return valid structured output.", mode="abstained")
            return {**base, **out, "model": result.model}
        validated = self.validate(parsed, evidence, facts)
        confidence = self._confidence(parsed, validated, r.strength)
        not_found = bool(parsed.get("not_found")) or validated["citations_valid"] == 0 or confidence["score"] < settings.min_confidence
        answer = NOT_FOUND if not_found else str(parsed.get("answer", "")).strip()
        reason = None
        if not_found and not parsed.get("not_found"):
            reason = ("No claim could be verified against the retrieved code." if validated["citations_valid"] == 0
                      else "Confidence was below the abstain threshold.")
        return {
            **base,
            "mode": "llm",
            "answer": answer,
            "not_found": not_found,
            "abstain_reason": reason,
            "confidence": round(confidence["score"] if not not_found else min(confidence["score"], 0.3), 2),
            "confidence_label": _label(confidence["score"]) if not not_found else "Low",
            "confidence_breakdown": confidence["parts"],
            "claims": [] if not_found else validated["claims"],
            "validation": {k: v for k, v in validated.items() if k != "claims"},
            "model": result.model,
            "usage": {"input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
                      "latency_ms": result.latency_ms, "cached": bool(getattr(result, "cached", False)),
                      "redactions": getattr(result, "redactions", 0)},
        }

    def _prompt(self, question: str, evidence: list[dict], facts: list[dict], history: str = "",
                resolved_question: str | None = None) -> str:
        parts = [history] if history else []
        parts.append(f"QUESTION:\n{question}\n")
        if resolved_question and resolved_question != question:
            parts.append(f"(In this conversation the question means: {resolved_question})\n")
        if facts:
            parts.append("FACTS (from static analysis; each is verified at the cited line):")
            parts += [f"- {f['text']} — {f['file']}:{f['line']}" for f in facts]
            parts.append("")
        parts.append("SOURCE EXCERPTS (line numbers on the left):")
        for i, c in enumerate(evidence, start=1):
            parts.append(f"[S{i}] {c['file']} lines {c['start_line']}-{c['end_line']} — {c['title']}\n{c['numbered']}\n")
        parts.append("Return the JSON object now.")
        return "\n".join(parts)

    # ------------------------------------------------------------------ validation
    def _resolve_file(self, name: str) -> str | None:
        name = (name or "").strip().strip("`")
        if name in self.files:
            return name
        matches = self.basenames.get(name.split("/")[-1].upper(), [])
        return matches[0] if len(matches) == 1 else None

    def validate(self, parsed: dict, evidence: list[dict], facts: list[dict]) -> dict:
        ranges = [(c["file"], c["start_line"], c["end_line"]) for c in evidence] + [
            (f["file"], f["line"], f["line"]) for f in facts]
        claims_out, total, valid = [], 0, 0
        for claim in parsed.get("claims") or []:
            if not isinstance(claim, dict):
                continue
            cites_out = []
            for cite in claim.get("citations") or []:
                if not isinstance(cite, dict):
                    continue
                total += 1
                path = self._resolve_file(str(cite.get("file", "")))
                try:
                    start = int(cite.get("line_start") or cite.get("line") or 0)
                    end = int(cite.get("line_end") or start)
                except (TypeError, ValueError):
                    start = end = 0
                reason = None
                if path is None:
                    reason = "File does not exist in the repository"
                elif start < 1 or end < start or end > self.files[path]["line_count"]:
                    reason = "Line range does not exist in the file"
                elif not any(f == path and start <= r_end + 2 and end >= r_start - 2 for f, r_start, r_end in ranges):
                    reason = "Lines were not part of the retrieved evidence"
                ok = reason is None
                valid += ok
                cites_out.append({"file": path or cite.get("file"), "line_start": start, "line_end": end,
                                  "valid": ok, "reason": reason})
            claims_out.append({"text": str(claim.get("text", "")), "citations": cites_out,
                               "supported": any(c["valid"] for c in cites_out)})
        answer = str(parsed.get("answer", "")) + " " + " ".join(c["text"] for c in claims_out)
        idents = self._identifiers(answer)
        unknown = sorted(i for i in idents if _norm(i) not in self.symbol_names)
        return {
            "claims": claims_out,
            "citations_total": total,
            "citations_valid": valid,
            "claims_total": len(claims_out),
            "claims_supported": sum(1 for c in claims_out if c["supported"]),
            "identifiers_checked": len(idents),
            "unknown_identifiers": unknown,
        }

    @staticmethod
    def _identifiers(text: str) -> set[str]:
        """Upper-case names in the answer that look like code identifiers (FIELD-NAME, TABLE_NAME, FILE.CBL, PROGRAM)."""
        tokens = re.findall(r"\b[A-Z0-9]+(?:[-_.][A-Z0-9]+)+\b|\b[A-Z][A-Z0-9]{3,}\b", text)
        return {t for t in tokens if t not in GENERIC_WORDS and re.search(r"[A-Z]", t)}

    @staticmethod
    def _confidence(parsed: dict, v: dict, retrieval: float) -> dict:
        try:
            model = max(0.0, min(1.0, float(parsed.get("confidence", 0.5))))
        except (TypeError, ValueError):
            model = 0.5
        citation = v["citations_valid"] / v["citations_total"] if v["citations_total"] else 0.0
        support = v["claims_supported"] / v["claims_total"] if v["claims_total"] else 0.0
        idents = 1.0 - (len(v["unknown_identifiers"]) / v["identifiers_checked"] if v["identifiers_checked"] else 0.0)
        score = 0.30 * model + 0.25 * citation + 0.20 * support + 0.15 * retrieval + 0.10 * idents
        if v["unknown_identifiers"]:
            score = min(score, 0.6)
        parts = [
            {"name": "Model self-assessment", "weight": 0.30, "value": round(model, 2)},
            {"name": "Valid citations", "weight": 0.25, "value": round(citation, 2)},
            {"name": "Claims with evidence", "weight": 0.20, "value": round(support, 2)},
            {"name": "Retrieval strength", "weight": 0.15, "value": round(retrieval, 2)},
            {"name": "Known identifiers only", "weight": 0.10, "value": round(idents, 2)},
        ]
        return {"score": round(score, 3), "parts": parts}

    # ------------------------------------------------------------------ fallbacks
    @staticmethod
    def _abstain(reason: str, mode: str) -> dict:
        return {"mode": mode, "answer": NOT_FOUND, "not_found": True, "abstain_reason": reason, "confidence": 0.0,
                "confidence_label": "Low", "confidence_breakdown": [], "claims": [], "validation": {}}

    def _evidence_only(self, evidence: list[dict], facts: list[dict], retrieval: float) -> dict:
        claims = [{"text": f["text"], "citations": [{"file": f["file"], "line_start": f["line"], "line_end": f["line"],
                                                     "valid": True, "reason": None}], "supported": True} for f in facts[:6]]
        for c in evidence[:3]:
            claims.append({"text": f"Relevant code: {c['title']}", "supported": True, "citations": [
                {"file": c["file"], "line_start": c["start_line"], "line_end": c["end_line"], "valid": True, "reason": None}]})
        score = round(0.6 * retrieval, 2)
        return {
            "mode": "evidence_only",
            "answer": ("No AI model is configured, so this is the static-analysis evidence for your question. "
                       "Configure a model in Settings to get a written answer."),
            "not_found": False,
            "abstain_reason": None,
            "confidence": score,
            "confidence_label": _label(score),
            "confidence_breakdown": [{"name": "Retrieval strength", "weight": 0.6, "value": round(retrieval, 2)}],
            "claims": claims,
            "validation": {"citations_total": len(claims), "citations_valid": len(claims)},
        }

    @staticmethod
    def _evidence_view(c: dict) -> dict:
        lines = c["text"].split("\n")
        return {"file": c["file"], "start_line": c["start_line"], "end_line": c["end_line"], "title": c["title"],
                "program": c.get("program"), "score": c["score"], "similarity": c.get("similarity"),
                "found_by": c.get("found_by", "keyword"), "snippet": "\n".join(lines[:14])}


def _label(score: float) -> str:
    return "High" if score >= 0.75 else "Medium" if score >= 0.5 else "Low"


def parse_json(text: str) -> dict | None:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def suggested_questions(insights: dict, graph: dict, rules: list[dict]) -> list[str]:
    """Deterministic starter questions built from what the analysis found."""
    qs = []
    calcs = [r for r in rules if r["kind"] == "calculation" and not r["is_technical"]]
    for kw in ("INTEREST", "FEE", "BALANCE", "PREMIUM", "TAX", "AMOUNT", "TOTAL"):
        if any(kw in r["description"].upper() for r in calcs):
            qs.append(f"Where is {kw.lower()} calculated?")
            break
    crit = sorted(insights["programs"].values(), key=lambda s: -s["criticality_score"])
    if crit:
        qs.append(f"What does {crit[0]['program']} do and who calls it?")
    cols = [n["label"] for n in graph["nodes"] if n["type"] == "column"]
    for c in cols:
        if "ADDRESS" in c or "STATUS" in c:
            qs.append(f"Which programs use {c.split('.', 1)[1]}?")
            break
    val = next((r for r in rules if r["category"] == "Validation"), None)
    if val:
        qs.append(f"What validations does {val['program']} perform?")
    jobs = [n["label"] for n in graph["nodes"] if n["type"] == "job"]
    if jobs:
        qs.append(f"What happens in the {jobs[0]} batch job?")
    qs.append("Where are payments above the approval limit handled?" if any("APPROVAL" in r["description"].upper() for r in rules)
              else "Which tables are written by more than one program?")
    return qs[:6]
