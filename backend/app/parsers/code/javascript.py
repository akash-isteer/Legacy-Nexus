"""JavaScript / TypeScript parser: classes and modules with Express-style routes, NestJS controllers,
SQL strings, constants and script entry points."""
from __future__ import annotations

from pathlib import PurePosixPath

from app.parsers.code.scan import Hooks, Scope, scan_body
from app.parsers.code.tsutil import (
    data_item, end_line, endpoint, field, finalise, join_paths, line, literal_value, new_unit, parse_tree,
    preceding_comment, simple_type, txt, walk,
)

ROUTE_METHODS = {"get": "GET", "post": "POST", "put": "PUT", "delete": "DELETE", "patch": "PATCH"}
NEST_VERBS = {"Get": "GET", "Post": "POST", "Put": "PUT", "Delete": "DELETE", "Patch": "PATCH"}
FUNCTION_VALUES = {"arrow_function", "function_expression", "function"}
CLASS_NODES = {"class_declaration", "class", "abstract_class_declaration"}


def grammar_for(path: str) -> str:
    suffix = PurePosixPath(path).suffix.lower()
    return {".ts": "typescript", ".mts": "typescript", ".cts": "typescript", ".tsx": "tsx"}.get(suffix, "javascript")


def _string(node) -> str | None:
    if node is None:
        return None
    if node.type == "string":
        return "".join(txt(c) for c in node.named_children if c.type in {"string_fragment", "escape_sequence"})
    if node.type == "template_string" and not any(c.type == "template_substitution" for c in node.named_children):
        return txt(node)[1:-1]
    if node.type == "binary_expression" and txt(field(node, "operator")) == "+":
        left, right = _string(field(node, "left")), _string(field(node, "right"))
        if left is not None and right is not None:
            return left + right
    return None


def _string_value(node) -> str | None:
    if node.type not in {"string", "template_string", "binary_expression"}:
        return None
    parent = node.parent
    if parent is not None and parent.type == "binary_expression" and _string(parent) is not None:
        return None
    return _string(node)


def _module_name(path: str) -> str:
    p = PurePosixPath(path)
    return f"{p.parent.name}/{p.stem}" if p.parent.name else p.stem


def _hooks(field_types: dict, module_types: dict) -> Hooks:
    def call_parts(n):
        if n.type != "call_expression":
            return None
        fn = field(n, "function")
        if fn is not None and fn.type == "member_expression":
            return field(fn, "object"), txt(field(fn, "property"))
        if fn is not None and fn.type == "identifier":
            return None, txt(fn)
        return None

    def new_type(n):
        return simple_type(txt(field(n, "constructor"))) if n.type == "new_expression" else None

    def if_parts(n):
        if n.type == "if_statement":
            return field(n, "condition"), field(n, "consequence"), field(n, "alternative")
        return None

    def assign_parts(n):
        if n.type == "augmented_assignment_expression":
            left, right, op = txt(field(n, "left")), txt(field(n, "right")), txt(field(n, "operator"))
            return left, f"{left} {op[0]} {right}"
        if n.type == "assignment_expression":
            return txt(field(n, "left")), txt(field(n, "right"))
        return None

    def local_decls(n):
        if n.type not in {"lexical_declaration", "variable_declaration"}:
            return []
        out = []
        for d in (c for c in n.named_children if c.type == "variable_declarator"):
            ann = field(d, "type")
            typ = simple_type(txt(ann).lstrip(": ")) if ann is not None else None
            out.append((txt(field(d, "name")), typ, field(d, "value")))
        return out

    def receiver_type(r, scope, unit):
        if r.type == "identifier":
            name = txt(r)
            return scope.types.get(name) or module_types.get(name) or (name if name[:1].isupper() else None)
        if r.type == "member_expression" and txt(field(r, "object")) == "this":
            return field_types.get(txt(field(r, "property")))
        if r.type == "new_expression":
            return new_type(r)
        return None

    return Hooks(
        nested=CLASS_NODES | {"function_declaration", "method_definition"},
        loops={"for_statement", "for_in_statement", "while_statement", "do_statement"},
        call_parts=call_parts, new_type=new_type, if_parts=if_parts, assign_parts=assign_parts,
        local_decls=local_decls, string_value=_string_value, receiver_type=receiver_type,
        is_self=lambda r: r is not None and r.type == "this",
    )


def _decorators(node) -> list[tuple[str, str | None]]:
    out = []
    for d in (c for c in node.children if c.type == "decorator"):
        call = d.named_children[0] if d.named_children else None
        if call is not None and call.type == "call_expression":
            args = field(call, "arguments")
            first = next((_string(a) for a in (args.named_children if args else []) if _string(a) is not None), None)
            out.append((txt(field(call, "function")), first))
        elif call is not None:
            out.append((txt(call), None))
    return out


def parse_javascript(path: str, text: str) -> dict:
    root = parse_tree(grammar_for(path), text).root_node
    lines = text.split("\n")

    module_types: dict[str, str] = {}
    constants = []
    functions: list[tuple[str, object, object]] = []  # (name, declaration node, function node)
    classes = []
    statements = []
    for c in root.named_children:
        node = field(c, "declaration") if c.type == "export_statement" and field(c, "declaration") is not None else c
        if node.type in CLASS_NODES:
            classes.append(node)
        elif node.type in {"function_declaration", "generator_function_declaration"}:
            functions.append((txt(field(node, "name")), node, node))
        elif node.type in {"lexical_declaration", "variable_declaration"}:
            for d in (x for x in node.named_children if x.type == "variable_declarator"):
                name, value = txt(field(d, "name")), field(d, "value")
                if value is None:
                    continue
                if value.type in FUNCTION_VALUES:
                    functions.append((name, node, value))
                elif value.type == "new_expression":
                    module_types[name] = simple_type(txt(field(value, "constructor"))) or ""
                elif value.type == "class":
                    classes.append(value)
                elif literal_value(txt(value)) is not None and name.isupper():
                    constants.append(data_item(name, literal_value(txt(value)), d, "MODULE"))
                else:
                    statements.append(node)
        elif node.type not in {"import_statement", "comment", "export_statement"}:
            statements.append(node)

    units = [_class_unit(c, path, lines, constants, module_types) for c in classes]
    routes = [s for s in statements if _route_call(s)]
    script_calls = [s for s in statements if s.type == "expression_statement" and not _route_call(s)]
    if functions or routes or (script_calls and not classes):
        units.append(_module_unit(path, lines, functions, routes, script_calls, constants, module_types))
    return {"units": units}


def _route_call(stmt):
    """router.get('/path', handler) / app.post(...) at module level -> (verb, path, call node)."""
    expr = stmt.named_children[0] if stmt.type == "expression_statement" and stmt.named_children else None
    if expr is None or expr.type != "call_expression":
        return None
    fn = field(expr, "function")
    if fn is None or fn.type != "member_expression":
        return None
    verb = ROUTE_METHODS.get(txt(field(fn, "property")))
    args = field(expr, "arguments")
    first = args.named_children[0] if args is not None and args.named_children else None
    path = _string(first)
    if verb and path is not None and path.startswith("/"):
        return verb, path, expr
    return None


def _class_unit(node, path, lines, constants, module_types) -> dict:
    name = txt(field(node, "name")) or PurePosixPath(path).stem
    u = new_unit(path, name, "typescript" if grammar_for(path) != "javascript" else "javascript", "class",
                 line(node), end_line(node), len(lines))
    u["qualified_name"] = f"{_module_name(path)}.{name}"
    u["purpose"] = preceding_comment(node.parent if node.parent is not None and node.parent.type == "export_statement"
                                     else node, lines)
    u["header_comment"] = u["purpose"]
    u["data_items"] += [dict(d) for d in constants]
    heritage = next((c for c in node.children if c.type == "class_heritage"), None)
    for t in (walk(heritage) if heritage is not None else []):
        if t.type in {"identifier", "type_identifier"} and t.parent.type in {"extends_clause", "implements_clause",
                                                                             "class_heritage"}:
            u["extends"].append(txt(t))
            u["type_refs"].append({"type": txt(t), "line": line(t), "how": "extends"})

    decorated = node.parent if node.parent is not None and node.parent.type == "export_statement" else node
    base = next((p for n, p in _decorators(decorated) + _decorators(node) if n == "Controller"), None)
    body = field(node, "body")
    members = body.named_children if body is not None else []
    for m in members:
        if m.type in {"public_field_definition", "field_definition"}:
            fname = txt(field(m, "name") or field(m, "property"))
            ann = field(m, "type")
            value = field(m, "value")
            if ann is not None and simple_type(txt(ann).lstrip(": ")):
                u["field_types"][fname] = simple_type(txt(ann).lstrip(": "))
            elif value is not None and value.type == "new_expression":
                u["field_types"][fname] = simple_type(txt(field(value, "constructor")))
            u["data_items"].append(data_item(fname, literal_value(txt(value)) if value is not None else None, m))

    methods = [m for m in members if m.type == "method_definition"]
    own = {txt(field(m, "name")) for m in methods}
    for m in methods:  # constructor(private readonly svc: Service) declares typed fields
        if txt(field(m, "name")) == "constructor":
            for p in (field(m, "parameters").named_children if field(m, "parameters") else []):
                ann = field(p, "type")
                pname = txt(field(p, "pattern"))
                if ann is not None and simple_type(txt(ann).lstrip(": ")):
                    u["field_types"][pname] = simple_type(txt(ann).lstrip(": "))
                    u["type_refs"].append({"type": u["field_types"][pname], "line": line(p), "how": "field"})
    hooks = _hooks(u["field_types"], module_types)
    for m in methods:
        mname = txt(field(m, "name"))
        u["paragraphs"].append({"name": mname, "start_line": line(m), "end_line": end_line(m), "is_section": False})
        scope = Scope()
        for p in (field(m, "parameters").named_children if field(m, "parameters") else []):
            scope.names.add(txt(field(p, "pattern") or p))
            ann = field(p, "type")
            if ann is not None and simple_type(txt(ann).lstrip(": ")):
                scope.types[txt(field(p, "pattern"))] = simple_type(txt(ann).lstrip(": "))
        if base is not None:
            for dname, sub in _decorators(m):
                if dname in NEST_VERBS:
                    u["endpoints"].append(endpoint(NEST_VERBS[dname], join_paths(base, sub or ""), mname, name, m))
        scan_body(field(m, "body"), u, mname, scope, hooks, lines, own)
    return finalise(u, lines)


def _module_unit(path, lines, functions, routes, script_calls, constants, module_types) -> dict:
    name = _module_name(path)
    u = new_unit(path, name, "typescript" if grammar_for(path) != "javascript" else "javascript", "module",
                 1, len(lines), len(lines))
    first_comment = next((ln.strip().lstrip("/* ").strip() for ln in lines if ln.strip().startswith(("//", "/*"))), "")
    u["purpose"] = first_comment
    u["header_comment"] = first_comment
    u["data_items"] += [dict(d) for d in constants]
    own = {n for n, _, _ in functions}
    hooks = _hooks(u["field_types"], module_types)
    for fname, decl, fn in functions:
        u["paragraphs"].append({"name": fname, "start_line": line(decl), "end_line": end_line(decl), "is_section": False})
        scope = Scope(names={txt(field(p, "pattern") or p) for p in
                             (field(fn, "parameters").named_children if field(fn, "parameters") else [])})
        scan_body(field(fn, "body"), u, fname, scope, hooks, lines, own)
    for stmt in routes:
        verb, route, call = _route_call(stmt)
        handler = f"{ROUTE_METHODS[verb.lower()] if verb.lower() in ROUTE_METHODS else verb} {route}"
        u["paragraphs"].append({"name": handler, "start_line": line(stmt), "end_line": end_line(stmt),
                                "is_section": False})
        u["endpoints"].append(endpoint(verb, route, handler, name, stmt))
        scan_body(field(call, "arguments"), u, handler, Scope(), hooks, lines, own)
    if script_calls:
        u["paragraphs"].append({"name": "<script>", "start_line": line(script_calls[0]),
                                "end_line": end_line(script_calls[-1]), "is_section": False})
        before = len(u["performs"])
        for stmt in script_calls:
            scan_body(stmt, u, "<script>", Scope(), hooks, lines, own)
        u["entry_point"] = len(u["performs"]) > before or any(c["paragraph"] == "<script>" for c in u["raw_calls"])
    u["paragraphs"].sort(key=lambda p: p["start_line"])
    return finalise(u, lines)
