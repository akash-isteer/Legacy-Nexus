"""Modernization assessment and plan. Deterministic: computed from the knowledge graph, rules and insights."""
from __future__ import annotations

import re
from pathlib import Path

import sqlglot

from app.engine.catalog import TARGET_DATABASES, TARGET_LANGUAGES, TARGET_PLATFORMS
from app.engine.model import read_text


def _camel(name: str) -> str:
    return "".join(p.capitalize() for p in re.split(r"[^A-Za-z0-9]+", name) if p)


def _kebab(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def build_plan(root: Path, model: dict, graph: dict, rules: list[dict], insights: dict,
               language: str, database: str, platform: str) -> dict:
    lang = TARGET_LANGUAGES[language]
    db = TARGET_DATABASES[database]
    cloud = TARGET_PLATFORMS[platform]
    progs = insights["programs"]

    assessments = []
    for name, s in sorted(progs.items()):
        p = model["programs"][name]
        strategy, rationale = _strategy(s, p)
        risk = _risk(s, p)
        assessments.append({
            "program": name, "file": s["file"], "type": s["type"], "loc": s["loc"], "complexity": s["complexity"],
            "criticality": s.get("criticality"), "fan_in": s["fan_in"], "fan_out": s["fan_out"],
            "business_rules": s["business_rules"], "strategy": strategy, "rationale": rationale, "risk": risk,
            "target_component": _target_component(name, s, lang),
        })

    services = _services(insights, model, lang, cloud)
    waves = _waves(graph, insights, assessments, rules)
    readiness = _readiness(model, insights, rules, assessments)
    ddl = _convert_ddl(root, model, db["sqlglot_dialect"])
    risks = _risks(model, graph, insights, assessments)
    parity = parity_tests(rules)

    architecture = {
        "layers": [
            {"name": "Channels", "components": ["Web & mobile apps", "Partner integrations"]},
            {"name": "API layer", "components": [cloud["api_gateway"]]},
            {"name": "Services", "components": [s["name"] for s in services]},
            {"name": "Batch", "components": [f"{lang['batch']} · {j}" for j in sorted(model["jobs"])] or [lang["batch"]]},
            {"name": "Integration", "components": [cloud["messaging"], cloud["files"]]},
            {"name": "Data", "components": [cloud["database_hosting"].get(database, db["label"])]},
            {"name": "Platform", "components": [cloud["compute"], cloud["secrets"], cloud["observability"]]},
        ],
        "mappings": [
            {"legacy": "COBOL program", "target": lang["program"]},
            {"legacy": "Paragraph", "target": lang["paragraph"]},
            {"legacy": "Copybook", "target": lang["copybook"]},
            {"legacy": "COMP-3 packed decimal", "target": lang["packed_decimal"]},
            {"legacy": "Embedded SQL (DB2)", "target": f"{lang['data_access']} on {db['label']}"},
            {"legacy": "JCL batch job", "target": lang["batch"]},
            {"legacy": "Job scheduling", "target": cloud["batch"]},
            {"legacy": "Sequential datasets", "target": cloud["files"]},
            {"legacy": "IBM MQ", "target": cloud["messaging"]},
            {"legacy": "CICS / API wrapper", "target": cloud["api_gateway"]},
        ],
    }
    return {
        "targets": {"language": lang["label"], "database": db["label"], "platform": cloud["label"]},
        "readiness": readiness,
        "assessments": assessments,
        "services": services,
        "architecture": architecture,
        "waves": waves,
        "ddl": ddl,
        "risks": risks,
        "parity_summary": {"scenarios": len(parity), "rules_covered": len({t["rule_id"] for t in parity})},
        "strategy_counts": {k: sum(1 for a in assessments if a["strategy"] == k) for k in
                            ("Retire", "Rehost", "Replatform", "Refactor", "Encapsulate")},
    }


def _strategy(s: dict, p: dict) -> tuple[str, str]:
    if s["unused"]:
        return "Retire", "No callers, jobs or APIs reference it. Confirm with operations logs, then decommission."
    if s["complexity"] >= 25 and s["fan_in"] >= 3:
        return "Encapsulate", "High complexity and many dependants: wrap behind an API first (strangler pattern), rewrite later."
    if p["program_type"] == "batch" and s["business_rules"] <= 3:
        return "Replatform", "Batch flow with little business logic: move to the target batch framework with minimal change."
    if s["business_rules"] > 0 or s["fan_in"] > 0:
        return "Refactor", "Contains business rules or serves other programs: re-implement as a service with parity tests."
    runtime = {"cobol": "a COBOL runtime", "java": "a JVM", "csharp": ".NET", "python": "a Python runtime",
               "javascript": "Node.js", "typescript": "Node.js"}.get(p.get("language", "cobol"), "its current runtime")
    return "Rehost", f"Low logic and low coupling: run as-is on {runtime} in the target platform."


def _risk(s: dict, p: dict) -> str:
    score = s["complexity"] / 10 + s["fan_in"] + len(s["datasets_out"]) + s["business_rules"] / 4 + len(p["cics"])
    score += sum(1 for c in p["calls"] if c["dynamic"]) * 2
    return "High" if score >= 6 else "Medium" if score >= 3 else "Low"


def _target_component(name: str, s: dict, lang: dict) -> str:
    if s["unused"]:
        return "Decommission"
    if s["type"] == "batch":
        return f"{lang['batch']}: {_camel(name)}Job"
    return f"{lang['program']}: {_camel(name)}Service"


def _services(insights: dict, model: dict, lang: dict, cloud: dict) -> list[dict]:
    out = []
    for cap in insights["capabilities"]:
        if cap["unused"]:
            continue
        endpoints = []
        for prog in cap["programs"]:
            p = model["programs"][prog]
            if p["program_type"] == "subprogram":
                verb = "POST" if any(insights["programs"][prog]["tables_written"]) else "GET"
                endpoints.append(f"{verb} /{_kebab(prog)}  (replaces CALL '{prog}')")
        for api in cap["apis"]:
            for o in model["apis"].get(api, {}).get("operations", []):
                endpoints.append(f"{o['method']} {o['path']}  (existing {api})")
        out.append({
            "name": f"{_kebab(cap['name'])}-service",
            "capability": cap["name"],
            "technology": lang["service"],
            "programs": cap["programs"],
            "owned_tables": cap["tables"],
            "endpoints": endpoints,
            "batch_jobs": cap["jobs"],
            "hosting": cloud["compute"],
        })
    return out


def _waves(graph: dict, insights: dict, assessments: list[dict], rules: list[dict]) -> list[dict]:
    caps = [c for c in insights["capabilities"]]
    order = insights["application_map"]["order"]
    retire = [a for a in assessments if a["strategy"] == "Retire"]
    waves = []
    if retire:
        waves.append({
            "wave": 0, "name": "Retire unused components", "capabilities": [],
            "programs": [a["program"] for a in retire],
            "activities": ["Confirm zero usage with SMF / job logs", "Archive source and data", "Remove from schedules"],
            "risk": "Low", "rules_to_verify": 0,
        })
    business = {r["program"]: 0 for r in rules}
    for r in rules:
        if not r["is_technical"]:
            business[r["program"]] = business.get(r["program"], 0) + 1
    n = 1
    for cap_name in order:
        cap = next((c for c in caps if c["name"] == cap_name), None)
        if not cap or cap["unused"]:
            continue
        programs = [p for p in cap["programs"] if not insights["programs"][p]["unused"]]
        if not programs:
            continue
        risks = [a["risk"] for a in assessments if a["program"] in programs]
        waves.append({
            "wave": n,
            "name": f"Modernize {cap_name}",
            "capabilities": [cap_name],
            "programs": programs,
            "tables": cap["tables"],
            "activities": [
                "Generate parity tests from the extracted business rules",
                f"Migrate owned tables ({', '.join(cap['tables']) or 'none'}) with dual-write or CDC",
                "Implement the service and route traffic through the API layer",
                "Run old and new side by side and compare outputs before cut-over",
            ],
            "risk": "High" if "High" in risks else "Medium" if "Medium" in risks else "Low",
            "rules_to_verify": sum(business.get(p, 0) for p in programs),
        })
        n += 1
    return waves


def _readiness(model: dict, insights: dict, rules: list[dict], assessments: list[dict]) -> dict:
    o = insights["overview"]
    progs = list(insights["programs"].values())
    avg_cx = sum(p["complexity"] for p in progs) / len(progs) if progs else 0
    documented = sum(1 for p in model["programs"].values() if p["purpose"]) / max(1, len(model["programs"]))
    undocumented_ext = sum(1 for i in insights["external_interfaces"] if not i["documented"])
    dynamic_calls = sum(1 for p in model["programs"].values() for c in p["calls"] if c["dynamic"])
    factors = [
        {"name": "Code complexity", "score": max(0, 100 - avg_cx * 3), "detail": f"Average cyclomatic complexity {avg_cx:.1f}"},
        {"name": "Rule traceability", "score": 100 if rules else 40, "detail": f"{o['business_rules']} business rules linked to source lines"},
        {"name": "Self-documentation", "score": round(documented * 100), "detail": f"{round(documented * 100)}% of programs have a purpose header"},
        {"name": "Interface clarity", "score": max(0, 100 - undocumented_ext * 15), "detail": f"{undocumented_ext} interface(s) used without a specification"},
        {"name": "Static resolvability", "score": max(0, 100 - dynamic_calls * 20), "detail": f"{dynamic_calls} dynamic CALL(s) that cannot be resolved statically"},
    ]
    score = round(sum(f["score"] for f in factors) / len(factors))
    return {"score": score, "level": "Ready" if score >= 75 else "Needs preparation" if score >= 50 else "High effort",
            "factors": [{**f, "score": round(f["score"])} for f in factors]}


def _convert_ddl(root: Path, model: dict, dialect: str) -> list[dict]:
    out = []
    seen_files = sorted({t["path"] for t in model["tables"].values()})
    for path in seen_files:
        text = read_text(root / path)
        for stmt in re.split(r";\s*\n", text):
            clean = re.sub(r"--[^\n]*", "", stmt).strip()
            if not re.match(r"CREATE\s+TABLE", clean, re.I):
                continue
            name = re.search(r"TABLE\s+([A-Z0-9_.]+)", clean, re.I).group(1).upper()
            try:
                converted = sqlglot.transpile(clean, write=dialect, pretty=True)[0]
                status = "converted"
            except Exception as exc:  # keep going; report the statement for manual conversion
                converted, status = f"-- manual conversion required: {exc}", "manual"
            out.append({"table": name, "source": clean, "target": converted, "status": status, "file": path})
    return out


def _risks(model: dict, graph: dict, insights: dict, assessments: list[dict]) -> list[dict]:
    risks = []
    for i in insights["external_interfaces"]:
        if not i["documented"]:
            risks.append({"severity": "High", "title": f"Undocumented interface {i['name']}",
                          "detail": f"Used by {', '.join(i['used_by'])}. Its contract must be captured before migration."})
    for n in graph["nodes"]:
        if n["type"] == "table" and not n.get("declared"):
            risks.append({"severity": "Medium", "title": f"No DDL for table {n['label']}",
                          "detail": "Referenced in code but the schema was not uploaded; types cannot be mapped."})
        if n["type"] == "program" and n.get("missing"):
            risks.append({"severity": "High", "title": f"Missing source for {n['label']}",
                          "detail": "Referenced by JCL or APIs but the source was not uploaded."})
    for p in model["programs"].values():
        for c in p["calls"]:
            if c["dynamic"]:
                risks.append({"severity": "Medium", "title": f"Dynamic CALL in {p['program_id']}",
                              "detail": f"Target resolved at runtime ({c['target']}) at {p['path']}:{c['line']}."})
    packed = [p["program_id"] for p in model["programs"].values() if any((d.get("usage") or "").startswith("COMP-3") for d in p["data_items"])]
    if packed:
        risks.append({"severity": "Medium", "title": "Packed-decimal arithmetic",
                      "detail": f"{', '.join(packed)} use COMP-3; rounding must be reproduced exactly (use decimal types, never floating point)."})
    for a in assessments:
        if a["risk"] == "High":
            risks.append({"severity": "High", "title": f"High-risk program {a['program']}",
                          "detail": f"Complexity {a['complexity']}, fan-in {a['fan_in']}, {a['business_rules']} rules."})
    order = {"High": 0, "Medium": 1, "Low": 2}
    return sorted(risks, key=lambda r: order[r["severity"]])


def parity_tests(rules: list[dict]) -> list[dict]:
    """Behavioural-equivalence scenarios derived from business rules (positive and negative cases)."""
    tests = []
    for r in rules:
        if r["is_technical"]:
            continue
        src = f"{r['file']}:{r['start_line']}-{r['end_line']}"
        if r["kind"] == "decision":
            then_actions = [a for a in r["actions"] if a != "ELSE"]
            if "ELSE" in r["actions"]:
                idx = r["actions"].index("ELSE")
                then_actions, else_actions = r["actions"][:idx], r["actions"][idx + 1:]
            else:
                else_actions = []
            tests.append({"rule_id": r["id"], "program": r["program"], "scenario": f"{r['id']} condition holds",
                          "given": f"{r['condition']} is TRUE", "then": "; ".join(then_actions) or "no change", "source": src})
            tests.append({"rule_id": r["id"], "program": r["program"], "scenario": f"{r['id']} condition does not hold",
                          "given": f"{r['condition']} is FALSE", "then": "; ".join(else_actions) or "none of the above actions occur",
                          "source": src})
        elif r["kind"] == "classification":
            for b in r["branches"]:
                tests.append({"rule_id": r["id"], "program": r["program"], "scenario": f"{r['id']} when {b['when']}",
                              "given": f"{r['condition'].replace('EVALUATE ', '')} = {b['when']}",
                              "then": "; ".join(b["actions"]) or "no action", "source": src})
        elif r["kind"] == "calculation":
            tests.append({"rule_id": r["id"], "program": r["program"], "scenario": f"{r['id']} calculation matches",
                          "given": "representative inputs incl. boundary and rounding cases",
                          "then": f"result of {r['actions'][0]} equals the legacy output to the last decimal", "source": src})
        elif r["kind"] == "domain":
            tests.append({"rule_id": r["id"], "program": r["program"], "scenario": f"{r['id']} domain values",
                          "given": "each allowed value and one invalid value", "then": r["description"], "source": src})
    return tests


def parity_gherkin(tests: list[dict]) -> str:
    out = "Feature: Behavioural parity between legacy and modernized system\n\n"
    for t in tests:
        out += f"  # Source: {t['source']}\n  Scenario: {t['program']} - {t['scenario']}\n"
        out += f"    Given {t['given']}\n    When {t['program']} logic is executed\n    Then {t['then']}\n\n"
    return out
