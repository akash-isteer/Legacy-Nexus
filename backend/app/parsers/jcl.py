"""JCL parser: jobs, steps, executed programs/procedures, DD statements and in-stream program calls."""
from __future__ import annotations

import re

# IBM utilities and TSO/DB2 drivers: they run other things rather than application logic.
SYSTEM_PROGRAMS = {
    "IKJEFT01", "IKJEFT1A", "IKJEFT1B", "IEBGENER", "IEBCOPY", "IDCAMS", "SORT", "DFSORT", "ICETOOL", "ICEMAN",
    "IEFBR14", "DSNTEP2", "DSNTEP4", "DSNTIAUL", "DSNUTILB", "ADRDSSU", "IEBUPDTE", "SYNCSORT", "FTP", "BPXBATCH",
}

_STMT = re.compile(r"^//([A-Z0-9@#$]{0,8})\s+(JOB|EXEC|DD|PROC|PEND|SET|INCLUDE|JCLLIB|IF|ELSE|ENDIF)\b\s*(.*)$")


def _param(operands: str, key: str) -> str | None:
    m = re.search(rf"(?:^|,)\s*{key}=(\([^)]*\)|[^,\s]+)", operands)
    return m.group(1) if m else None


def parse_jcl(path: str, text: str) -> dict:
    lines = text.replace("\r\n", "\n").split("\n")
    job: dict = {"name": None, "line": None, "description": "", "steps": [], "comments": []}
    current_step: dict | None = None
    instream_for: dict | None = None
    instream_lines: list[tuple[int, str]] = []

    def flush_instream() -> None:
        nonlocal instream_lines, instream_for
        if instream_for is not None and current_step is not None:
            body = "\n".join(t for _, t in instream_lines)
            instream_for["instream"] = body
            for n, t in instream_lines:
                up = t.upper()
                for m in re.finditer(r"\bCALL\s+([A-Z0-9_.]+)\s*\(", up):
                    current_step["calls"].append({"target": m.group(1), "kind": "procedure", "line": n})
                for m in re.finditer(r"\bRUN\s+PROGRAM\s*\(\s*([A-Z0-9@#$]+)\s*\)", up):
                    if m.group(1) not in SYSTEM_PROGRAMS:
                        current_step["calls"].append({"target": m.group(1), "kind": "program", "line": n})
        instream_lines, instream_for = [], None

    for idx, raw in enumerate(lines, start=1):
        line = raw.rstrip()
        if instream_for is not None:
            if line.startswith("/*") or (line.startswith("//") and not line.startswith("//*")):
                flush_instream()
                if line.startswith("/*"):
                    continue
            else:
                instream_lines.append((idx, line))
                continue
        if line.startswith("//*"):
            comment = line[3:].strip(" *-")
            if comment:
                job["comments"].append(comment)
            continue
        m = _STMT.match(line.upper())
        if not m:
            continue
        name, op, operands = m.group(1), m.group(2), m.group(3).strip()
        if op == "JOB":
            job["name"] = name
            job["line"] = idx
            desc = re.search(r"'([^']*)'", line)
            job["description"] = desc.group(1).title() if desc else ""
        elif op == "EXEC":
            pgm = _param(operands, "PGM")
            proc = None if pgm else (_param(operands, "PROC") or operands.split(",")[0].strip() or None)
            current_step = {
                "name": name or f"STEP{len(job['steps']) + 1:03d}",
                "line": idx,
                "program": pgm,
                "proc": proc,
                "is_system_program": bool(pgm and pgm in SYSTEM_PROGRAMS),
                "cond": _param(operands, "COND"),
                "dds": [],
                "calls": [],
            }
            job["steps"].append(current_step)
        elif op == "DD" and current_step is not None:
            dsn = _param(operands, "DSN") or _param(operands, "DSNAME")
            disp = _param(operands, "DISP")
            dd = {"name": name, "dsn": dsn.strip("()") if dsn else None, "disp": disp, "line": idx,
                  "sysout": _param(operands, "SYSOUT")}
            current_step["dds"].append(dd)
            if re.match(r"^(\*|DATA)(?=[\s,]|$)", operands):
                instream_for = dd
                instream_lines = []
    flush_instream()
    if job["name"] is None:
        job["name"] = path.split("/")[-1].split(".")[0].upper()
    job["description"] = job["description"] or (job["comments"][0].capitalize() if job["comments"] else "")
    return job


def dd_direction(disp: str | None) -> str:
    """Best-effort dataset direction from DISP when program file modes are unknown."""
    if not disp:
        return "input"
    first = disp.strip("()").split(",")[0].strip().upper()
    return "output" if first in {"NEW", "MOD"} else "input"
