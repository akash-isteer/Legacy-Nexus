"""SQL analysis built on sqlglot, with regex fallbacks for dialect constructs it cannot parse.

Used for embedded SQL in COBOL (EXEC SQL ... END-EXEC) and for standalone DDL/stored procedure files.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import expressions as exp
from sqlglot.errors import ParseError

HOST_VAR = re.compile(r":([A-Z0-9][A-Z0-9_-]*)", re.I)
_DB2_SPECIAL = [
    (re.compile(r"\bCURRENT\s+DATE\b", re.I), "CURRENT_DATE"),
    (re.compile(r"\bCURRENT\s+TIMESTAMP\b", re.I), "CURRENT_TIMESTAMP"),
    (re.compile(r"\bCURRENT\s+TIME\b", re.I), "CURRENT_TIME"),
    # DB2 labeled durations: "P_DAYS DAYS" -> "P_DAYS"
    (re.compile(r"\b([A-Z0-9_]+)\s+(?:DAYS?|MONTHS?|YEARS?|HOURS?|MINUTES?|SECONDS?)\b", re.I), r"\1"),
    (re.compile(r"\bWITH\s+(?:UR|CS|RS|RR)\b", re.I), ""),
    (re.compile(r"\bFOR\s+(?:FETCH|READ)\s+ONLY\b", re.I), ""),
    (re.compile(r"\bFOR\s+UPDATE(?:\s+OF\s+[A-Z0-9_,\s]+)?$", re.I), ""),
    (re.compile(r"\bFETCH\s+FIRST\s+\d+\s+ROWS?\s+ONLY\b", re.I), ""),
]


@dataclass
class SqlStatement:
    op: str  # SELECT | INSERT | UPDATE | DELETE | DECLARE | FETCH | OPEN | CLOSE | INCLUDE | CALL | OTHER
    tables_read: list[str] = field(default_factory=list)
    tables_written: list[str] = field(default_factory=list)
    columns_read: list[tuple[str | None, str]] = field(default_factory=list)  # (table, column)
    columns_written: list[tuple[str | None, str]] = field(default_factory=list)
    filter_columns: list[tuple[str | None, str]] = field(default_factory=list)
    select_columns: list[tuple[str | None, str]] = field(default_factory=list)  # projection order
    into_vars: list[str] = field(default_factory=list)  # host vars receiving SELECT/FETCH results
    input_vars: list[str] = field(default_factory=list)  # host vars used as inputs
    assignments: list[tuple[str | None, str, str]] = field(default_factory=list)  # (table, column, host var)
    cursor: str | None = None
    include: str | None = None
    called_procedure: str | None = None
    parsed_with: str = "sqlglot"

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def _normalize(sql: str) -> str:
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = " ".join(sql.split())
    for pattern, repl in _DB2_SPECIAL:
        sql = pattern.sub(repl, sql)
    return sql.strip().rstrip(";").strip()


def _replace_host_vars(sql: str) -> tuple[str, dict[str, str]]:
    mapping: dict[str, str] = {}

    def repl(m: re.Match) -> str:
        name = m.group(1).upper()
        key = f"HV{len(mapping)}"
        mapping[key] = name
        return f":{key}"

    return HOST_VAR.sub(repl, sql), mapping


def _table_aliases(tree: exp.Expression) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for t in tree.find_all(exp.Table):
        name = t.name.upper()
        aliases[name] = name
        if t.alias:
            aliases[t.alias.upper()] = name
    return aliases


def _col(col: exp.Column, aliases: dict[str, str], tables: list[str]) -> tuple[str | None, str]:
    qualifier = col.table.upper() if col.table else None
    table = aliases.get(qualifier) if qualifier else (tables[0] if len(tables) == 1 else None)
    return table, col.name.upper()


def analyze_statement(raw_sql: str) -> SqlStatement:
    """Analyse one SQL statement (host variables like :WS-NAME are supported)."""
    sql = _normalize(raw_sql)
    upper = sql.upper()
    first = upper.split(" ", 1)[0] if upper else ""

    if first == "INCLUDE":
        return SqlStatement(op="INCLUDE", include=upper.split()[1] if len(upper.split()) > 1 else None)
    if first in {"OPEN", "CLOSE"}:
        parts = upper.split()
        return SqlStatement(op=first, cursor=parts[1] if len(parts) > 1 else None)
    if first == "FETCH":
        m = re.match(r"FETCH\s+(?:NEXT\s+FROM\s+|FROM\s+)?([A-Z0-9_-]+)\s+INTO\s+(.*)$", upper)
        stmt = SqlStatement(op="FETCH")
        if m:
            stmt.cursor = m.group(1)
            stmt.into_vars = [v.upper() for v in HOST_VAR.findall(m.group(2))]
        return stmt
    if first == "CALL":
        m = re.match(r"CALL\s+([A-Z0-9_.]+)", upper)
        stmt = SqlStatement(op="CALL", called_procedure=m.group(1) if m else None)
        stmt.input_vars = [v.upper() for v in HOST_VAR.findall(upper)]
        return stmt

    cursor = None
    m = re.match(r"DECLARE\s+([A-Z0-9_-]+)\s+(?:\w+\s+)*?CURSOR\s+(?:WITH\s+HOLD\s+)?FOR\s+(.*)$", upper)
    if m:
        cursor, sql = m.group(1), m.group(2)
        upper = sql.upper()

    into_vars: list[str] = []
    m = re.search(r"\bINTO\s+((?::[A-Z0-9_-]+\s*,?\s*)+)\s*FROM\b", upper)
    if upper.startswith("SELECT") and m:
        into_vars = [v.upper() for v in HOST_VAR.findall(m.group(1))]
        sql = sql[: m.start()] + " FROM" + sql[m.end():]

    prepared, hv_map = _replace_host_vars(sql)
    stmt = SqlStatement(op="DECLARE" if cursor else first or "OTHER", cursor=cursor, into_vars=into_vars)
    try:
        tree = sqlglot.parse_one(prepared)
    except (ParseError, Exception):  # sqlglot raises several error types for unsupported dialect syntax
        return _regex_fallback(sql, stmt, hv_map)
    if tree is None:
        return _regex_fallback(sql, stmt, hv_map)

    aliases = _table_aliases(tree)
    tables = list(dict.fromkeys(aliases.values()))

    def hv(node: exp.Expression) -> str | None:
        return hv_map.get(node.name) if isinstance(node, exp.Placeholder) else None

    stmt.input_vars = [hv_map[p.name] for p in tree.find_all(exp.Placeholder) if p.name in hv_map]

    if isinstance(tree, exp.Select):
        stmt.tables_read = tables
        for proj in tree.expressions:
            inner = proj.this if isinstance(proj, exp.Alias) else proj
            if isinstance(inner, exp.Column):
                stmt.select_columns.append(_col(inner, aliases, tables))
            elif isinstance(inner, exp.Star):
                stmt.select_columns.append((tables[0] if tables else None, "*"))
            else:
                cols = [_col(c, aliases, tables) for c in inner.find_all(exp.Column)]
                stmt.select_columns.append(cols[0] if cols else (None, inner.sql()))
        stmt.columns_read = [_col(c, aliases, tables) for c in tree.find_all(exp.Column)]
        where = tree.args.get("where")
        if where:
            stmt.filter_columns = [_col(c, aliases, tables) for c in where.find_all(exp.Column)]
    elif isinstance(tree, exp.Insert):
        target = tree.this.this if isinstance(tree.this, exp.Schema) else tree.this
        target_name = target.name.upper() if isinstance(target, exp.Table) else None
        stmt.tables_written = [target_name] if target_name else []
        cols = [i.name.upper() for i in tree.this.expressions] if isinstance(tree.this, exp.Schema) else []
        stmt.columns_written = [(target_name, c) for c in cols]
        values = tree.expression
        if isinstance(values, exp.Values) and values.expressions:
            row = values.expressions[0].expressions
            for col_name, val in zip(cols, row):
                var = hv(val)
                if var:
                    stmt.assignments.append((target_name, col_name, var))
        elif isinstance(values, exp.Select):
            sub_tables = list({t.name.upper() for t in values.find_all(exp.Table)})
            stmt.tables_read = sub_tables
            stmt.columns_read = [_col(c, aliases, sub_tables) for c in values.find_all(exp.Column)]
    elif isinstance(tree, exp.Update):
        target_name = tree.this.name.upper() if isinstance(tree.this, exp.Table) else None
        stmt.tables_written = [target_name] if target_name else []
        for assign in tree.expressions:
            if isinstance(assign, exp.EQ) and isinstance(assign.this, exp.Column):
                col_name = assign.this.name.upper()
                stmt.columns_written.append((target_name, col_name))
                for p in assign.expression.find_all(exp.Placeholder):
                    if p.name in hv_map:
                        stmt.assignments.append((target_name, col_name, hv_map[p.name]))
                for c in assign.expression.find_all(exp.Column):
                    stmt.columns_read.append(_col(c, aliases, tables))
        where = tree.args.get("where")
        if where:
            stmt.filter_columns = [_col(c, aliases, tables) for c in where.find_all(exp.Column)]
            stmt.columns_read += stmt.filter_columns
    elif isinstance(tree, exp.Delete):
        target_name = tree.this.name.upper() if isinstance(tree.this, exp.Table) else None
        stmt.tables_written = [target_name] if target_name else []
        where = tree.args.get("where")
        if where:
            stmt.filter_columns = [_col(c, aliases, tables) for c in where.find_all(exp.Column)]
            stmt.columns_read = list(stmt.filter_columns)
    else:
        stmt.tables_read = tables
        stmt.columns_read = [_col(c, aliases, tables) for c in tree.find_all(exp.Column)]
    return stmt


def _regex_fallback(sql: str, stmt: SqlStatement, hv_map: dict[str, str]) -> SqlStatement:
    upper = sql.upper()
    stmt.parsed_with = "regex"
    stmt.input_vars = [v.upper() for v in HOST_VAR.findall(upper)]
    read = re.findall(r"\b(?:FROM|JOIN)\s+([A-Z0-9_.]+)", upper)
    if upper.startswith("UPDATE"):
        m = re.match(r"UPDATE\s+([A-Z0-9_.]+)", upper)
        if m:
            stmt.tables_written = [m.group(1)]
        set_part = re.search(r"\bSET\s+(.*?)(?:\bWHERE\b|$)", upper)
        if set_part:
            for col, val in re.findall(r"([A-Z0-9_]+)\s*=\s*([^,]+)", set_part.group(1)):
                stmt.columns_written.append((stmt.tables_written[0] if stmt.tables_written else None, col))
                for var in HOST_VAR.findall(val):
                    stmt.assignments.append((stmt.tables_written[0] if stmt.tables_written else None, col, var.upper()))
    elif upper.startswith("INSERT"):
        m = re.match(r"INSERT\s+INTO\s+([A-Z0-9_.]+)", upper)
        if m:
            stmt.tables_written = [m.group(1)]
    elif upper.startswith("DELETE"):
        m = re.match(r"DELETE\s+FROM\s+([A-Z0-9_.]+)", upper)
        if m:
            stmt.tables_written = [m.group(1)]
            read = [r for r in read if r != m.group(1)]
    stmt.tables_read = list(dict.fromkeys(read))
    where = re.search(r"\bWHERE\s+(.*)$", upper)
    if where:
        table = (stmt.tables_written or stmt.tables_read or [None])[0]
        cols = [c for c in re.findall(r"\b([A-Z][A-Z0-9_]*)\s*(?:=|<>|<=|>=|<|>|\bIN\b|\bLIKE\b)", where.group(1))]
        stmt.filter_columns = [(table, c) for c in dict.fromkeys(cols) if c not in {"AND", "OR", "NOT"}]
        stmt.columns_read += stmt.filter_columns
    return stmt


# ----------------------------------------------------------------------------- standalone SQL files

_ROUTINE = re.compile(r"CREATE\s+(?:OR\s+REPLACE\s+)?(PROCEDURE|FUNCTION|TRIGGER)\s+([A-Z0-9_.\"]+)", re.I)


@dataclass
class SqlFileResult:
    tables: list[dict] = field(default_factory=list)  # {name, line, columns:[{name,type,nullable,pk,line}], foreign_keys:[...]}
    routines: list[dict] = field(default_factory=list)  # {name, kind, line, end_line, statements:[{...SqlStatement, line}]}
    statements: list[dict] = field(default_factory=list)  # other DML found in scripts
    views: list[dict] = field(default_factory=list)


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def parse_sql_file(text: str) -> SqlFileResult:
    result = SqlFileResult()
    consumed: list[tuple[int, int]] = []

    # 1. Routines (procedure bodies contain ';' so they are cut out first)
    for m in _ROUTINE.finditer(text):
        start = m.start()
        end_match = None
        for em in re.finditer(r"^\s*END\s*[;@/]?\s*$", text[start:], re.I | re.M):
            end_match = em
        if end_match is None:
            continue
        end = start + end_match.end()
        consumed.append((start, end))
        body = text[start:end]
        begin = re.search(r"\bBEGIN\b", body, re.I)
        routine = {
            "name": m.group(2).strip('"').upper(),
            "kind": m.group(1).upper(),
            "line": _line_of(text, start),
            "end_line": _line_of(text, end),
            "statements": [],
        }
        if begin:
            inner_start = start + begin.end()
            inner = text[inner_start:start + end_match.start()]
            offset = 0
            for piece in inner.split(";"):
                if piece.strip():
                    lead = len(piece) - len(piece.lstrip())
                    st = analyze_statement(piece).to_dict()
                    st["line"] = _line_of(text, inner_start + offset + lead)
                    routine["statements"].append(st)
                offset += len(piece) + 1
        result.routines.append(routine)

    # 2. Everything else, statement by statement
    pos = 0
    for start, end in sorted(consumed) + [(len(text), len(text))]:
        chunk_start = pos
        segment = text[pos:start]
        offset = 0
        for piece in segment.split(";"):
            stripped = re.sub(r"--[^\n]*", "", piece).strip()
            if stripped:
                lead = len(piece) - len(piece.lstrip())
                line = _line_of(text, chunk_start + offset + lead)
                # skip leading comment lines when computing the statement line
                for raw in piece.splitlines():
                    if raw.strip() and not raw.strip().startswith("--"):
                        line = _line_of(text, chunk_start + offset + piece.index(raw))
                        break
                _parse_ddl_or_dml(stripped, piece, text, chunk_start + offset, line, result)
            offset += len(piece) + 1
        pos = end
    return result


def _parse_ddl_or_dml(sql: str, raw_piece: str, full_text: str, piece_offset: int, line: int, result: SqlFileResult) -> None:
    upper = sql.upper()
    if upper.startswith("CREATE TABLE") or re.match(r"CREATE\s+(GLOBAL\s+TEMPORARY\s+)?TABLE", upper):
        try:
            tree = sqlglot.parse_one(_normalize(sql))
        except Exception:
            tree = None
        table = {"name": None, "line": line, "columns": [], "foreign_keys": []}
        if isinstance(tree, exp.Create) and isinstance(tree.this, exp.Schema):
            table["name"] = tree.this.this.name.upper()
            for cdef in tree.this.expressions:
                if isinstance(cdef, exp.ColumnDef):
                    cons = [c.sql().upper() for c in cdef.args.get("constraints", [])]
                    col_line = line
                    m = re.search(rf"^[ \t]*{re.escape(cdef.name)}\b", raw_piece, re.I | re.M)
                    if m:
                        col_line = _line_of(full_text, piece_offset + m.start())
                    ref = next((c for c in cons if c.startswith("REFERENCES")), None)
                    table["columns"].append({
                        "name": cdef.name.upper(),
                        "type": cdef.args["kind"].sql().upper() if cdef.args.get("kind") else "",
                        "nullable": "NOT NULL" not in cons,
                        "primary_key": "PRIMARY KEY" in cons,
                        "line": col_line,
                    })
                    if ref:
                        rm = re.match(r"REFERENCES\s+([A-Z0-9_.]+)\s*\(([^)]*)\)", ref)
                        if rm:
                            table["foreign_keys"].append({
                                "column": cdef.name.upper(), "ref_table": rm.group(1), "ref_column": rm.group(2).strip()
                            })
                elif isinstance(cdef, exp.PrimaryKey):
                    pk = {e.name.upper() for e in cdef.expressions}
                    for c in table["columns"]:
                        if c["name"] in pk:
                            c["primary_key"] = True
        else:
            m = re.match(r"CREATE\s+(?:GLOBAL\s+TEMPORARY\s+)?TABLE\s+([A-Z0-9_.]+)", upper)
            table["name"] = m.group(1) if m else None
        if table["name"]:
            result.tables.append(table)
        return
    if upper.startswith("CREATE VIEW") or upper.startswith("CREATE OR REPLACE VIEW"):
        m = re.search(r"VIEW\s+([A-Z0-9_.]+)\s+(?:\([^)]*\)\s+)?AS\s+(.*)$", upper, re.S)
        if m:
            inner = analyze_statement(m.group(2))
            result.views.append({"name": m.group(1), "line": line, "tables_read": inner.tables_read})
        return
    if re.match(r"(SELECT|INSERT|UPDATE|DELETE|MERGE|CALL)\b", upper):
        st = analyze_statement(sql).to_dict()
        st["line"] = line
        result.statements.append(st)
