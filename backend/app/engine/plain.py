"""Plain-language wording for people who do not read code. Deterministic: no AI involved.

Technical names become words ("WS-MIN-BALANCE" -> "minimum balance", "ACCOUNT_MASTER" -> "Account master",
"newBalance" -> "new balance") and rule descriptions are rewritten as sentences a business reader can check.
"""
from __future__ import annotations

import re

PREFIXES = re.compile(r"^(WS|LK|PI|RPT|WK|W|L|LS|FD|SQL|HV|IN|OUT|TMP|M|P)[-_](?=[A-Za-z])", re.I)
ABBREVIATIONS = {
    "acct": "account", "amt": "amount", "bal": "balance", "cust": "customer", "addr": "address", "pmt": "payment",
    "pay": "payment", "txn": "transaction", "trn": "transaction", "num": "number", "nbr": "number", "no": "number",
    "id": "ID", "dt": "date", "ts": "timestamp", "qty": "quantity", "pct": "percent", "rt": "rate", "min": "minimum",
    "max": "maximum", "cnt": "count", "cd": "code", "desc": "description", "stmt": "statement", "rpt": "report",
    "int": "interest", "ccy": "currency", "curr": "current", "prev": "previous", "msg": "message", "err": "error",
    "flg": "flag", "ind": "indicator", "eod": "end of day", "eom": "end of month", "yr": "year", "mth": "month",
}
KEEP_UPPER = {"ID", "API", "URL", "SQL", "VAT", "IBAN", "SWIFT", "ACH", "KYC", "GL"}


def words(name: str) -> str:
    """'WS-MIN-BALANCE' -> 'minimum balance'; 'newBalance' -> 'new balance'; 'MAX_PAYMENT' -> 'maximum payment'."""
    if not name:
        return ""
    base = re.sub(r"^(this|self)\.", "", name.strip())
    base = PREFIXES.sub("", base)
    base = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", base)  # camelCase
    parts = [p for p in re.split(r"[-_.\s]+", base) if p]
    out = []
    for p in parts:
        low = p.lower()
        if p.upper() in KEEP_UPPER:
            out.append(p.upper())
        else:
            out.append(ABBREVIATIONS.get(low, low))
    return " ".join(out)


def table_label(name: str) -> str:
    """'ACCOUNT_MASTER' -> 'Account master'."""
    w = words(name)
    return w[:1].upper() + w[1:] if w else name


def file_label(path: str) -> str:
    return path.replace("\\", "/").split("/")[-1]


_NAME = re.compile(r"(?<![\w'\"])([A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)+|[a-z]+[A-Z][A-Za-z0-9]*"
                   r"|[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+)(?![\w'\"(])")
_ARG = r"((?:[^()]|\([^()]*\))*)"  # an argument that may contain one level of parentheses


def plain_rule(rule: dict) -> str:
    """A business-readable sentence for an extracted rule."""
    text = rule.get("description") or ""
    intent = (rule.get("intent") or "").strip()
    if intent and text.startswith(intent):
        text = text[len(intent):].strip()
    text = re.sub(r"\s*\[([A-Z0-9_]+)\.([A-Z0-9_]+)\]", lambda m: f" (stored as {words(m.group(2))} in "
                                                                  f"{table_label(m.group(1))})", text)
    text = re.sub(r"(?:throw|raise)\s+new\s+\w+\(\s*[\"']([^\"']*)[\"']\s*\)|(?:throw|raise)\s+\w+\(\s*[\"']([^\"']*)[\"']\s*\)",
                  lambda m: f"reject it with the message “{m.group(1) or m.group(2)}”", text, flags=re.I)
    text = re.sub(r"\b(\w+)\.set(\w+)\(([^)]*)\)", lambda m: f"set {words(m.group(2))} to {m.group(3).strip() or 'on'}", text)
    text = re.sub(r"\b(\w+)\.(get|is)(\w+)\(\)", lambda m: words(m.group(3)), text)
    text = re.sub(rf"\b(\w+)\.compareTo\({_ARG}\)\s+is greater than 0", r"\1 is greater than \2", text)
    text = re.sub(rf"\b(\w+)\.compareTo\({_ARG}\)\s+is less than 0", r"\1 is less than \2", text)
    text = re.sub(r"\bSQLCODE does not equal 0\b", "the database lookup fails", text)
    text = re.sub(r"\bSQLCODE equals 0\b", "the database lookup succeeds", text)
    text = re.sub(r"\bSPACES?\b", "blank", text)
    text = re.sub(r"\bDepending on TRUE:", "Depending on the case:", text)
    text = re.sub(r"\brun (\d+-)?([A-Z][A-Z0-9-]*)", lambda m: f"run the “{words(m.group(2))}” step", text)
    text = re.sub(r"\bcall program ([A-Z0-9-]+)", r"call the \1 program", text)
    text = re.sub(r"\.intValue\(\)|\.doubleValue\(\)|\.Length\b|\.length\b", lambda m: "" if "Value" in m.group(0)
                  else " length", text)
    text = re.sub(r"string\.IsNullOrWhiteSpace\((\w+)\)|string\.IsNullOrEmpty\((\w+)\)",
                  lambda m: f"{m.group(1) or m.group(2)} is empty", text)
    text = re.sub(r"“[^”]*”|‘[^’]*’|'[^']*'", lambda m: m.group(0).replace("-", "‑"), text)  # keep quoted text
    text = _NAME.sub(lambda m: words(m.group(1)), text)
    text = text.replace("‑", "-")
    text = re.sub(r"\b(Calculates?|calculate) ([\w ]+?) = ", lambda m: f"{m.group(1)} {m.group(2)} as ", text)
    text = re.sub(r"\s+", " ", text).strip()
    sentence = text[:1].upper() + text[1:]
    return f"{intent} {sentence}".strip() if intent else sentence


CATEGORY_MEANING = {
    "Validation": "Checks input and rejects or holds what is not allowed.",
    "Threshold": "Compares a value against a limit and acts when it is crossed.",
    "Calculation": "Computes an amount or value.",
    "Decision": "Chooses what happens next based on a condition.",
    "Classification": "Sorts cases into categories and handles each one differently.",
    "Domain values": "Lists the values a field is allowed to hold.",
    "Technical": "Handles technical conditions such as errors or end of file, not business policy.",
}
