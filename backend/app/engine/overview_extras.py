"""Extra Overview facts, all read straight from the analysis (no AI):

- sensitive_data: where personal / financial fields live and which programs touch them
- data_hotspots: tables that several programs update (shared ownership = conflict risk)
- snapshot / diff_snapshots: what changed since the previous analysis
"""
import re
from collections import defaultdict

# Each category: tokens that mark a field as sensitive. A field name is split into words
# (CUSTOMER_NAME -> CUSTOMER, NAME; custEmail -> CUST, EMAIL) and matched word by word.
_PERSON_WORDS = {"CUSTOMER", "CUST", "CLIENT", "FIRST", "LAST", "MIDDLE", "FULL", "HOLDER", "PAYEE",
                 "EMPLOYEE", "EMP", "BENEFICIARY", "MEMBER", "OWNER", "PATIENT", "USER", "PERSON", "CONTACT"}
_NOT_ADDRESS = {"IP", "MAC", "URL", "HOST", "WEB", "SERVER", "MEMORY", "MEM"}

CATEGORIES = [
    ("personal_name", "Personal names"),
    ("address", "Postal addresses"),
    ("contact", "Email and phone"),
    ("government_id", "Tax and government IDs"),
    ("birth_date", "Dates of birth"),
    ("card", "Card numbers"),
    ("bank_account", "Bank account numbers"),
    ("credential", "Passwords and PINs"),
]
LABELS = dict(CATEGORIES)


def _words(name: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return [w for w in re.split(r"[^A-Za-z0-9]+", spaced.upper()) if w]


def classify(name: str) -> str | None:
    """The sensitive-data category of a field name, or None."""
    w = _words(name)
    s = set(w)
    joined = "".join(w)
    if s & {"PASSWORD", "PASSWD", "PWD", "PIN", "PASSCODE", "SECRET"} or "PASSWORD" in joined:
        return "credential"
    if s & {"CVV", "CVC", "CVV2"} or ("CARD" in s and s & {"NO", "NUM", "NUMBER", "NBR", "PAN"}) or "CARDNUMBER" in joined:
        return "card"
    if s & {"SSN", "SIN", "TIN", "PAN", "AADHAAR", "AADHAR", "PASSPORT", "NINO"} or {"TAX", "ID"} <= s \
            or {"NATIONAL", "ID"} <= s or ({"DRIVER", "DRIVING"} & s and s & {"LICENSE", "LICENCE"}):
        return "government_id"
    if s & {"DOB", "BIRTHDATE", "BIRTHDAY"} or "BIRTH" in s or "DATEOFBIRTH" in joined:
        return "birth_date"
    if s & {"EMAIL", "PHONE", "MOBILE", "CELL", "TELEPHONE"} or "EMAIL" in joined:
        return "contact"
    if s & {"ADDRESS", "ADDR", "STREET", "ZIP", "ZIPCODE", "POSTCODE", "POSTAL"} and not s & _NOT_ADDRESS:
        return "address"
    if s & {"IBAN", "ROUTING", "SORTCODE"} or ({"ACCOUNT", "ACCT", "ACC"} & s and s & {"ID", "NO", "NUM", "NUMBER", "NBR"}):
        return "bank_account"
    if "NAME" in s and (s & _PERSON_WORDS) or s & {"SURNAME", "FNAME", "LNAME", "FIRSTNAME", "LASTNAME", "FULLNAME"}:
        return "personal_name"
    return None


def sensitive_data(model: dict, graph: dict) -> dict:
    """Sensitive fields grouped by category, each with where it is stored and which programs use it."""
    edges = graph.get("edges", [])
    users_of: dict[str, set] = defaultdict(set)  # node id -> program/procedure labels that touch it
    for e in edges:
        if e["type"] in ("READ_BY", "INCLUDED_IN"):
            users_of[e["source"]].add(e["target"].split(":", 1)[1])
        elif e["type"] == "WRITES":
            users_of[e["target"]].add(e["source"].split(":", 1)[1])

    found: dict[str, dict] = {}

    def add(cat, key, field, where, kind, file, line, users):
        item = found.setdefault(key, {"category": cat, "field": field, "where": where, "kind": kind,
                                      "file": file, "line": line, "used_by": set()})
        item["used_by"] |= set(users)

    # Database columns (declared in DDL, or referenced by SQL in code)
    declared = {name: t for name, t in model.get("tables", {}).items()}
    for n in graph.get("nodes", []):
        if n["type"] != "column":
            continue
        table, col = n["label"].split(".", 1) if "." in n["label"] else (n.get("table"), n["label"])
        cat = classify(col)
        if not cat:
            continue
        users = users_of.get(n["id"], set()) | users_of.get(f"table:{table}", set())
        file, line = n.get("file") or (declared.get(table) or {}).get("path"), n.get("line")
        add(cat, f"col:{table}.{col}", col, table, "table", file, line, users)

    # Copybook records shared between programs
    for name, cb in model.get("copybooks", {}).items():
        for d in cb.get("data_items", []):
            cat = classify(d["name"])
            if cat:
                add(cat, f"cb:{name}.{d['name']}", d["name"], name, "copybook", cb.get("path"), d.get("line"),
                    users_of.get(f"copybook:{name}", set()))

    # Fields declared inside programs (COBOL working storage, Java/C# fields, ...)
    for name, p in model.get("programs", {}).items():
        for d in p.get("data_items", []):
            cat = classify(d["name"])
            if cat:
                add(cat, f"pg:{name}.{d['name']}", d["name"], name, "program", p.get("path"), d.get("line"), {name})

    groups = []
    for cat, label in CATEGORIES:
        items = [dict(i, used_by=sorted(i["used_by"])) for i in found.values() if i["category"] == cat]
        if items:
            items.sort(key=lambda i: (i["kind"] != "table", i["where"], i["field"]))
            groups.append({"key": cat, "label": label, "count": len(items), "fields": items})
    tables = sorted({i["where"] for i in found.values() if i["kind"] == "table"})
    programs = sorted({u for i in found.values() for u in i["used_by"]})
    return {"fields": len(found), "tables": tables, "programs": programs, "categories": groups}


def data_hotspots(graph: dict, limit: int = 8) -> list[dict]:
    """Tables ranked by how many programs update them, then by how many read them."""
    writers: dict[str, set] = defaultdict(set)
    readers: dict[str, set] = defaultdict(set)
    for e in graph.get("edges", []):
        if e["type"] == "WRITES" and e["target"].startswith("table:"):
            writers[e["target"]].add(e["source"].split(":", 1)[1])
        elif e["type"] == "READ_BY" and e["source"].startswith("table:"):
            readers[e["source"]].add(e["target"].split(":", 1)[1])
    nodes = {n["id"]: n for n in graph.get("nodes", []) if n["type"] == "table"}
    rows = []
    for tid, n in nodes.items():
        w, r = writers.get(tid, set()), readers.get(tid, set())
        if not w and not r:
            continue
        rows.append({"table": n["label"], "file": n.get("file"), "line": n.get("line"),
                     "writers": sorted(w), "readers": sorted(r - w), "shared_write": len(w) >= 2})
    rows.sort(key=lambda x: (-len(x["writers"]), -(len(x["readers"]) + len(x["writers"])), x["table"]))
    return rows[:limit]


# --------------------------------------------------------------------------- change since last run

def snapshot(files: list[dict], graph: dict, rules: list[dict], fingerprint) -> dict:
    """A compact record of one analysis, small enough to keep and compare with the next one."""
    by_type: dict[str, list] = defaultdict(list)
    for n in graph.get("nodes", []):
        if n["type"] in ("program", "table", "job", "external", "procedure", "api"):
            by_type[n["type"]].append(n["label"])
    return {
        "files": {f["path"]: f["sha256"] for f in files},
        "components": {k: sorted(set(v)) for k, v in by_type.items()},
        "rules": {fingerprint(r): r.get("description") or r.get("id") for r in rules if not r.get("is_technical")},
    }


_KIND_LABEL = {"program": "programs", "table": "tables", "job": "batch jobs", "external": "outside systems",
               "procedure": "stored procedures", "api": "APIs"}


def diff_snapshots(prev: dict | None, cur: dict) -> dict:
    if not prev:
        return {"baseline": True}
    pf, cf = prev.get("files", {}), cur["files"]
    files = {"added": sorted(set(cf) - set(pf)), "removed": sorted(set(pf) - set(cf)),
             "changed": sorted(p for p in cf if p in pf and pf[p] != cf[p])}
    components = []
    for kind, label in _KIND_LABEL.items():
        before, after = set(prev.get("components", {}).get(kind, [])), set(cur["components"].get(kind, []))
        if before != after:
            components.append({"kind": kind, "label": label, "added": sorted(after - before), "removed": sorted(before - after)})
    pr, cr = prev.get("rules", {}), cur["rules"]
    rules = {"added": [cr[k] for k in cr if k not in pr], "removed": [pr[k] for k in pr if k not in cr]}
    nothing = not any(files.values()) and not components and not any(rules.values())
    return {"baseline": False, "unchanged": nothing, "files": files, "components": components,
            "rules": {"added": len(rules["added"]), "removed": len(rules["removed"]),
                      "added_sample": rules["added"][:5], "removed_sample": rules["removed"][:5]}}
