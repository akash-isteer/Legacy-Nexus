"""Business-rule extraction from decisions, calculations and condition names. No LLM involved."""
from __future__ import annotations

import re

from app.parsers.cobol import identifiers

TECHNICAL = re.compile(r"\bSQLCODE\b|\bSQLSTATE\b|RETURN-CODE|-RC\b|\bEOF\b|FILE-STATUS|END-OF-|\bFS-|-STATUS-CODE")
ERROR_ACTION = re.compile(r"MOVE\s+\S+\s+TO\s+\S*(RETURN-CODE|-RC|ERROR|ERR-|REASON)|REJECT|ERROR|ABEND|HOLD"
                          r"|\bthrow\b|\braise\b", re.I)
COMPARE = re.compile(r"(<=|>=|<|>|\bGREATER\b|\bLESS\b)")

_CODE_NAME = re.compile(r"(?<![\w.$])([A-Za-z_]\w*)(?![\w(])")
_CODE_KEYWORDS = {
    "null", "None", "nil", "undefined", "true", "false", "True", "False", "new", "this", "self", "return",
    "throw", "raise", "is", "in", "not", "and", "or", "AND", "OR", "NOT", "typeof", "instanceof", "var", "let",
    "const", "await", "async", "lambda", "string", "int", "bool", "void",
}


def code_identifiers(text: str) -> list[str]:
    """Variable and constant names in a Java / C# / Python / JavaScript expression (not method calls)."""
    stripped = re.sub(r"'[^']*'|\"[^\"]*\"", " ", text)
    return list(dict.fromkeys(m.group(1) for m in _CODE_NAME.finditer(stripped)
                              if m.group(1) not in _CODE_KEYWORDS and not m.group(1).isdigit()))

_OPS = [
    (r"\bNOT\s*=\s*", " does not equal "), (r"\bNOT\s+EQUAL\s+(TO\s+)?", " does not equal "),
    (r">=", " is at least "), (r"<=", " is at most "), (r"\bNOT\s*<\s*", " is at least "),
    (r"\bNOT\s*>\s*", " is at most "), (r"(?<![<>])=", " equals "), (r">", " is greater than "),
    (r"<", " is less than "), (r"\bGREATER\s+THAN\s+(OR\s+EQUAL\s+TO\s+)?", " is greater than "),
    (r"\bLESS\s+THAN\s+(OR\s+EQUAL\s+TO\s+)?", " is less than "), (r"\bAND\b", " and "), (r"\bOR\b", " or "),
    (r"\bSPACES?\b", "blank"), (r"\bZEROE?S?\b", "zero"), (r"\bNOT\s+NUMERIC\b", "is not numeric"),
    (r"\bNUMERIC\b", "is numeric"),
]


class RuleContext:
    """Program knowledge used to explain conditions in business terms."""

    def __init__(self, program: dict, column_of: dict[str, str]):
        self.program = program
        self.cobol = program.get("language", "cobol") == "cobol"
        self.ident = identifiers if self.cobol else code_identifiers
        self._edge = "A-Z0-9-" if self.cobol else r"\w-"
        self.items = {d["name"]: d for d in program["data_items"]}
        self.conditions = program["conditions"]
        self.column_of = column_of  # field name -> TABLE.COLUMN it is loaded from
        written = {f["dst"] for f in program["data_flows"]}
        for b in program["sql_blocks"]:
            written.update(b["analysis"].get("into_vars", []))
        self.written = written  # fields that change at runtime are not constants

    def is_business_action(self, action: dict) -> bool:
        """True when an action changes business data rather than only control/technical state."""
        t = action["text"]
        m = re.match(r"(?:MOVE\s+.+?\s+TO|COMPUTE|ADD\s+.+?\s+TO|SUBTRACT\s+.+?\s+FROM)\s+([A-Z0-9-]+)", t)
        return bool(m) and not TECHNICAL.search(m.group(1))

    def describe_name(self, name: str) -> str:
        if name in self.conditions:
            c = self.conditions[name]
            vals = " or ".join(c["values"]) or "?"
            return f"{c['parent']} is {vals} ({name})"
        label = name
        item = self.items.get(name)
        if (item and item.get("value") and name not in self.written
                and re.fullmatch(r"[-+]?\d+(\.\d+)?", item["value"])):
            label = f"{name} ({item['value']})"
        if name in self.column_of:
            label = f"{label} [{self.column_of[name]}]"
        return label

    def humanize(self, condition: str) -> str:
        text = f" {condition} "
        names = sorted(self.ident(condition), key=len, reverse=True)
        edge = self._edge
        for i, name in enumerate(names):  # hide data names so operator rewriting cannot touch them
            text = re.sub(rf"(?<![{edge}]){re.escape(name)}(?![{edge}])", f"\u0000{i}\u0001", text)
        for pattern, repl in _OPS:
            text = re.sub(pattern, repl, text)
        text = re.sub("\u0000(\\d+)\u0001", lambda m: self.describe_name(names[int(m.group(1))]), text)
        return " ".join(text.split())

    def action(self, a: dict) -> str:
        t = a["text"]
        m = re.match(r"MOVE\s+(.+?)\s+TO\s+(.+)$", t)
        if m:
            return f"set {', '.join(identifiers(m.group(2)) or [m.group(2)])} to {self._value(m.group(1))}"
        m = re.match(r"COMPUTE\s+(\S+)(?:\s+ROUNDED)?\s*=\s*(.+)$", t)
        if m:
            return f"calculate {m.group(1)} = {m.group(2)}"
        m = re.match(r"(ADD|SUBTRACT)\s+(.+?)\s+(TO|FROM)\s+(.+)$", t)
        if m:
            return f"{m.group(1).lower()} {self._value(m.group(2))} {m.group(3).lower()} {m.group(4)}"
        m = re.match(r"PERFORM\s+(\S+)", t)
        if m:
            return f"run {m.group(1)}"
        m = re.match(r"CALL\s+'?([A-Z0-9-]+)'?", t)
        if m:
            return f"call program {m.group(1)}"
        if t.startswith("IF "):
            return f"check whether {self.humanize(t[3:])}"
        if t.startswith("EXEC SQL"):
            return "execute " + t[5:].lower().replace("sql ", "SQL ")
        if t == "CONTINUE":
            return "take no action"
        return t.lower() if self.cobol else t  # code is case-sensitive: keep it as written

    def _value(self, text: str) -> str:
        text = text.strip()
        if text.startswith(("'", '"')) or re.fullmatch(r"[-+]?\d+(\.\d+)?", text):
            return text
        return self.describe_name(text)


def extract_rules(model: dict, lineage: dict) -> list[dict]:
    column_sources: dict[str, dict[str, str]] = {}
    for e in lineage["edges"]:
        if e["type"] == "DATA" and e["source"].startswith("column:") and e["target"].startswith("field:"):
            prog, fld = e["target"][6:].split(".", 1)
            column_sources.setdefault(prog, {})[fld] = e["source"][7:]

    rules: list[dict] = []
    for name in sorted(model["programs"]):
        p = model["programs"][name]
        ctx = RuleContext(p, column_sources.get(name, {}))
        covered_lines: set[int] = set()

        for d in p["decisions"]:
            if d["type"] == "IF":
                rule = _if_rule(d, p, ctx)
            else:
                rule = _evaluate_rule(d, p, ctx)
            rules.append(rule)
            covered_lines.update(a["line"] for a in d.get("then", []) + d.get("else", []))

        for calc in p["calculations"]:
            if not calc["expression"] or calc["target"] is None:
                continue
            context = _branch_context(p["decisions"], calc["line"], ctx)
            fields = ctx.ident(calc["expression"]) + [calc["target"]]
            expr = calc["expression"]
            arithmetic = expr.startswith(("ADD", "SUBTRACT", "MULTIPLY", "DIVIDE"))
            if not arithmetic:
                expr = f"{calc['target']} = {expr}"
            explained = ", ".join(ctx.describe_name(f) for f in ctx.ident(calc["expression"]) if ctx.describe_name(f) != f)
            if arithmetic:
                desc = ctx.action({"text": expr})
                desc = desc[:1].upper() + desc[1:]
            else:
                desc = f"Calculates {expr}"
            if explained:
                desc += f" using {explained}"
            if context:
                desc += f", when {' and '.join(context)}"
            rules.append(_rule(p, "calculation", "Calculation", desc + ".", calc["line"], calc["end_line"],
                               calc.get("paragraph"), fields, technical=bool(TECHNICAL.search(expr)),
                               condition=None, actions=[expr]))

        parents: dict[str, list[tuple[str, dict]]] = {}
        for cname, c in p["conditions"].items():
            parents.setdefault(c["parent"], []).append((cname, c))
        for parent, conds in parents.items():
            if not parent:
                continue
            col = ctx.column_of.get(parent)
            values = "; ".join(f"{' / '.join(c['values'])} = {cname}" for cname, c in conds)
            target = f"{parent} [{col}]" if col else parent
            desc = f"Allowed values of {target}: {values}."
            lines = [c["line"] for _, c in conds]
            rules.append(_rule(p, "domain", "Domain values", desc, min(lines), max(lines), None,
                               [parent] + [n for n, _ in conds], technical=bool(TECHNICAL.search(parent)),
                               condition=None, actions=[]))

    rules.sort(key=lambda r: (r["program"], r["start_line"]))
    for i, r in enumerate(rules, start=1):
        r["id"] = f"BR-{i:03d}"
    return rules


def _rule(p, kind, category, description, start, end, paragraph, fields, technical, condition, actions,
          intent="", branches=None) -> dict:
    return {
        "id": "",
        "program": p["program_id"],
        "file": p["path"],
        "paragraph": paragraph,
        "start_line": start,
        "end_line": end,
        "kind": kind,
        "category": "Technical" if technical else category,
        "is_technical": technical,
        "intent": intent,
        "description": description,
        "condition": condition,
        "actions": actions,
        "branches": branches or [],
        "fields": list(dict.fromkeys(fields)),
    }


def _branch_context(decisions: list[dict], line: int, ctx: RuleContext) -> list[str]:
    """Plain-language conditions under which a given line executes (IF branches and EVALUATE arms)."""
    out = []
    for d in decisions:
        if not (d["start_line"] < line <= d["end_line"]):
            continue
        if d["type"] == "IF":
            else_lines = [a["line"] for a in d["else"]]
            in_else = bool(else_lines) and line >= min(else_lines)
            text = ctx.humanize(d["condition"])
            out.append(f"not ({text})" if in_else else text)
        else:
            arm = None
            for w in d["whens"]:
                if w["line"] <= line:
                    arm = w
            if arm:
                value = "any other value" if arm["value"] == "OTHER" else arm["value"]
                out.append(f"{ctx.describe_name(d['subject'])} is {value}")
    return out


def _if_rule(d: dict, p: dict, ctx: RuleContext) -> dict:
    cond = d["condition"]
    then = [ctx.action(a) for a in d["then"]]
    els = [ctx.action(a) for a in d["else"]]
    # A return-code check that sets business data (e.g. a payment status) is still a business rule.
    technical = bool(TECHNICAL.search(cond)) and not any(ctx.is_business_action(a) for a in d["then"] + d["else"])
    all_actions = " ".join(a["text"] for a in d["then"] + d["else"])
    if ERROR_ACTION.search(all_actions):
        category = "Validation"
    elif COMPARE.search(cond) and re.search(r"\d|[A-Z]+[-_](LIMIT|THRESHOLD|MIN|MAX)|(MIN|MAX)[-_]", cond + " " + " ".join(
            ctx.describe_name(n) for n in ctx.ident(cond))):
        category = "Threshold"
    else:
        category = "Decision"
    when = ctx.humanize(cond)
    outer = _branch_context([x for x in p["decisions"] if x is not d], d["start_line"], ctx)
    if outer:
        when = f"{when} (within: {'; '.join(outer)})"
    desc = f"When {when}: {', then '.join(then) or 'no action'}."
    if els:
        desc += f" Otherwise: {', then '.join(els)}."
    if d.get("comment"):
        desc = f"{d['comment']} {desc}"
    return _rule(p, "decision", category, desc, d["start_line"], d["end_line"], d["paragraph"],
                 ctx.ident(cond), technical, cond, [a["text"] for a in d["then"]] + (
                     ["ELSE"] + [a["text"] for a in d["else"]] if d["else"] else []), intent=d.get("comment", ""))


def _evaluate_rule(d: dict, p: dict, ctx: RuleContext) -> dict:
    branches = []
    for w in d["whens"]:
        actions = [ctx.action(a) for a in w["actions"]]
        label = "any other value" if w["value"] == "OTHER" else w["value"]
        branches.append({"when": label, "line": w["line"], "actions": actions})
    parts = [f"{b['when']} → {', '.join(b['actions']) or 'no action'}" for b in branches]
    subject = ctx.describe_name(d["subject"]) if d["subject"] in ctx.items else d["subject"]
    desc = f"Depending on {subject}: " + "; ".join(parts) + "."
    if d.get("comment"):
        desc = f"{d['comment']} {desc}"
    technical = bool(TECHNICAL.search(d["subject"]))
    return _rule(p, "classification", "Classification", desc, d["start_line"], d["end_line"], d["paragraph"],
                 ctx.ident(d["subject"]), technical, f"EVALUATE {d['subject']}",
                 [f"WHEN {b['when']}" for b in branches], intent=d.get("comment", ""), branches=branches)
