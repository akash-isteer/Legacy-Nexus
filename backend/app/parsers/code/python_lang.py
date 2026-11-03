"""Python parser: classes and modules with FastAPI / Flask routes, SQLAlchemy and Django models, SQL strings,
constants and ``__main__`` entry points."""
from __future__ import annotations

from pathlib import PurePosixPath

from app.parsers.code.scan import Hooks, Scope, scan_body
from app.parsers.code.tsutil import (
    data_item, doc_text, end_line, endpoint, field, finalise, line, literal_value, new_unit, parse_tree, txt, walk,
)

ROUTE_METHODS = {"get": "GET", "post": "POST", "put": "PUT", "delete": "DELETE", "patch": "PATCH"}
FRAMEWORK_PARAMS = {"self", "cls", "request", "req", "db", "session", "background_tasks", "response"}


def _string(node) -> str | None:
    if node is None:
        return None
    if node.type == "string":
        return "".join(txt(c) for c in node.named_children if c.type in {"string_content", "escape_sequence"})
    if node.type == "concatenated_string":
        parts = [_string(c) for c in node.named_children]
        return "".join(p for p in parts if p is not None)
    return None


def _string_value(node) -> str | None:
    if node.type == "string" and node.parent is not None and node.parent.type == "concatenated_string":
        return None
    if node.type in {"string", "concatenated_string"}:
        return _string(node)
    return None


def _unwrap(stmt):
    """Grammar versions differ on wrapping expressions in expression_statement; accept both."""
    if stmt is not None and stmt.type == "expression_statement" and stmt.named_children:
        return stmt.named_children[0]
    return stmt


def _docstring(block) -> str:
    first = _unwrap(block.named_children[0]) if block is not None and block.named_children else None
    if first is not None and first.type == "string":
        return doc_text(_string(first) or "")
    return ""


def _module_name(path: str) -> str:
    p = PurePosixPath(path)
    return f"{p.parent.name}.{p.stem}" if p.parent.name else p.stem


def _callee_name(fn) -> str | None:
    if fn is None:
        return None
    if fn.type == "identifier":
        return txt(fn)
    if fn.type == "attribute":
        return txt(field(fn, "attribute"))
    return None


def _hooks(field_types: dict, classes_here: set[str]) -> Hooks:
    def is_class_name(name: str | None) -> bool:
        return bool(name) and (name[:1].isupper() or name in classes_here)

    def call_parts(n):
        if n.type != "call":
            return None
        fn = field(n, "function")
        if fn is not None and fn.type == "attribute":
            return field(fn, "object"), txt(field(fn, "attribute"))
        if fn is not None and fn.type == "identifier":
            return None, txt(fn)
        return None

    def constructor_call(n):
        if n is not None and n.type == "call":
            fn = field(n, "function")
            name = _callee_name(fn)
            if is_class_name(name) and (fn.type == "identifier" or fn.type == "attribute"):
                return name
        return None

    def if_parts(n):
        if n.type in {"if_statement", "elif_clause"}:
            alt = field(n, "alternative")
            return field(n, "condition"), field(n, "consequence"), alt if alt is not None and alt.type == "else_clause" else None
        return None

    def assign_parts(n):
        if n.type == "augmented_assignment":
            left, right, op = txt(field(n, "left")), txt(field(n, "right")), txt(field(n, "operator"))
            return left, f"{left} {op[0]} {right}"
        if n.type == "assignment" and field(n, "right") is not None and field(n, "left").type == "attribute":
            return txt(field(n, "left")), txt(field(n, "right"))
        return None

    def local_decls(n):
        if n.type == "assignment" and field(n, "left") is not None and field(n, "left").type == "identifier":
            ann = field(n, "type")
            return [(txt(field(n, "left")), txt(ann).split("[")[0] if ann is not None else None, field(n, "right"))]
        return []

    def receiver_type(r, scope, unit):
        if r.type == "identifier":
            name = txt(r)
            if name in scope.types:
                return scope.types[name]
            return name if is_class_name(name) else None
        if r.type == "attribute" and txt(field(r, "object")) == "self":
            return field_types.get(txt(field(r, "attribute")))
        if r.type == "call":
            return constructor_call(r)
        return None

    def arg_types(n, scope):
        args = field(n, "arguments")
        return [t for t in (constructor_call(a) for a in (args.named_children if args else [])) if t]

    return Hooks(
        nested={"class_definition", "function_definition", "decorated_definition"},
        loops={"for_statement", "while_statement"},
        call_parts=call_parts, new_type=lambda n: None, if_parts=if_parts, assign_parts=assign_parts,
        local_decls=local_decls, string_value=_string_value, receiver_type=receiver_type,
        is_self=lambda r: r is not None and r.type == "identifier" and txt(r) in {"self", "cls"},
        constructor_call=constructor_call, arg_types=arg_types,
    )


def parse_python(path: str, text: str) -> dict:
    root = parse_tree("python", text).root_node
    lines = text.split("\n")
    classes, functions, other = [], [], []
    for c in root.named_children:
        inner = field(c, "definition") if c.type == "decorated_definition" else c
        if inner is not None and inner.type == "class_definition":
            classes.append((c, inner))
        elif inner is not None and inner.type == "function_definition":
            functions.append((c, inner))
        else:
            other.append(c)

    constants = {}
    const_items = []
    for c in other:
        a = _unwrap(c)
        if a is not None and a.type == "assignment" and field(a, "left").type == "identifier":
            value = literal_value(txt(field(a, "right"))) if field(a, "right") is not None else None
            name = txt(field(a, "left"))
            if value is not None and name.isupper():
                constants[name] = value
                const_items.append(data_item(name, value, a, "MODULE"))

    class_names = {txt(field(inner, "name")) for _, inner in classes}
    units = [_class_unit(outer, inner, path, lines, const_items, class_names) for outer, inner in classes]

    main_block = next((c for c in other if c.type == "if_statement" and "__name__" in txt(field(c, "condition"))), None)
    if functions or main_block is not None:
        units.append(_module_unit(root, functions, main_block, path, lines, const_items, class_names))
    return {"units": units}


def _class_unit(outer, node, path, lines, const_items, class_names) -> dict:
    name = txt(field(node, "name"))
    u = new_unit(path, name, "python", "class", line(outer), end_line(node), len(lines))
    u["qualified_name"] = f"{_module_name(path)}.{name}"
    body = field(node, "body")
    u["purpose"] = _docstring(body)
    u["header_comment"] = u["purpose"]
    supers = field(node, "superclasses")
    for s in (supers.named_children if supers else []):
        if s.type in {"identifier", "attribute"}:
            base = txt(s).split(".")[-1]
            u["extends"].append(base)
            u["type_refs"].append({"type": base, "line": line(s), "how": "extends"})
    u["data_items"] += [dict(d) for d in const_items]

    members = body.named_children if body is not None else []
    methods = []
    for m in members:
        inner = field(m, "definition") if m.type == "decorated_definition" else m
        if inner is not None and inner.type == "function_definition":
            methods.append((m, inner))
        elif _unwrap(m) is not None and _unwrap(m).type == "assignment":
            a = _unwrap(m)
            target, value = txt(field(a, "left")), field(a, "right")
            if target == "__tablename__":
                u["entity_table"] = (_string(value) or name).upper()
            elif value is not None:
                u["data_items"].append(data_item(target, literal_value(txt(value)), a))
                if value.type == "call" and txt(field(value, "function")).split(".")[-1] in {"Column", "mapped_column"}:
                    u["entity_columns"].append({"name": target.upper(), "line": line(a)})
    if "Model" in u["extends"] and not u["entity_table"]:  # Django model
        u["entity_table"] = name.upper()

    # self.x = Type(...) in __init__ gives attribute types
    for _, fn in methods:
        if txt(field(fn, "name")) == "__init__":
            for n in walk(fn):
                if n.type == "assignment" and field(n, "left") is not None and field(n, "left").type == "attribute" \
                        and txt(field(field(n, "left"), "object")) == "self":
                    attr = txt(field(field(n, "left"), "attribute"))
                    right = field(n, "right")
                    callee = _callee_name(field(right, "function")) if right is not None and right.type == "call" else None
                    if callee and (callee[:1].isupper() or callee in class_names):
                        u["field_types"][attr] = callee
            for p in (field(fn, "parameters").named_children if field(fn, "parameters") else []):
                ann = field(p, "type")
                pname = txt(p.named_children[0]) if p.named_children else txt(p)
                if ann is not None and txt(ann).split("[")[0].split("|")[0].strip()[:1].isupper():
                    u["field_types"].setdefault(pname, txt(ann).split("[")[0].split("|")[0].strip())

    own = {txt(field(fn, "name")) for _, fn in methods}
    hooks = _hooks(u["field_types"], class_names)
    for outer_m, fn in methods:
        _function(u, outer_m, fn, hooks, lines, own, class_scope=True)
    return finalise(u, lines)


def _module_unit(root, functions, main_block, path, lines, const_items, class_names) -> dict:
    name = _module_name(path)
    u = new_unit(path, name, "python", "module", 1, len(lines), len(lines))
    u["purpose"] = _docstring(root)
    u["header_comment"] = u["purpose"]
    u["data_items"] += [dict(d) for d in const_items]
    own = {txt(field(fn, "name")) for _, fn in functions}
    hooks = _hooks(u["field_types"], class_names)
    for outer, fn in functions:
        _function(u, outer, fn, hooks, lines, own, class_scope=False)
    if main_block is not None:
        u["entry_point"] = True
        u["paragraphs"].append({"name": "__main__", "start_line": line(main_block), "end_line": end_line(main_block),
                                "is_section": False})
        scan_body(field(main_block, "consequence"), u, "__main__", Scope(), hooks, lines, own)
    return finalise(u, lines)


def _function(u, outer, fn, hooks, lines, own, class_scope: bool) -> None:
    fname = txt(field(fn, "name"))
    u["paragraphs"].append({"name": fname, "start_line": line(outer), "end_line": end_line(fn), "is_section": False})
    scope = Scope()
    params = []
    for p in (field(fn, "parameters").named_children if field(fn, "parameters") else []):
        pname = txt(field(p, "name") or (p.named_children[0] if p.named_children else p))
        scope.names.add(pname)
        ann = field(p, "type")
        if ann is not None:
            t = txt(ann).split("[")[0].split("|")[0].strip()
            if t[:1].isupper():
                scope.types[pname] = t
                u["type_refs"].append({"type": t, "line": line(p), "how": "parameter"})
        if pname not in FRAMEWORK_PARAMS and not pname.startswith("*"):
            has_default = p.type in {"default_parameter", "typed_default_parameter"}
            params.append({"name": pname, "in": "query", "required": not has_default})
    if outer.type == "decorated_definition":
        for dec in (c for c in outer.named_children if c.type == "decorator"):
            call = dec.named_children[0] if dec.named_children else None
            if call is None or call.type != "call" or field(call, "function").type != "attribute":
                continue
            verb_name = txt(field(field(call, "function"), "attribute"))
            args = field(call, "arguments")
            route = next((_string(a) for a in (args.named_children if args else []) if a.type == "string"), None)
            if route is None:
                continue
            if verb_name in ROUTE_METHODS:
                verbs = [ROUTE_METHODS[verb_name]]
            elif verb_name == "route":  # Flask: @app.route("/x", methods=["POST"])
                methods_arg = next((a for a in args.named_children if a.type == "keyword_argument"
                                    and txt(field(a, "name")) == "methods"), None)
                verbs = [s.upper() for s in (_string(n) for n in walk(methods_arg) if n.type == "string") if s] \
                    if methods_arg is not None else ["GET"]
            else:
                continue
            for verb in verbs:
                u["endpoints"].append(endpoint(verb, route, fname, u["program_id"], fn, params))
    scan_body(field(fn, "body"), u, fname, scope, hooks, lines, own)
