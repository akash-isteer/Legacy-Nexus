"""Structural COBOL parser.

Handles fixed-format (sequence area, indicator column, area A/B) and free-format sources. It recovers:
divisions, paragraphs, data items (with levels, PIC, VALUE, 88-level conditions), file control, COPY members,
EXEC SQL / EXEC CICS blocks, CALL / PERFORM edges, data movement (MOVE/COMPUTE/arithmetic), and decision
logic (IF / EVALUATE) used for business-rule extraction and control-flow lineage. Every fact keeps line numbers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.parsers.sql import analyze_statement

VERBS = {
    "ACCEPT", "ADD", "ALTER", "CALL", "CANCEL", "CLOSE", "COMPUTE", "CONTINUE", "DELETE", "DISPLAY", "DIVIDE",
    "EVALUATE", "EXIT", "GO", "GOBACK", "IF", "INITIALIZE", "INSPECT", "MERGE", "MOVE", "MULTIPLY", "OPEN",
    "PERFORM", "READ", "RELEASE", "RETURN", "REWRITE", "SEARCH", "SET", "SORT", "START", "STOP", "STRING",
    "SUBTRACT", "UNSTRING", "WRITE", "EXEC", "ELSE", "WHEN", "NEXT", "XML", "JSON", "ALLOCATE", "FREE",
}
SCOPE_END = {
    "END-IF", "END-EVALUATE", "END-PERFORM", "END-READ", "END-CALL", "END-COMPUTE", "END-WRITE", "END-STRING",
    "END-UNSTRING", "END-SEARCH", "END-EXEC", "END-START", "END-DELETE", "END-REWRITE", "END-RETURN", "END-ADD",
    "END-SUBTRACT", "END-MULTIPLY", "END-DIVIDE", "END-ACCEPT", "END-DISPLAY",
}
STARTERS = VERBS | SCOPE_END
# Verbs that may legitimately appear embedded after a condition on the same line.
EMBEDDABLE = VERBS - {"ELSE", "WHEN", "NEXT", "EXEC"}

NON_PARAGRAPH = SCOPE_END | VERBS | {"END", "DECLARATIVES", "EJECT", "SKIP1", "SKIP2", "SKIP3"}

RESERVED = {
    "TO", "FROM", "BY", "INTO", "GIVING", "ROUNDED", "AND", "OR", "NOT", "IS", "ARE", "THAN", "EQUAL", "GREATER",
    "LESS", "SPACES", "SPACE", "ZERO", "ZEROS", "ZEROES", "HIGH-VALUES", "HIGH-VALUE", "LOW-VALUES", "LOW-VALUE",
    "QUOTE", "QUOTES", "ALL", "OF", "IN", "USING", "REFERENCE", "CONTENT", "VALUE", "OMITTED", "THRU", "THROUGH",
    "UNTIL", "VARYING", "TIMES", "AFTER", "BEFORE", "WITH", "TEST", "AT", "END", "ON", "SIZE", "ERROR", "OTHER",
    "TRUE", "FALSE", "ALSO", "ANY", "THEN", "NUMERIC", "ALPHABETIC", "POSITIVE", "NEGATIVE", "RUN", "SENTENCE",
    "INPUT", "OUTPUT", "I-O", "EXTEND", "RECORD", "KEY", "INVALID", "CORRESPONDING", "CORR", "FUNCTION",
    "LENGTH", "ADDRESS", "RETURNING", "UPON", "CONSOLE", "DELIMITED", "POINTER", "TALLYING", "REPLACING",
    "LEADING", "FIRST", "INITIAL", "CHARACTERS", "SQLCA", "PROGRAM", "COMMAREA", "SQL",
} | STARTERS

_IDENT = re.compile(r"(?<![A-Z0-9'\"-])([A-Z][A-Z0-9-]*[A-Z0-9]|[A-Z])(?![A-Z0-9-])")
_LITERAL = re.compile(r"'[^']*'|\"[^\"]*\"")
_NUMBER = re.compile(r"^[-+]?\d+(\.\d+)?$")


@dataclass
class SourceLine:
    number: int
    raw: str
    code: str  # upper-cased program text (no sequence/indicator area, no inline comments)
    is_comment: bool
    comment: str
    area_a: bool


def _strip_literals(text: str) -> str:
    return _LITERAL.sub(" ", text)


def identifiers(text: str) -> list[str]:
    """Data-name candidates in a COBOL fragment (literals and reserved words removed)."""
    out = []
    for m in _IDENT.finditer(_strip_literals(text.upper())):
        word = m.group(1)
        if word not in RESERVED and not _NUMBER.match(word):
            out.append(word)
    return list(dict.fromkeys(out))


def split_lines(text: str) -> list[SourceLine]:
    raw_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    candidates = [ln for ln in raw_lines if ln.strip()]
    fixed_votes = sum(1 for ln in candidates if len(ln) >= 7 and re.match(r"^[\d ]{6}", ln))
    fixed = bool(candidates) and fixed_votes / len(candidates) >= 0.8

    result: list[SourceLine] = []
    for i, raw in enumerate(raw_lines, start=1):
        raw = raw.expandtabs(4).rstrip()
        if fixed:
            indicator = raw[6] if len(raw) > 6 else " "
            body = raw[7:72] if len(raw) > 7 else ""
            if indicator in "*/":
                result.append(SourceLine(i, raw, "", True, raw[7:72].strip(" *-="), False))
                continue
            area_a = bool(body[:4].strip())
        else:
            stripped = raw.lstrip()
            if stripped.startswith("*>") or raw.startswith("*"):
                result.append(SourceLine(i, raw, "", True, stripped.lstrip("*>").strip(" *-="), False))
                continue
            body = raw
            area_a = len(raw) - len(stripped) < 4 if stripped else False
        if "*>" in body:
            body = body.split("*>", 1)[0]
        result.append(SourceLine(i, raw, body.rstrip().upper(), False, "", area_a))
    return result


@dataclass
class Statement:
    verb: str
    text: str
    start_line: int
    end_line: int
    paragraph: str | None
    ends_sentence: bool = False


@dataclass
class CobolProgram:
    path: str
    program_id: str | None = None
    kind: str = "program"  # program | copybook
    program_type: str = "batch"  # batch | subprogram | online
    purpose: str = ""
    header_comment: str = ""
    loc: int = 0
    comment_lines: int = 0
    total_lines: int = 0
    paragraphs: list[dict] = field(default_factory=list)
    data_items: list[dict] = field(default_factory=list)
    conditions: dict[str, dict] = field(default_factory=dict)
    using_params: list[str] = field(default_factory=list)
    files: list[dict] = field(default_factory=list)
    copybooks: list[dict] = field(default_factory=list)
    sql_blocks: list[dict] = field(default_factory=list)
    cics: list[dict] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)
    performs: list[dict] = field(default_factory=list)
    data_flows: list[dict] = field(default_factory=list)
    file_ops: list[dict] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    calculations: list[dict] = field(default_factory=list)
    statements: list[dict] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    comments_by_line: dict[int, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["comments_by_line"] = {str(k): v for k, v in self.comments_by_line.items()}
        return d


# ----------------------------------------------------------------------------- helpers

def _first_word(code: str) -> str:
    parts = code.split()
    return parts[0].rstrip(".") if parts else ""


def _split_embedded(text: str) -> tuple[str, list[str]]:
    """Split 'IF A = B MOVE 1 TO C' into ('IF A = B', ['MOVE 1 TO C'])."""
    tokens = re.findall(r"'[^']*'|\"[^\"]*\"|\S+", text)
    head: list[str] = []
    rest: list[list[str]] = []
    for idx, tok in enumerate(tokens):
        word = tok.rstrip(".")
        if idx > 0 and word in EMBEDDABLE and not (head and head[-1] in {"NOT", "AT"}):
            rest.append([tok])
            continue
        if rest:
            rest[-1].append(tok)
        else:
            head.append(tok)
    return " ".join(head), [" ".join(r) for r in rest]


def _clean_condition(head: str, keyword: str) -> str:
    cond = head[len(keyword):].strip() if head.startswith(keyword) else head
    cond = re.sub(r"\bTHEN\b\s*$", "", cond).strip().rstrip(".")
    return " ".join(cond.split())


# ----------------------------------------------------------------------------- main entry

def parse_cobol(path: str, text: str, is_copybook: bool = False) -> CobolProgram:
    lines = split_lines(text)
    prog = CobolProgram(path=path, kind="copybook" if is_copybook else "program")
    prog.total_lines = len(lines)
    prog.comment_lines = sum(1 for ln in lines if ln.is_comment)
    prog.loc = sum(1 for ln in lines if not ln.is_comment and ln.code.strip())
    prog.comments_by_line = {ln.number: ln.comment for ln in lines if ln.is_comment and ln.comment}

    consumed: set[int] = set()
    _extract_exec_blocks(lines, prog, consumed)

    division = "DATA" if is_copybook else None
    header_comments: list[str] = []
    seen_code_after_id = False
    data_buffer: list[SourceLine] = []
    proc_lines: list[SourceLine] = []
    proc_header: list[str] = []
    in_proc_header = False
    data_section = None

    for ln in lines:
        if ln.is_comment:
            if not seen_code_after_id and division in (None, "IDENTIFICATION"):
                header_comments.append(ln.comment)
            continue
        code = ln.code.strip()
        if not code or ln.number in consumed:
            continue
        m = re.match(r"^(IDENTIFICATION|ID|ENVIRONMENT|DATA|PROCEDURE)\s+DIVISION\b", code)
        if m:
            division = {"ID": "IDENTIFICATION"}.get(m.group(1), m.group(1))
            if division != "IDENTIFICATION":
                seen_code_after_id = True
            if division == "PROCEDURE":
                in_proc_header = True
                proc_header.append(code)
                if code.endswith("."):
                    in_proc_header = False
            continue
        if in_proc_header:
            proc_header.append(code)
            if code.endswith("."):
                in_proc_header = False
            continue
        if division == "IDENTIFICATION":
            pm = re.match(r"PROGRAM-ID\.?\s+['\"]?([A-Z0-9][A-Z0-9-]*)", code)
            if pm:
                prog.program_id = pm.group(1)
            continue
        if division in ("ENVIRONMENT", "DATA"):
            _collect_copy(code, ln.number, prog)
            if re.match(r"^COPY\s", code):
                continue
            data_buffer.append(ln)
            if code.endswith("."):
                stmt_text = " ".join(x.code.strip() for x in data_buffer)
                data_section = _handle_data_statement(stmt_text, data_buffer[0].number, prog, division, data_section)
                data_buffer = []
            continue
        if division == "PROCEDURE":
            _collect_copy(code, ln.number, prog)
            proc_lines.append(ln)

    header = " ".join(c for c in header_comments if c and not re.fullmatch(r"[-=*\s]*", c))
    prog.header_comment = header
    pm = re.search(r"PURPOSE\s*:?\s*(.*)", header, re.I)
    prog.purpose = _sentence_case(pm.group(1) if pm else header)

    params = " ".join(proc_header)
    um = re.search(r"\bUSING\s+(.*?)(?:\bRETURNING\b|\.$|$)", params)
    if um:
        prog.using_params = [p for p in identifiers(um.group(1))]

    _parse_procedure(proc_lines, prog)
    _finalise(prog, is_copybook)
    return prog


def _sentence_case(text: str) -> str:
    text = " ".join(text.split()).strip(" .")
    if not text:
        return ""
    if text.isupper():
        text = text.lower()
        text = re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)
        # restore identifiers that are clearly technical names
        text = re.sub(r"\b([a-z0-9]+_[a-z0-9_]+)\b", lambda m: m.group(1).upper(), text)
        text = re.sub(r"\bmq\b", "MQ", text)
    return text + "."


def _collect_copy(code: str, line: int, prog: CobolProgram) -> None:
    for m in re.finditer(r"\bCOPY\s+['\"]?([A-Z0-9][A-Z0-9-]*)", code):
        prog.copybooks.append({"name": m.group(1), "line": line})


def _extract_exec_blocks(lines: list[SourceLine], prog: CobolProgram, consumed: set[int]) -> None:
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.is_comment or "EXEC " not in ln.code:
            i += 1
            continue
        m = re.search(r"\bEXEC\s+(SQL|CICS|DLI)\b(.*)", ln.code)
        if not m:
            i += 1
            continue
        kind = m.group(1)
        buf = [m.group(2)]
        start = ln.number
        j = i
        while "END-EXEC" not in buf[-1] and j + 1 < len(lines):
            j += 1
            if not lines[j].is_comment:
                buf.append(lines[j].code)
        end = lines[j].number
        body = " ".join(buf)
        tail = body.split("END-EXEC", 1)[1] if "END-EXEC" in body else ""
        body = body.split("END-EXEC", 1)[0]
        for k in range(i, j + 1):
            consumed.add(lines[k].number)
        block = {
            "kind": kind,
            "start_line": start,
            "end_line": end,
            "text": " ".join(body.split()),
            "ends_sentence": tail.strip().startswith("."),
        }
        if kind == "SQL":
            analysis = analyze_statement(block["text"])
            block["analysis"] = analysis.to_dict()
            if analysis.op == "INCLUDE" and analysis.include and analysis.include not in {"SQLCA", "SQLDA"}:
                prog.copybooks.append({"name": analysis.include, "line": start})
            prog.sql_blocks.append(block)
        else:
            block["command"] = block["text"].split(" ", 1)[0] if block["text"] else ""
            pm = re.search(r"PROGRAM\s*\(\s*['\"]?([A-Z0-9-]+)", block["text"])
            block["target_program"] = pm.group(1) if pm else None
            prog.cics.append(block)
        i = j + 1


def _handle_data_statement(text: str, line: int, prog: CobolProgram, division: str, section: str | None) -> str | None:
    text = text.rstrip(".").strip()
    sm = re.match(r"^([A-Z0-9-]+)\s+SECTION$", text)
    if sm:
        return sm.group(1)
    if division == "ENVIRONMENT":
        fm = re.match(r"SELECT\s+(?:OPTIONAL\s+)?([A-Z0-9-]+)\s+ASSIGN\s+(?:TO\s+)?['\"]?([A-Z0-9-]+)", text)
        if fm:
            prog.files.append({"name": fm.group(1), "assign": fm.group(2), "line": line, "records": [], "modes": []})
        return section
    fd = re.match(r"^(FD|SD)\s+([A-Z0-9-]+)", text)
    if fd:
        prog.metrics.setdefault("_current_fd", fd.group(2))
        prog.metrics["_current_fd"] = fd.group(2)
        return section
    lm = re.match(r"^(\d{1,2})\s+([A-Z0-9][A-Z0-9-]*)\b\s*(.*)$", text)
    if not lm:
        return section
    level, name, rest = int(lm.group(1)), lm.group(2), lm.group(3)
    pic = re.search(r"\bPIC(?:TURE)?\s+(?:IS\s+)?(\S+)", rest)
    val = re.search(r"\bVALUES?\s+(?:IS\s+|ARE\s+)?((?:'[^']*'|\"[^\"]*\"|[-+]?\d+(?:\.\d+)?|[A-Z-]+)(?:\s*(?:,|THRU|THROUGH)?\s*(?:'[^']*'|\"[^\"]*\"|[-+]?\d+(?:\.\d+)?))*)", rest)
    usage = re.search(r"\b(COMP-[1-5]|COMP|COMPUTATIONAL(?:-\d)?|BINARY|PACKED-DECIMAL|DISPLAY)\b", rest)
    if level == 88:
        parent = next((d["name"] for d in reversed(prog.data_items) if d["level"] != 88), None)
        values = re.findall(r"'[^']*'|\"[^\"]*\"|[-+]?\d+(?:\.\d+)?", val.group(1)) if val else []
        prog.conditions[name] = {"parent": parent, "values": values, "line": line}
        return section
    current_fd = prog.metrics.get("_current_fd") if section == "FILE" else None
    parent = None
    for d in reversed(prog.data_items):
        if d["level"] < level and d["level"] not in (66, 77, 88) and level not in (1, 77):
            parent = d["name"]
            break
    item = {
        "level": level,
        "name": name,
        "pic": pic.group(1) if pic else None,
        "value": val.group(1).strip() if val else None,
        "usage": usage.group(1) if usage else None,
        "section": section or "WORKING-STORAGE",
        "parent": parent,
        "file": current_fd,
        "line": line,
        "redefines": (re.search(r"REDEFINES\s+([A-Z0-9-]+)", rest) or [None, None])[1],
    }
    prog.data_items.append(item)
    if current_fd and level == 1:
        for f in prog.files:
            if f["name"] == current_fd:
                f["records"].append(name)
    return section


def _parse_procedure(proc_lines: list[SourceLine], prog: CobolProgram) -> None:
    paragraphs: list[dict] = []
    statements: list[Statement] = []
    current_para: str | None = None
    sql_by_start = {b["start_line"]: b for b in prog.sql_blocks + prog.cics}
    proc_start = proc_lines[0].number if proc_lines else None
    proc_end = proc_lines[-1].number if proc_lines else None

    # Re-insert EXEC blocks that lie inside the procedure division as pseudo statements
    exec_lines = sorted(
        b["start_line"] for b in prog.sql_blocks + prog.cics
        if proc_start and b["start_line"] >= proc_start - 1 and (proc_end is None or b["start_line"] <= proc_end + 50)
    )
    merged: list[tuple[int, SourceLine | dict]] = [(ln.number, ln) for ln in proc_lines]
    merged += [(n, sql_by_start[n]) for n in exec_lines]
    merged.sort(key=lambda x: x[0])
    in_procedure = False

    for number, item in merged:
        if isinstance(item, dict):
            if not in_procedure:
                continue
            item["paragraph"] = current_para
            op = item.get("analysis", {}).get("op") or item.get("command") or ""
            statements.append(Statement("EXEC", f"EXEC {item['kind']} {op}", item["start_line"], item["end_line"],
                                        current_para, item["ends_sentence"]))
            continue
        ln = item
        code = ln.code.strip()
        in_procedure = True
        word = _first_word(code)
        is_para = (
            re.fullmatch(r"[A-Z0-9][A-Z0-9-]*\.", code) is not None and word not in NON_PARAGRAPH
            and (ln.area_a or not any(c.isspace() for c in code))
        )
        sec = re.fullmatch(r"([A-Z0-9][A-Z0-9-]*)\s+SECTION\.", code)
        if is_para or sec:
            name = sec.group(1) if sec else word
            if paragraphs:
                paragraphs[-1]["end_line"] = number - 1
            paragraphs.append({"name": name, "start_line": number, "end_line": number, "is_section": bool(sec)})
            current_para = name
            continue
        if statements and word not in STARTERS and not statements[-1].ends_sentence:
            st = statements[-1]
            st.text = f"{st.text} {code}"
            st.end_line = number
            st.ends_sentence = code.endswith(".")
            continue
        statements.append(Statement(word or "UNKNOWN", code, number, number, current_para, code.endswith(".")))

    if paragraphs:
        paragraphs[-1]["end_line"] = proc_end or paragraphs[-1]["end_line"]
        # trim trailing blank region of each paragraph to its last statement
        for p in paragraphs:
            last = max((s.end_line for s in statements if s.paragraph == p["name"]), default=p["start_line"])
            p["end_line"] = max(p["start_line"], min(p["end_line"], last))

    # Split embedded verbs following IF/ELSE/WHEN heads into their own statements
    expanded: list[Statement] = []
    for st in statements:
        if st.verb in {"IF", "ELSE", "WHEN"}:
            head, embedded = _split_embedded(st.text.rstrip("."))
            ends = st.ends_sentence
            expanded.append(Statement(st.verb, head, st.start_line, st.end_line, st.paragraph, ends and not embedded))
            for k, e in enumerate(embedded):
                expanded.append(Statement(_first_word(e), e, st.start_line, st.end_line, st.paragraph,
                                          ends and k == len(embedded) - 1))
        else:
            expanded.append(st)
    statements = expanded

    prog.paragraphs = paragraphs
    para_names = {p["name"] for p in paragraphs}
    prog.statements = [s.__dict__ for s in statements]

    for st in statements:
        text = st.text.rstrip(".")
        if st.verb == "PERFORM":
            m = re.match(r"PERFORM\s+([A-Z0-9][A-Z0-9-]*)(?:\s+(?:THRU|THROUGH)\s+([A-Z0-9-]+))?", text)
            if m and m.group(1) in para_names:
                prog.performs.append({"from": st.paragraph, "to": m.group(1), "thru": m.group(2), "line": st.start_line,
                                      "conditional": bool(re.search(r"\bUNTIL\b|\bVARYING\b|\bTIMES\b", text))})
        for cm in re.finditer(r"\bCALL\s+(?:'([^']+)'|\"([^\"]+)\"|([A-Z0-9][A-Z0-9-]*))(?:\s+USING\s+(.*?))?(?=\s+(?:ON\s+|NOT\s+ON|END-CALL|RETURNING)|$)", text):
            literal = cm.group(1) or cm.group(2)
            target = literal or cm.group(3)
            dynamic = literal is None
            if dynamic:
                item = next((d for d in prog.data_items if d["name"] == target and d.get("value")), None)
                if item and item["value"].startswith(("'", '"')):
                    target, dynamic = item["value"].strip("'\""), False
            args = identifiers(cm.group(4) or "")
            prog.calls.append({"target": target.upper(), "dynamic": dynamic, "args": args, "line": st.start_line,
                               "paragraph": st.paragraph})
        _collect_flows(st, text, prog)
        if st.verb == "OPEN":
            mode = None
            for tok in text.split()[1:]:
                if tok in {"INPUT", "OUTPUT", "I-O", "EXTEND"}:
                    mode = tok
                elif mode:
                    prog.file_ops.append({"op": "OPEN", "file": tok, "mode": mode, "line": st.start_line})
        elif st.verb in {"READ", "WRITE", "REWRITE", "DELETE", "START"}:
            parts = text.split()
            if len(parts) > 1:
                prog.file_ops.append({"op": st.verb, "target": parts[1], "line": st.start_line,
                                      "paragraph": st.paragraph})

    _extract_logic(statements, prog)


def _collect_flows(st: Statement, text: str, prog: CobolProgram) -> None:
    def add(src: str | None, dst: str, kind: str, literal: str | None = None) -> None:
        prog.data_flows.append({"src": src, "dst": dst, "kind": kind, "line": st.start_line,
                                "paragraph": st.paragraph, "literal": literal})

    for m in re.finditer(r"\bMOVE\s+(?:CORRESPONDING\s+|CORR\s+)?(.+?)\s+TO\s+(.+?)(?=\s+(?:%s)\b|$)" % "|".join(sorted(EMBEDDABLE)), text):
        src_txt, dst_txt = m.group(1), m.group(2)
        src_ids = identifiers(src_txt)
        literal = None if src_ids else src_txt.strip()
        for dst in identifiers(dst_txt):
            if src_ids:
                for s in src_ids[:1]:
                    add(s, dst, "move")
            else:
                add(None, dst, "move", literal)
    if st.verb == "COMPUTE":
        m = re.match(r"COMPUTE\s+(.+?)\s*=\s*(.+)$", text)
        if m:
            targets = [t for t in identifiers(m.group(1))]
            srcs = identifiers(m.group(2))
            for dst in targets:
                for s in srcs:
                    add(s, dst, "compute")
                if not srcs:
                    add(None, dst, "compute", m.group(2).strip())
            prog.calculations.append({"target": targets[0] if targets else None, "expression": " ".join(m.group(2).split()),
                                      "line": st.start_line, "end_line": st.end_line, "paragraph": st.paragraph})
    elif st.verb in {"ADD", "SUBTRACT", "MULTIPLY", "DIVIDE"}:
        giving = re.search(r"\bGIVING\s+(.+)$", text)
        body = text[: giving.start()] if giving else text
        sep = {"ADD": "TO", "SUBTRACT": "FROM", "MULTIPLY": "BY", "DIVIDE": r"(?:INTO|BY)"}[st.verb]
        parts = re.split(rf"\s+{sep}\s+", body[len(st.verb):], maxsplit=1)
        srcs = identifiers(parts[0])
        others = identifiers(parts[1]) if len(parts) > 1 else []
        targets = identifiers(giving.group(1)) if giving else others
        all_srcs = srcs + (others if giving else [])
        for dst in targets:
            for s in all_srcs + ([dst] if not giving else []):
                if s != dst or not giving:
                    add(s, dst, "arithmetic")
        if targets:
            prog.calculations.append({"target": targets[0], "expression": " ".join(text.split()),
                                      "line": st.start_line, "end_line": st.end_line, "paragraph": st.paragraph})


# ----------------------------------------------------------------------------- decision logic

def _extract_logic(statements: list[Statement], prog: CobolProgram) -> None:
    """Walk IF/EVALUATE scopes and record each decision with its branches and actions."""
    stack: list[dict] = []

    def close_top(end_line: int) -> None:
        frame = stack.pop()
        frame["end_line"] = end_line
        if frame["type"] == "EVALUATE" and frame.get("current"):
            frame["whens"].append(frame.pop("current"))
        frame.pop("current", None)
        frame["context"] = [f["condition"] for f in stack if f["type"] == "IF"] + [
            f"{f['subject']} = {f['current']['value']}" for f in stack if f["type"] == "EVALUATE" and f.get("current")
        ]
        prog.decisions.append(frame)
        if stack:
            summary = f"IF {frame['condition']}" if frame["type"] == "IF" else f"EVALUATE {frame['subject']}"
            _append_action(stack[-1], {"text": summary, "line": frame["start_line"], "verb": frame["type"],
                                       "nested": True})

    for st in statements:
        text = st.text.rstrip(".")
        verb = st.verb
        if verb == "IF":
            stack.append({"type": "IF", "condition": _clean_condition(text, "IF"), "start_line": st.start_line,
                          "end_line": st.end_line, "paragraph": st.paragraph, "then": [], "else": [],
                          "branch": "then", "comment": _preceding_comment(prog, st.start_line)})
        elif verb == "ELSE":
            for frame in reversed(stack):
                if frame["type"] == "IF":
                    frame["branch"] = "else"
                    break
        elif verb == "END-IF":
            while stack and stack[-1]["type"] != "IF":
                close_top(st.end_line)
            if stack:
                close_top(st.end_line)
        elif verb == "EVALUATE":
            stack.append({"type": "EVALUATE", "subject": " ".join(text[len("EVALUATE"):].split()),
                          "start_line": st.start_line, "end_line": st.end_line, "paragraph": st.paragraph,
                          "whens": [], "current": None, "comment": _preceding_comment(prog, st.start_line)})
        elif verb == "WHEN":
            while stack and stack[-1]["type"] == "IF":
                close_top(st.start_line - 1)
            if stack and stack[-1]["type"] == "EVALUATE":
                frame = stack[-1]
                if frame.get("current"):
                    frame["whens"].append(frame["current"])
                frame["current"] = {"value": " ".join(text[len("WHEN"):].split()), "line": st.start_line, "actions": []}
        elif verb == "END-EVALUATE":
            while stack and stack[-1]["type"] != "EVALUATE":
                close_top(st.end_line)
            if stack:
                close_top(st.end_line)
        elif stack and verb not in SCOPE_END:
            _append_action(stack[-1], {"text": " ".join(text.split()), "line": st.start_line, "verb": verb})
        if st.ends_sentence:
            while stack:
                close_top(st.end_line)
    while stack:
        close_top(stack[-1]["end_line"])
    prog.decisions.sort(key=lambda d: d["start_line"])


def _append_action(frame: dict, action: dict) -> None:
    if frame["type"] == "IF":
        frame[frame["branch"]].append(action)
    elif frame.get("current") is not None:
        frame["current"]["actions"].append(action)


def _preceding_comment(prog: CobolProgram, line: int) -> str:
    """Comment lines immediately above a statement often state the business intent."""
    parts = []
    n = line - 1
    while n in prog.comments_by_line:
        parts.insert(0, prog.comments_by_line[n])
        n -= 1
    return _sentence_case(" ".join(parts)) if parts else ""


def _finalise(prog: CobolProgram, is_copybook: bool) -> None:
    prog.metrics.pop("_current_fd", None)
    if is_copybook:
        return
    if prog.program_id is None:
        prog.program_id = re.sub(r"\.[^.]+$", "", prog.path.split("/")[-1]).upper()
    file_modes: dict[str, set] = {}
    for op in prog.file_ops:
        if op["op"] == "OPEN":
            file_modes.setdefault(op["file"], set()).add(op["mode"])
    for f in prog.files:
        f["modes"] = sorted(file_modes.get(f["name"], set()))
    if prog.cics:
        prog.program_type = "online"
    elif prog.using_params:
        prog.program_type = "subprogram"
    else:
        prog.program_type = "batch"
    ifs = sum(1 for d in prog.decisions if d["type"] == "IF")
    whens = sum(len(d["whens"]) for d in prog.decisions if d["type"] == "EVALUATE")
    bool_ops = sum(len(re.findall(r"\b(AND|OR)\b", d.get("condition", ""))) for d in prog.decisions)
    loops = sum(1 for p in prog.performs if p["conditional"])
    prog.metrics = {
        "if_count": ifs,
        "evaluate_count": sum(1 for d in prog.decisions if d["type"] == "EVALUATE"),
        "when_count": whens,
        "loop_count": loops,
        "cyclomatic_complexity": 1 + ifs + whens + bool_ops + loops,
        "statement_count": len(prog.statements),
        "paragraph_count": len(prog.paragraphs),
        "sql_count": sum(1 for b in prog.sql_blocks if b["analysis"]["op"] not in {"INCLUDE"}),
        "call_count": len(prog.calls),
    }
