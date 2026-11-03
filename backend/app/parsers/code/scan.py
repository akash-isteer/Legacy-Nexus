"""Language-neutral scan of a method or function body.

Each language supplies ``Hooks`` that recognise its node types; the scan then records, with line numbers:
calls (resolved later across files), object creation, decisions for business rules, arithmetic for
calculation rules, embedded SQL strings, local variable types and loops.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field

from app.parsers.code.tsutil import (
    calculation, decision, line, looks_like_sql, one_line, preceding_comment, sql_block, txt, walk,
)


@dataclass
class Hooks:
    nested: set[str]                       # node types not descended into (nested classes / functions)
    loops: set[str]
    call_parts: callable                   # node -> (receiver_node | None, method_name) | None
    new_type: callable                     # node -> type name | None
    if_parts: callable                     # node -> (cond, then, else) | None
    assign_parts: callable                 # node -> (target_text, expr_text) | None
    local_decls: callable                  # node -> [(name, type | None, value_node | None)]
    string_value: callable                 # node -> str | None (whole concatenations, once)
    receiver_type: callable                # (receiver_node, scope, unit) -> type | None
    is_self: callable                      # receiver_node -> bool
    member_of: callable = lambda receiver, scope, unit: None  # receiver -> {"type", "member"} for a.b receivers
    constructor_call: callable = lambda node: None            # call node -> type when the call builds an object
    arg_types: callable = lambda node, scope: []              # call node -> types of objects built in arguments


@dataclass
class Scope:
    types: dict = dc_field(default_factory=dict)   # variable -> declared or inferred type
    names: set = dc_field(default_factory=set)     # parameters and local variables, typed or not


def scan_body(body, unit: dict, paragraph: str | None, scope: Scope, hooks: Hooks, lines: list[str],
              own_methods: set[str]) -> None:
    if body is None:
        return
    for n in walk(body, stop=hooks.nested):
        if n is not body and n.type in hooks.nested:
            continue
        if n.type in hooks.loops:
            unit["metrics"]["_loops"] = unit["metrics"].get("_loops", 0) + 1

        for name, typ, value in hooks.local_decls(n):
            scope.names.add(name)
            built =(hooks.new_type(value) or hooks.constructor_call(value)) if value is not None else None
            if typ or built:
                scope.types[name] = typ if typ and typ not in {"var", "let", "const", "auto"} else built
            if value is not None:
                calc = calculation(name, txt(value), n, paragraph)
                if calc:
                    unit["calculations"].append(calc)

        parts = hooks.if_parts(n)
        if parts:
            cond, then, other = parts
            d = decision(n, cond, then, other, paragraph, preceding_comment(n, lines))
            unit["decisions"].append(d)
            unit["statements"].append({"verb": "IF", "text": "IF " + d["condition"], "start_line": d["start_line"],
                                       "end_line": d["start_line"], "paragraph": paragraph, "ends_sentence": True})

        call = hooks.call_parts(n)
        if call:
            _record_call(n, call, unit, paragraph, scope, hooks, own_methods)

        built = hooks.new_type(n)
        if built:
            unit["type_refs"].append({"type": built, "line": line(n), "how": "new"})

        asg = hooks.assign_parts(n)
        if asg:
            calc = calculation(asg[0], asg[1], n, paragraph)
            if calc:
                unit["calculations"].append(calc)

        text = hooks.string_value(n)
        if text and looks_like_sql(text):
            unit["sql_blocks"].append(sql_block(text, n, paragraph))
            unit["statements"].append({"verb": "EXEC", "text": "EXEC SQL " + one_line(text, 120),
                                       "start_line": line(n), "end_line": line(n), "paragraph": paragraph,
                                       "ends_sentence": True})


def _record_call(n, call, unit, paragraph, scope, hooks, own_methods) -> None:
    receiver, method = call
    ln = line(n)
    unit["statements"].append({"verb": "CALL", "text": one_line(txt(n), 160), "start_line": ln, "end_line": ln,
                               "paragraph": paragraph, "ends_sentence": True})
    if receiver is None or hooks.is_self(receiver):
        if method in own_methods:
            unit["performs"].append({"from": paragraph, "to": method, "thru": None, "line": ln, "conditional": False})
            return
        if receiver is None:
            built = hooks.constructor_call(n)
            if built:
                unit["type_refs"].append({"type": built, "line": ln, "how": "new"})
                return
    rtype = None
    member = None
    if receiver is not None and not hooks.is_self(receiver):
        rtype = hooks.receiver_type(receiver, scope, unit)
        member = hooks.member_of(receiver, scope, unit)
        if rtype == unit["program_id"] and method in own_methods:  # new Self().run(), Self.helper()
            unit["performs"].append({"from": paragraph, "to": method, "thru": None, "line": ln, "conditional": False})
            return
    rtext = one_line(txt(receiver), 120) if receiver is not None else None
    # A variable, parameter or object field (not an imported module such as `requests`): dynamic-language
    # calls through it may be inferred from method names; calls on modules are library calls.
    local = bool(rtext) and (rtext in scope.names or rtext in scope.types
                             or rtext.startswith(("self.", "this.")) or rtext in unit["field_types"])
    unit["raw_calls"].append({
        "receiver": rtext, "receiver_type": rtype, "method": method, "line": ln, "paragraph": paragraph,
        "member_of": member, "arg_types": hooks.arg_types(n, scope),
        "self_call": receiver is not None and hooks.is_self(receiver), "receiver_local": local,
    })

