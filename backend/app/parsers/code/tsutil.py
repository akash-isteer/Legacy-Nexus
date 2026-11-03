"""Shared helpers for the tree-sitter based parsers.

A *unit* is one class (Java, C#, Python, TypeScript) or one module of top-level functions (Python,
JavaScript). Units use the same dictionary shape as a parsed COBOL program, plus a few fields that the
cross-file resolution pass (app/engine/resolve.py) needs: raw calls, field types, type references.
"""
from __future__ import annotations

import re
from functools import lru_cache

from app.parsers.cobol import CobolProgram
from app.parsers.sql import analyze_statement


class GrammarUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=None)
def parser(language: str):
    try:
        from tree_sitter_language_pack import get_parser
    except ImportError as exc:  # pragma: no cover - dependency missing
        raise GrammarUnavailable("tree-sitter-language-pack is not installed") from exc
    try:
        return get_parser(language)
    except Exception as exc:  # grammar not downloaded and no network (air-gapped install)
        raise GrammarUnavailable(f"tree-sitter grammar '{language}' is not available: {exc}") from exc


def parse_tree(language: str, text: str):
    return parser(language).parse(text.encode("utf-8"))


def txt(node) -> str:
    return node.text.decode("utf-8", errors="replace") if node is not None else ""


def line(node) -> int:
    return node.start_point[0] + 1


def end_line(node) -> int:
    return node.end_point[0] + 1


def one_line(text: str, limit: int = 240) -> str:
    return " ".join(text.split())[:limit]


def walk(node, stop: set[str] | None = None):
    """Depth-first pre-order traversal; does not descend into node types in ``stop`` (the stop node is yielded)."""
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        if stop and n.type in stop and n is not node:
            continue
        stack.extend(reversed(n.children))


def field(node, name: str):
    return node.child_by_field_name(name) if node is not None else None


# ----------------------------------------------------------------------------- units


def new_unit(path: str, name: str, language: str, kind: str, start: int, end: int, total_lines: int) -> dict:
    """An empty unit with every key the downstream engine reads."""
    u = CobolProgram(path=path, program_id=name).to_dict()
    u.update({
        "language": language,
        "kind": kind,  # class | interface | enum | record | module
        "program_type": "service",
        "start_line": start,
        "end_line": end,
        "total_lines": total_lines,
        "qualified_name": name,
        "extends": [],          # base types / interfaces (simple names)
        "field_types": {},      # field / property name -> declared type (simple name)
        "raw_calls": [],        # unresolved call sites, resolved after all files are parsed
        "type_refs": [],        # [{type, line, how}] types this unit uses (new, fields, parameters, generics)
        "endpoints": [],        # REST operations implemented here
        "entry_point": False,   # runnable on its own (main method, __main__ block, script)
        "entity_table": None,   # ORM entity -> table name
        "entity_columns": [],   # [{name, line}]
        "repository_entity": None,  # Spring Data repository -> entity type
        "dbsets": {},           # EF DbContext property -> entity type
        "module_constants": {},
    })
    return u


def finalise(unit: dict, source_lines: list[str]) -> dict:
    lo, hi = unit["start_line"], unit["end_line"]
    body = source_lines[lo - 1:hi]
    unit["loc"] = sum(1 for ln in body if ln.strip() and not _is_comment_line(ln))
    unit["comment_lines"] = sum(1 for ln in body if _is_comment_line(ln))
    unit["purpose"] = _sentence(unit.get("purpose") or "")
    if unit["endpoints"]:
        unit["program_type"] = "online"
    elif unit["entry_point"]:
        unit["program_type"] = "batch"
    ifs = sum(1 for d in unit["decisions"] if d["type"] == "IF")
    bool_ops = sum(len(re.findall(r"\b(AND|OR)\b", d.get("condition", ""))) for d in unit["decisions"])
    loops = unit["metrics"].pop("_loops", 0)
    unit["metrics"] = {
        "if_count": ifs, "evaluate_count": 0, "when_count": 0, "loop_count": loops,
        "cyclomatic_complexity": 1 + ifs + bool_ops + loops,
        "statement_count": len(unit["statements"]), "paragraph_count": len(unit["paragraphs"]),
        "sql_count": len(unit["sql_blocks"]), "call_count": len(unit["raw_calls"]),
    }
    return unit


def _is_comment_line(ln: str) -> bool:
    s = ln.strip()
    return s.startswith(("//", "/*", "*", "#", "///", '"""', "'''")) and not s.startswith("#!")


def _sentence(text: str) -> str:
    text = " ".join(text.split()).strip()
    if not text:
        return ""
    first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    return first if first.endswith((".", "!", "?")) else first + "."


def doc_text(raw: str) -> str:
    """Strip comment markers from a doc comment or docstring."""
    raw = re.sub(r"^\s*(/\*\*?|\*/|\*|///?|#|\"\"\"|''')", "", raw, flags=re.M)
    raw = raw.replace("*/", "").replace('"""', "").replace("'''", "")
    raw = re.sub(r"</?summary>", "", raw)
    return " ".join(raw.split())


def preceding_comment(node, source_lines: list[str]) -> str:
    """Comment lines directly above a node (doc comments, // or # comments)."""
    n = line(node) - 2
    parts = []
    while n >= 0:
        s = source_lines[n].strip()
        if s.startswith(("//", "#", "*", "/*", "///")) and not s.startswith("#!"):
            parts.insert(0, s)
            n -= 1
            continue
        if s.startswith("@") or s.startswith("["):  # annotations / attributes between doc and declaration
            n -= 1
            continue
        break
    return doc_text("\n".join(parts))


# ----------------------------------------------------------------------------- statements


_SQL_START = re.compile(r"^\s*(SELECT|INSERT\s+INTO|UPDATE\s+\w|DELETE\s+FROM|MERGE\s+INTO|WITH\s+\w+\s+AS|CALL\s+\w)",
                        re.I)
_PLACEHOLDERS = re.compile(r"%s|%\(\w+\)s|\$\d+|\{\d+\}|@\w+|(?<![:\w]):\w+")


def looks_like_sql(text: str) -> bool:
    return bool(_SQL_START.match(text)) and len(text) < 20_000


def sql_block(text: str, node, paragraph: str | None) -> dict:
    """An embedded-SQL record (same shape as COBOL EXEC SQL blocks) for a SQL string literal."""
    clean = _PLACEHOLDERS.sub("?", " ".join(text.split()))
    analysis = analyze_statement(clean).to_dict()
    return {"kind": "SQL", "start_line": line(node), "end_line": end_line(node), "text": clean,
            "ends_sentence": True, "analysis": analysis, "paragraph": paragraph}


_OPERATORS = [
    (r"===|==", " = "), (r"!==|!=", " NOT = "), (r"&&", " AND "), (r"\|\|", " OR "),
    (r"\band\b", " AND "), (r"\bor\b", " OR "), (r"\bis\s+not\b", " NOT = "), (r"(?<![=<>!])!(?!=)", " NOT "),
    (r"\bnot\b", " NOT "),
]


def normalize_condition(text: str) -> str:
    """Rewrite code operators into the vocabulary the rule engine understands (=, NOT =, AND, OR)."""
    text = one_line(text, 400)
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    for pattern, repl in _OPERATORS:
        text = re.sub(pattern, repl, text)
    return " ".join(text.split())


def action(node) -> dict:
    t = one_line(txt(node)).rstrip(";")
    verb = (re.match(r"[A-Za-z_]+", t) or [""])[0].upper() or "STATEMENT"
    return {"text": t, "line": line(node), "verb": verb}


def statements_in(block) -> list:
    """Direct child statements of a block (or the single statement if it is not a block)."""
    if block is None:
        return []
    if block.type in {"block", "statement_block", "compound_statement"}:
        return [c for c in block.named_children if not c.type.endswith("comment")]
    return [block]


def decision(node, cond_node, then_node, else_node, paragraph: str | None, comment: str) -> dict:
    else_actions: list[dict] = []
    if else_node is not None:
        inner = else_node
        if inner.type in {"else_clause"}:
            body = field(inner, "body") or (inner.named_children[0] if inner.named_children else None)
            inner = body
        else_actions = [action(s) for s in statements_in(inner)] if inner is not None else []
    return {
        "type": "IF", "condition": normalize_condition(txt(cond_node)), "start_line": line(node),
        "end_line": end_line(node), "paragraph": paragraph,
        "then": [action(s) for s in statements_in(then_node)], "else": else_actions,
        "comment": _sentence(comment) if comment else "", "context": [],
    }


_ARITH = re.compile(r"[+\-*/%]")


def calculation(target: str, expression: str, node, paragraph: str | None) -> dict | None:
    expr = one_line(expression, 300)
    if not _ARITH.search(re.sub(r"(['\"]).*?\1", "", expr)):
        return None
    return {"target": target, "expression": expr, "line": line(node), "end_line": end_line(node),
            "paragraph": paragraph}


def literal_value(text: str) -> str | None:
    """A constant's value when it is a plain number or string literal (used to explain rules)."""
    t = text.strip()
    m = re.fullmatch(r"new\s+BigDecimal\(\s*\"([-+]?\d+(?:\.\d+)?)\"\s*\)", t)
    if m:
        return m.group(1)
    t = re.sub(r"(?<=\d)[lLfFdDmM]$", "", t).replace("_", "")
    if re.fullmatch(r"[-+]?\d+(\.\d+)?", t):
        return t
    if re.fullmatch(r"(['\"]).*\1", t):
        return t
    return None


def data_item(name: str, value: str | None, node, section: str = "FIELDS") -> dict:
    return {"level": 1, "name": name, "pic": None, "value": value, "usage": None, "section": section,
            "parent": None, "file": None, "line": line(node), "redefines": None}


def simple_type(type_text: str) -> str | None:
    """'List<Account>' -> 'List', 'com.x.AccountService' -> 'AccountService', 'Account[]' -> 'Account'."""
    t = re.sub(r"<.*>|\[.*\]|\?|\s", "", type_text or "")
    t = t.split(".")[-1]
    return t if re.fullmatch(r"[A-Za-z_]\w*", t) else None


def snake_upper(name: str) -> str:
    """ORM default column naming: 'lowBalance' -> 'LOW_BALANCE'."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).upper()


def generic_args(type_text: str) -> list[str]:
    m = re.search(r"<(.+)>", type_text or "")
    if not m:
        return []
    return [s for s in (simple_type(p) for p in re.split(r"[,<>]", m.group(1))) if s]


HTTP_VERBS = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}


def join_paths(base: str, sub: str) -> str:
    path = "/" + "/".join(p.strip("/") for p in (base or "", sub or "") if p and p.strip("/"))
    return path if path != "" else "/"


def path_params(path: str) -> list[dict]:
    names = re.findall(r"\{(\w+)(?::[^}]*)?\}|:(\w+)|<(?:\w+:)?(\w+)>", path)
    return [{"name": next(x for x in n if x), "in": "path", "required": True} for n in names]


def endpoint(method: str, path: str, handler: str, unit: str, node, params: list[dict] | None = None) -> dict:
    in_path = path_params(path)
    names = {p["name"] for p in in_path}
    params = in_path + [p for p in (params or []) if p["name"] not in names]
    return {"method": method.upper(), "path": path or "/", "handler": handler, "unit": unit, "line": line(node),
            "params": params}
