"""Deterministic documentation generator. Every statement links back to a source location (file:line)."""
from __future__ import annotations

from collections import defaultdict


def _ref(file: str | None, line: int | None) -> str:
    return f"`{file}:{line}`" if file and line else (f"`{file}`" if file else "")


def _table(headers: list[str], rows: list[list]) -> str:
    if not rows:
        return "_None found._\n"
    out = "| " + " | ".join(headers) + " |\n| " + " | ".join("---" for _ in headers) + " |\n"
    for r in rows:
        out += "| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ") if c not in (None, "") else "—" for c in r) + " |\n"
    return out


def generate_docs(model: dict, graph: dict, lineage: dict, rules: list[dict], insights: dict, project_name: str) -> list[dict]:
    progs = insights["programs"]
    docs = [
        _overview(model, insights, project_name),
        _modules(model, insights, rules),
        _apis(model, insights),
        _data_flow(model, graph, lineage),
        _rules_doc(rules),
        _database(model, graph),
        _dependencies(model, graph, insights, progs),
    ]
    return docs


def _overview(model: dict, insights: dict, project_name: str) -> dict:
    o = insights["overview"]
    md = f"# {project_name} — Application overview\n\n"
    purposes = [(n, p["purpose"]) for n, p in sorted(model["programs"].items()) if p["purpose"]]
    md += "## What the application does\n\n"
    for cap in insights["capabilities"]:
        if cap["unused"]:
            continue
        md += f"- **{cap['name']}** — programs {', '.join(cap['programs'])}"
        if cap["tables"]:
            md += f"; owns {', '.join(cap['tables'])}"
        md += ".\n"
    md += "\n## Application map\n\n"
    md += " → ".join(insights["application_map"]["order"]) + "\n\n"
    md += "## Inventory\n\n"
    md += _table(["Measure", "Count"], [
        ["Programs analysed", o["programs_analysed"]], ["Lines of code", o["lines_of_code"]],
        ["Copybooks", o["copybooks"]], ["Batch jobs", o["jobs"]], ["Database tables", o["tables"]],
        ["Stored procedures", o["procedures"]], ["Datasets", o["datasets"]],
        ["Critical programs", o["critical_programs"]], ["Unused / low-use components", o["unused_components"]],
        ["Database dependencies", o["db_dependencies"]], ["Business rules", o["business_rules"]],
        ["External interfaces", o["external_interfaces"]],
    ])
    md += "\n## Program purposes\n\n"
    md += _table(["Program", "Type", "Purpose", "Source"], [
        [n, model["programs"][n]["program_type"], p, _ref(model["programs"][n]["path"], 1)] for n, p in purposes
    ])
    md += "\n## Entry points\n\n"
    rows = [[f"Job {j['name']}", j["description"], _ref(j["path"], j["line"])] for j in model["jobs"].values()]
    rows += [[f"API {a['id']}", a["title"], _ref(a["path"], a["line"])] for a in model["apis"].values()]
    md += _table(["Entry point", "Description", "Source"], rows)
    if o["warnings"]:
        md += "\n## Parser warnings\n\n" + "\n".join(f"- {w}" for w in o["warnings"]) + "\n"
    return {"id": "application-overview", "title": "Application overview", "category": "Overview", "markdown": md}


def _modules(model: dict, insights: dict, rules: list[dict]) -> dict:
    md = "# Module documentation\n\n"
    rules_by_prog = defaultdict(list)
    for r in rules:
        if not r["is_technical"]:
            rules_by_prog[r["program"]].append(r)
    for name in sorted(model["programs"]):
        p, s = model["programs"][name], insights["programs"][name]
        md += f"## {name}\n\n"
        md += f"{p['purpose'] or '_No header comment found._'}\n\n"
        md += _table(["Attribute", "Value"], [
            ["Source", _ref(p["path"], 1)], ["Type", p["program_type"]], ["Lines of code", p["loc"]],
            ["Cyclomatic complexity", s["complexity"]], ["Criticality", f"{s.get('criticality', '—')} ({s['criticality_score']})"],
            ["Called by", ", ".join(s["callers"])], ["Calls", ", ".join(s["callees"])],
            ["Run by jobs", ", ".join(s["jobs"])], ["Exposed as APIs", ", ".join(s["apis"])],
            ["Reads tables", ", ".join(s["tables_read"])], ["Writes tables", ", ".join(s["tables_written"])],
            ["Input datasets", ", ".join(s["datasets_in"])], ["Output datasets", ", ".join(s["datasets_out"])],
        ])
        if p["using_params"]:
            md += "\n**Interface (PROCEDURE DIVISION USING)**\n\n"
            items = {d["name"]: d for d in p["data_items"]}
            md += _table(["Parameter", "PIC", "Source"], [
                [prm, items.get(prm, {}).get("pic") or "group", _ref(p["path"], items.get(prm, {}).get("line"))]
                for prm in p["using_params"]
            ])
        md += "\n**Paragraphs**\n\n"
        performs = defaultdict(list)
        for x in p["performs"]:
            performs[x["from"]].append(x["to"])
        md += _table(["Paragraph", "Lines", "Performs"], [
            [para["name"], f"{para['start_line']}–{para['end_line']}", ", ".join(dict.fromkeys(performs.get(para["name"], [])))]
            for para in p["paragraphs"]
        ])
        if rules_by_prog[name]:
            md += "\n**Business rules**\n\n" + "\n".join(
                f"- **{r['id']}** {r['description']} {_ref(r['file'], r['start_line'])}" for r in rules_by_prog[name]) + "\n"
        md += "\n"
    return {"id": "module-documentation", "title": "Module documentation", "category": "Modules", "markdown": md}


def _apis(model: dict, insights: dict) -> dict:
    md = "# API & interface documentation\n\n## Documented APIs\n\n"
    if not model["apis"]:
        md += "_No API specifications were uploaded._\n"
    for api in model["apis"].values():
        md += f"### {api['id']} — {api['title']} (v{api.get('version') or '?'})\n\n{api['description']}\n\n"
        md += f"Backed by legacy program(s): **{', '.join(api['backing_programs']) or 'not declared'}** · source {_ref(api['path'], api['line'])}\n\n"
        md += _table(["Method", "Path", "Operation", "Summary"], [
            [o["method"], f"`{o['path']}`", o["operation_id"], o["summary"]] for o in api["operations"]
        ]) + "\n"
    md += "## Interfaces discovered in code\n\nThese are called from the code but have no specification in the uploaded sources.\n\n"
    rows = [[i["name"], i["type"], i["description"], ", ".join(i["used_by"])]
            for i in insights["external_interfaces"] if not i["documented"]]
    md += _table(["Interface", "Kind", "Description", "Used by"], rows)
    md += "\n## Program call interfaces\n\nSubprograms act as internal APIs. Their parameters are their contract.\n\n"
    rows = []
    for name, p in sorted(model["programs"].items()):
        if p["using_params"]:
            rows.append([name, ", ".join(p["using_params"]), ", ".join(insights["programs"][name]["callers"]), _ref(p["path"], 1)])
    md += _table(["Program", "Parameters", "Callers", "Source"], rows)
    return {"id": "api-documentation", "title": "API documentation", "category": "Interfaces", "markdown": md}


def _data_flow(model: dict, graph: dict, lineage: dict) -> dict:
    md = "# Data-flow documentation\n\n## Batch job flows\n\n"
    for job in model["jobs"].values():
        md += f"### Job {job['name']} — {job['description']}\n\nSource {_ref(job['path'], job['line'])}\n\n"
        for step in job["steps"]:
            runner = step["program"] or step.get("proc") or "?"
            md += f"1. **{step['name']}** runs `{runner}`"
            if step["is_system_program"]:
                md += " (system utility)"
            if step["calls"]:
                md += " → calls " + ", ".join(f"`{c['target']}`" for c in step["calls"])
            md += f" {_ref(job['path'], step['line'])}\n"
            for dd in step["dds"]:
                if dd["dsn"]:
                    md += f"   - DD `{dd['name']}` → dataset `{dd['dsn']}` (DISP={dd['disp'] or 'n/a'})\n"
        md += "\n"
    md += "## Column-level lineage\n\nWhere each database column flows after it is read, and where written columns come from.\n\n"
    by_source = defaultdict(list)
    for e in lineage["edges"]:
        if e["type"] == "DATA" and e["source"].startswith("column:"):
            by_source[e["source"][7:]].append((e["target"].split(":", 1)[1], e.get("how"), e["evidence"][:1]))
        if e["type"] == "DATA" and e["target"].startswith("column:"):
            by_source[e["target"][7:]].append(("← " + e["source"].split(":", 1)[1], e.get("how"), e["evidence"][:1]))
    rows = []
    for col in sorted(by_source):
        for tgt, how, ev in by_source[col]:
            rows.append([col, tgt, how, _ref(ev[0]["file"], ev[0]["line"]) if ev else ""])
    md += _table(["Column", "Flows to / from", "How", "Source"], rows)
    md += "\n## Dataset flows\n\n"
    rows = []
    for e in graph["edges"]:
        if e["source"].startswith("dataset:") or e["target"].startswith("dataset:"):
            ev = e["evidence"][0] if e["evidence"] else {}
            rows.append([e["source"].split(":", 1)[1], e["type"].replace("_", " ").lower(), e["target"].split(":", 1)[1],
                         _ref(ev.get("file"), ev.get("line"))])
    md += _table(["From", "Relation", "To", "Source"], rows)
    return {"id": "data-flow-documentation", "title": "Data-flow documentation", "category": "Data", "markdown": md}


def _rules_doc(rules: list[dict]) -> dict:
    md = "# Business-rule documentation\n\nRules are extracted deterministically from IF / EVALUATE logic, calculations and condition names. "
    md += "Each rule links to the exact source lines so it can be reviewed and signed off before migration.\n\n"
    by_prog = defaultdict(list)
    for r in rules:
        if not r["is_technical"]:
            by_prog[r["program"]].append(r)
    for prog in sorted(by_prog):
        md += f"## {prog}\n\n"
        md += _table(["ID", "Category", "Rule", "Source"], [
            [r["id"], r["category"], r["description"], _ref(r["file"], r["start_line"])] for r in by_prog[prog]
        ]) + "\n"
    tech = [r for r in rules if r["is_technical"]]
    md += f"## Technical checks\n\n{len(tech)} technical checks (SQL status, return codes, end-of-file) were excluded from the business view.\n"
    return {"id": "business-rule-documentation", "title": "Business-rule documentation", "category": "Rules", "markdown": md}


def _database(model: dict, graph: dict) -> dict:
    md = "# Database mapping\n\n"
    users = defaultdict(lambda: defaultdict(set))
    for e in graph["edges"]:
        if e["type"] == "READ_BY" and e["source"].startswith("column:"):
            users[e["source"][7:]][e["target"].split(":", 1)[1]].add("R")
        if e["type"] == "WRITES" and e["target"].startswith("column:"):
            users[e["target"][7:]][e["source"].split(":", 1)[1]].add("W")
    tables = sorted({n["label"] for n in graph["nodes"] if n["type"] == "table"})
    for t in tables:
        info = model["tables"].get(t)
        md += f"## {t}\n\n"
        if not info:
            md += "_Referenced in code but no DDL was uploaded._\n\n"
            cols = sorted({c.split(".", 1)[1] for c in users if c.startswith(t + ".")})
            md += _table(["Column", "Used by"], [[c, ", ".join(f"{p} ({''.join(sorted(m))})" for p, m in users[f'{t}.{c}'].items())] for c in cols]) + "\n"
            continue
        md += f"Defined at {_ref(info['path'], info['line'])}\n\n"
        rows = []
        for c in info["columns"]:
            u = users.get(f"{t}.{c['name']}", {})
            rows.append([c["name"], c["type"], "PK" if c["primary_key"] else "", "" if c["nullable"] else "NOT NULL",
                         ", ".join(f"{p} ({''.join(sorted(m))})" for p, m in sorted(u.items())), _ref(info["path"], c["line"])])
        md += _table(["Column", "Type", "Key", "Null", "Used by (R=read, W=write)", "Source"], rows)
        if info["foreign_keys"]:
            md += "\nForeign keys: " + "; ".join(f"{fk['column']} → {fk['ref_table']}.{fk['ref_column']}" for fk in info["foreign_keys"]) + "\n"
        md += "\n"
    md += "## CRUD matrix\n\n"
    crud = defaultdict(lambda: defaultdict(set))
    for blockowner, p in model["programs"].items():
        for b in p["sql_blocks"]:
            a = b["analysis"]
            letter = {"SELECT": "R", "DECLARE": "R", "FETCH": "R", "INSERT": "C", "UPDATE": "U", "DELETE": "D"}.get(a["op"])
            if not letter:
                continue
            for t in a.get("tables_read", []) + a.get("tables_written", []):
                crud[blockowner][t].add(letter if t in a.get("tables_written", []) or letter == "R" else "R")
    for name, r in model["routines"].items():
        for st in r["statements"]:
            letter = {"SELECT": "R", "INSERT": "C", "UPDATE": "U", "DELETE": "D"}.get(st["op"], "R")
            for t in st.get("tables_written", []):
                crud[name][t].add(letter)
            for t in st.get("tables_read", []):
                crud[name][t].add("R")
    owners = sorted(crud)
    md += _table(["Table"] + owners, [[t] + ["".join(sorted(crud[o].get(t, set()), key="CRUD".index)) for o in owners] for t in tables])
    return {"id": "database-mapping", "title": "Database mapping", "category": "Data", "markdown": md}


def _dependencies(model: dict, graph: dict, insights: dict, progs: dict) -> dict:
    md = "# Dependency documentation\n\n## Call hierarchy\n\n"
    calls = defaultdict(list)
    for e in graph["edges"]:
        if e["type"] == "CALLS":
            ev = e["evidence"][0] if e["evidence"] else {}
            calls[e["source"].split(":", 1)[1]].append((e["target"].split(":", 1)[1], ev))
    roots = [n for n, s in sorted(progs.items()) if not s["callers"]]

    def tree(name: str, depth: int, seen: set) -> str:
        out = ""
        for callee, ev in calls.get(name, []):
            out += "  " * depth + f"- {callee} {_ref(ev.get('file'), ev.get('line'))}\n"
            if callee not in seen:
                out += tree(callee, depth + 1, seen | {callee})
        return out

    for r in roots:
        md += f"- **{r}**\n" + tree(r, 1, {r})
    md += "\n## Program dependencies\n\n"
    md += _table(["Program", "Criticality", "Fan-in", "Fan-out", "Jobs", "Tables read", "Tables written"], [
        [n, s.get("criticality"), s["fan_in"], s["fan_out"], ", ".join(s["jobs"]), ", ".join(s["tables_read"]),
         ", ".join(s["tables_written"])] for n, s in sorted(progs.items())
    ])
    md += "\n## Critical programs\n\n"
    crit = [s for s in progs.values() if s.get("criticality") in ("Critical", "High")]
    md += _table(["Program", "Level", "Why"], [[s["program"], s["criticality"], "; ".join(s["criticality_reasons"])]
                                               for s in sorted(crit, key=lambda x: -x["criticality_score"])])
    md += "\n## Unused and low-use components\n\n"
    md += _table(["Type", "Name", "Reason", "Source"], [
        [u["type"], u["name"], u["reason"], _ref(u.get("file"), u.get("line"))] for u in insights["unused_components"]
    ])
    return {"id": "dependency-documentation", "title": "Dependency documentation", "category": "Dependencies", "markdown": md}
