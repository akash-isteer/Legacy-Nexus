"""Shell script parser: a script is a batch job; each command that runs a program is a step.

Steps record what they invoke (a Python/Node script, a Java class, another script, a SQL file, a URL) and
their file redirections, which become datasets. Targets are matched to programs after all files are parsed.
"""
from __future__ import annotations

import re
import shlex
from pathlib import PurePosixPath

BUILTINS = {
    "echo", "cd", "set", "export", "source", ".", "test", "[", "[[", "exit", "mkdir", "rm", "cp", "mv", "date",
    "sleep", "cat", "grep", "awk", "sed", "true", "false", "printf", "local", "read", "trap", "wait", "shift",
    "return", "unset", "chmod", "chown", "touch", "tee", "find", "xargs", "ls", "head", "tail", "wc", "sort",
    "uniq", "cut", "tr", "if", "then", "else", "elif", "fi", "for", "do", "done", "while", "until", "case", "esac",
    "function", "{", "}", "logger", "basename", "dirname", "pwd", "env", "eval", "exec", "time", "nohup", "sudo",
    "gzip", "gunzip", "tar", "zip", "unzip", "ln", "df", "du", "kill", "ps",
}
SQL_CLIENTS = {"sqlplus", "psql", "mysql", "db2", "sqlcmd", "bteq", "isql"}
INTERPRETERS = {"python": "python", "python3": "python", "node": "node", "ts-node": "node", "bash": "shell",
                "sh": "shell", "ksh": "shell", "zsh": "shell", "spark-submit": "python"}


def parse_shell(path: str, text: str) -> dict:
    lines = text.replace("\r\n", "\n").split("\n")
    name = PurePosixPath(path).stem
    comments = [ln.strip().lstrip("#").strip() for ln in lines if ln.strip().startswith("#")
                and not ln.startswith("#!") and ln.strip().lstrip("#").strip()]
    job = {"name": name, "line": 1, "description": comments[0].rstrip(".") + "." if comments else "",
           "steps": [], "comments": comments, "kind": "shell"}
    for number, logical in _logical_lines(lines):
        for segment in re.split(r"&&|\|\||;|\|(?!\|)", logical):
            step = _step(segment.strip(), number, len(job["steps"]) + 1)
            if step:
                job["steps"].append(step)
    return job


def _logical_lines(lines: list[str]):
    buf, start = "", None
    for i, raw in enumerate(lines, start=1):
        s = raw.strip()
        if not buf and (not s or s.startswith("#")):
            continue
        s = re.sub(r"\s+#.*$", "", s) if "'" not in s and '"' not in s else s
        if start is None:
            start = i
        if s.endswith("\\"):
            buf += s[:-1] + " "
            continue
        yield start, buf + s
        buf, start = "", None


def _tokens(segment: str) -> list[str]:
    try:
        return shlex.split(segment, comments=False, posix=True)
    except ValueError:
        return segment.split()


def _step(segment: str, line: int, n: int) -> dict | None:
    if re.match(r"^[A-Za-z_]\w*=(\$\(|`)", segment) or segment.startswith(("$(", "`")):
        return None  # VAR=$(command): a value, not a batch step
    tokens = _tokens(segment)
    dds, words = [], []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        m = re.fullmatch(r"(\d?)(>>|>|<)(.*)", t)
        if m:
            target = m.group(3) or (tokens[i + 1] if i + 1 < len(tokens) else "")
            i += 1 if m.group(3) else 2
            if target and not target.startswith("&") and target != "/dev/null":
                disp = {">": "NEW", ">>": "MOD", "<": "SHR"}[m.group(2)]
                dds.append({"name": f"{'IN' if disp == 'SHR' else 'OUT'}{len(dds) + 1}", "dsn": target,
                            "disp": disp, "line": line, "sysout": None})
            continue
        words.append(t)
        i += 1
    while words and re.fullmatch(r"[A-Za-z_]\w*=.*", words[0]):  # VAR=value command
        words.pop(0)
    if not words or not re.fullmatch(r"[\w./~+-]+", words[0]):
        return None
    invokes = _invocation(words)
    if invokes is None:
        return None
    return {"name": f"STEP{n:03d}", "line": line, "program": None, "proc": None, "is_system_program": False,
            "cond": None, "dds": dds, "calls": [], "invokes": invokes, "command": " ".join(words)[:200]}


def _invocation(words: list[str]) -> dict | None:
    cmd = PurePosixPath(words[0]).name
    args = [w for w in words[1:]]
    if cmd in BUILTINS:
        return None
    if cmd in {"curl", "wget"}:
        url = next((a for a in args if re.match(r"https?://", a)), None)
        return {"kind": "http", "target": url} if url else None
    if cmd in INTERPRETERS:
        kind = INTERPRETERS[cmd]
        if kind == "python" and "-m" in args:
            idx = args.index("-m")
            return {"kind": "python-module", "target": args[idx + 1]} if idx + 1 < len(args) else None
        script = next((a for a in args if not a.startswith("-")), None)
        return {"kind": kind, "target": script} if script else None
    if cmd == "npx" and len(args) >= 2 and args[0] in {"ts-node", "tsx"}:
        return {"kind": "node", "target": args[1]}
    if cmd == "java":
        if "-jar" in args:
            idx = args.index("-jar")
            return {"kind": "jar", "target": args[idx + 1]} if idx + 1 < len(args) else None
        skip_next, main = False, None
        for a in args:
            if skip_next:
                skip_next = False
                continue
            if a in {"-cp", "-classpath", "--class-path"}:
                skip_next = True
                continue
            if not a.startswith("-"):
                main = a
                break
        return {"kind": "java", "target": main} if main else None
    if cmd == "dotnet":
        target = next((a for a in args if a.endswith(".dll") or a.endswith(".csproj")), None)
        return {"kind": "dotnet", "target": target or " ".join(args)}
    if cmd in SQL_CLIENTS:
        script = next((a[1:] for a in args if a.startswith("@")), None) or next(
            (args[i + 1] for i, a in enumerate(args[:-1]) if a in {"-f", "-i", "-tvf", "-vf", "--file"}), None)
        return {"kind": "sql", "target": script or cmd}
    if words[0].endswith(".sh") or words[0].startswith("./") or words[0].startswith("/"):
        kind = "shell" if words[0].endswith((".sh", ".ksh", ".bash")) else "exec"
        return {"kind": kind, "target": words[0]}
    return {"kind": "exec", "target": words[0]}
