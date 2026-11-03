"""YAML configuration parser (Spring application.yml, docker-compose, Kubernetes, CI pipelines, ...).

It finds what an application connects to: databases, message brokers and topics, HTTP services and file
transfer endpoints. These become external interfaces in the knowledge graph. Secrets are never stored:
only scheme, host, port and the configuration key are kept.
"""
from __future__ import annotations

import re

import yaml

URL = re.compile(r"(?:jdbc:)?([a-z][a-z0-9+.\-]*)://(?:[^@/\s]*@)?([^/:?#\s,;]+)(?::(\d+))?", re.I)
HOST_PORT = re.compile(r"^([a-z0-9][a-z0-9.\-]*\.[a-z]{2,}|localhost):(\d{2,5})$", re.I)
DATABASE_SCHEMES = {"db2", "postgres", "postgresql", "mysql", "mariadb", "sqlserver", "oracle", "mongodb",
                    "mongodb+srv", "redis", "rediss", "cassandra", "h2"}
MESSAGING_SCHEMES = {"amqp", "amqps", "kafka", "nats", "mqtt", "sb", "jms", "tcp+ibmmq", "wmq"}
FILE_SCHEMES = {"ftp", "sftp", "ftps", "s3", "abfss", "wasbs", "gs"}


def parse_config(path: str, text: str) -> dict:
    try:
        docs = [d for d in yaml.safe_load_all(text) if d is not None]
    except yaml.YAMLError as exc:
        return {"kind": "invalid", "error": str(exc)[:200], "endpoints": [], "destinations": [], "app_name": None}
    lines = text.split("\n")
    endpoints, destinations, seen = [], [], set()
    for doc in docs:
        for key, value in _scalars(doc):
            if not isinstance(value, str) or "${" in value and "://" not in value:
                continue
            last = key.split(".")[-1].lower()
            for m in URL.finditer(value):
                scheme, host, port = m.group(1).lower(), m.group(2), m.group(3)
                if "${" in host or (scheme, host, port) in seen:
                    continue
                seen.add((scheme, host, port))
                endpoints.append({"kind": _kind(scheme, value), "scheme": scheme, "host": host, "port": port,
                                  "key": key, "line": _line_of(lines, m.group(0))})
            if not URL.search(value) and any(w in last for w in ("bootstrap", "broker", "servers", "host")):
                for part in re.split(r"[,\s]+", value):
                    hm = HOST_PORT.match(part)
                    if hm and (hm.group(1), hm.group(2)) not in seen:
                        seen.add((hm.group(1), hm.group(2)))
                        kind = "messaging" if any(w in key.lower() for w in ("kafka", "rabbit", "mq", "broker")) \
                            else "service"
                        endpoints.append({"kind": kind, "scheme": None, "host": hm.group(1), "port": hm.group(2),
                                          "key": key, "line": _line_of(lines, part)})
            if re.search(r"topic|queue", key, re.I) and re.fullmatch(r"[A-Za-z0-9._\-/]+", value):
                destinations.append({"name": value, "key": key, "line": _line_of(lines, value)})
    return {"kind": _config_kind(docs), "endpoints": endpoints, "destinations": destinations,
            "app_name": _app_name(docs)}


def _scalars(node, prefix: str = ""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _scalars(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _scalars(v, f"{prefix}[{i}]")
    else:
        yield prefix, node


def _kind(scheme: str, value: str) -> str:
    if value.lower().startswith("jdbc:") or scheme in DATABASE_SCHEMES:
        return "database"
    if scheme in MESSAGING_SCHEMES:
        return "messaging"
    if scheme in FILE_SCHEMES:
        return "file-transfer"
    if scheme in {"http", "https", "ws", "wss", "grpc"}:
        return "http"
    return "other"


def _config_kind(docs: list) -> str:
    first = docs[0] if docs and isinstance(docs[0], dict) else {}
    if "spring" in first:
        return "spring"
    if "services" in first and isinstance(first.get("services"), dict):
        return "compose"
    if "apiVersion" in first and "kind" in first:
        return "kubernetes"
    if "jobs" in first and (True in first or "on" in first):  # GitHub Actions ('on' loads as True)
        return "ci"
    if "stages" in first or "trigger" in first or "pool" in first:
        return "ci"
    return "generic"


def _app_name(docs: list) -> str | None:
    for d in docs:
        if isinstance(d, dict):
            name = (((d.get("spring") or {}).get("application") or {}).get("name")
                    if isinstance(d.get("spring"), dict) else None)
            if isinstance(name, str):
                return name
    return None


def _line_of(lines: list[str], needle: str) -> int:
    for i, ln in enumerate(lines, start=1):
        if needle in ln:
            return i
    return 1
