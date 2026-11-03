"""C# parser: classes, interfaces, records and structs with ASP.NET controller and minimal-API endpoints,
Entity Framework entities and DbSets, raw SQL strings, constants and entry points (Main or top-level code)."""
from __future__ import annotations

from app.parsers.code.scan import Hooks, Scope, scan_body
from app.parsers.code.tsutil import (
    data_item, end_line, endpoint, field, finalise, generic_args, join_paths, line, literal_value, new_unit,
    parse_tree, preceding_comment, simple_type, txt, walk,
)

CLASS_TYPES = {"class_declaration": "class", "interface_declaration": "interface", "record_declaration": "record",
               "struct_declaration": "class", "enum_declaration": "enum", "record_struct_declaration": "record"}
HTTP_ATTRS = {"HttpGet": "GET", "HttpPost": "POST", "HttpPut": "PUT", "HttpDelete": "DELETE", "HttpPatch": "PATCH"}
MINIMAL_API = {"MapGet": "GET", "MapPost": "POST", "MapPut": "PUT", "MapDelete": "DELETE", "MapPatch": "PATCH"}
STRING_TYPES = {"string_literal", "verbatim_string_literal", "raw_string_literal"}


def _string(node) -> str | None:
    if node is None:
        return None
    if node.type == "string_literal":
        return "".join(txt(c) for c in node.named_children) if node.named_children else txt(node)[1:-1]
    if node.type == "verbatim_string_literal":
        return txt(node)[2:-1].replace('""', '"')
    if node.type == "raw_string_literal":
        return txt(node).strip('"').strip()
    if node.type == "binary_expression" and txt(field(node, "operator")) == "+":
        left, right = _string(field(node, "left")), _string(field(node, "right"))
        if left is not None and right is not None:
            return left + right
    return None


def _string_value(node) -> str | None:
    if node.type not in STRING_TYPES | {"binary_expression"}:
        return None
    parent = node.parent
    if parent is not None and parent.type == "binary_expression" and _string(parent) is not None:
        return None
    return _string(node)


def _attributes(node) -> list[tuple[str, list[str], object]]:
    """[(name without 'Attribute', [string arguments], node)] from a declaration's attribute lists."""
    out = []
    for lst in (c for c in node.children if c.type == "attribute_list"):
        for a in (c for c in lst.named_children if c.type == "attribute"):
            name = txt(field(a, "name")).split(".")[-1]
            name = name[:-9] if name.endswith("Attribute") else name
            args = [s for s in (_string(n) for n in walk(a) if n.type in STRING_TYPES) if s is not None]
            out.append((name, args, a))
    return out


def _modifiers(node) -> set[str]:
    return {txt(c) for c in node.children if c.type == "modifier"}


def _declarator_value(d):
    name = field(d, "name")
    rest = [c for c in d.named_children if c != name and c.type != "bracketed_argument_list"]
    if rest and rest[-1].type == "equals_value_clause":
        return rest[-1].named_children[0] if rest[-1].named_children else None
    return rest[-1] if rest else None


def _hooks(field_types: dict) -> Hooks:
    def call_parts(n):
        if n.type != "invocation_expression":
            return None
        fn = field(n, "function")
        if fn is None:
            return None
        if fn.type == "member_access_expression":
            return field(fn, "expression"), txt(field(fn, "name")).split("<")[0]
        if fn.type in {"identifier", "generic_name"}:
            return None, txt(fn).split("<")[0]
        return None

    def new_type(n):
        return simple_type(txt(field(n, "type"))) if n.type == "object_creation_expression" else None

    def if_parts(n):
        if n.type == "if_statement":
            return field(n, "condition"), field(n, "consequence"), field(n, "alternative")
        return None

    def assign_parts(n):
        if n.type == "assignment_expression":
            left, right = txt(field(n, "left")), txt(field(n, "right"))
            op = txt(field(n, "operator")) or "="
            return (left, f"{left} {op[0]} {right}") if op in {"+=", "-=", "*=", "/="} else (left, right)
        return None

    def local_decls(n):
        if n.type != "local_declaration_statement":
            return []
        out = []
        for vd in (c for c in n.named_children if c.type == "variable_declaration"):
            typ = simple_type(txt(field(vd, "type")))
            for d in (c for c in vd.named_children if c.type == "variable_declarator"):
                out.append((txt(field(d, "name")), typ, _declarator_value(d)))
        return out

    def type_of(r, scope):
        if r.type == "identifier":
            name = txt(r)
            if name in scope.types:
                return scope.types[name]
            if name in field_types:
                return field_types[name]
            return name if name[:1].isupper() else None
        if r.type == "member_access_expression" and field(r, "expression").type == "this_expression":
            return field_types.get(txt(field(r, "name")))
        if r.type == "object_creation_expression":
            return new_type(r)
        return None

    def member_of(r, scope, unit):
        if r.type == "member_access_expression" and field(r, "expression").type != "this_expression":
            root = type_of(field(r, "expression"), scope)
            if root:
                return {"type": root, "member": txt(field(r, "name"))}
        return None

    return Hooks(
        nested=set(CLASS_TYPES),
        loops={"for_statement", "foreach_statement", "while_statement", "do_statement"},
        call_parts=call_parts, new_type=new_type, if_parts=if_parts, assign_parts=assign_parts,
        local_decls=local_decls, string_value=_string_value,
        receiver_type=lambda r, scope, unit: type_of(r, scope),
        is_self=lambda r: r is not None and r.type in {"this_expression", "base_expression"},
        member_of=member_of,
    )


def parse_csharp(path: str, text: str) -> dict:
    root = parse_tree("csharp", text).root_node
    lines = text.split("\n")
    units = []
    for node in walk(root):
        if node.type in CLASS_TYPES:
            units.append(_unit(node, path, _namespace(node), lines))
    top = [c for c in root.named_children if c.type == "global_statement"]
    if top:  # Program.cs with top-level statements (minimal APIs)
        units.append(_top_level(top, path, lines))
    return {"units": units}


def _namespace(node) -> str:
    n = node.parent
    while n is not None:
        if n.type in {"namespace_declaration", "file_scoped_namespace_declaration"}:
            return txt(field(n, "name"))
        n = n.parent
    root = node
    while root.parent is not None:
        root = root.parent
    fs = next((c for c in root.named_children if c.type == "file_scoped_namespace_declaration"), None)
    return txt(field(fs, "name")) if fs is not None else ""


def _unit(node, path: str, ns: str, lines: list[str]) -> dict:
    name = txt(field(node, "name"))
    u = new_unit(path, name, "csharp", CLASS_TYPES[node.type], line(node), end_line(node), len(lines))
    u["qualified_name"] = f"{ns}.{name}" if ns else name
    u["purpose"] = preceding_comment(node, lines)
    u["header_comment"] = u["purpose"]
    bases = next((c for c in node.children if c.type == "base_list"), None)
    for b in (bases.named_children if bases else []):
        base = simple_type(txt(b))
        if base:
            u["extends"].append(base)
            u["type_refs"].append({"type": base, "line": line(b), "how": "extends"})

    attrs = {a: v for a, v, _ in _attributes(node)}
    resource = name[:-10] if name.endswith("Controller") else name
    base_route = (attrs.get("Route") or [""])[0].replace("[controller]", resource.lower())
    if "Table" in attrs:
        u["entity_table"] = (attrs["Table"] or [name])[0].upper()

    body = field(node, "body")
    members = body.named_children if body is not None else []
    for m in members:
        if m.type == "field_declaration":
            vd = next((c for c in m.named_children if c.type == "variable_declaration"), None)
            if vd is None:
                continue
            typ = txt(field(vd, "type"))
            for d in (c for c in vd.named_children if c.type == "variable_declarator"):
                fname = txt(field(d, "name"))
                _remember_type(u, fname, typ, m)
                value = _declarator_value(d)
                const = literal_value(txt(value)) if value is not None and _modifiers(m) & {"const", "readonly"} else None
                u["data_items"].append(data_item(fname, const, m))
        elif m.type == "property_declaration":
            pname, typ = txt(field(m, "name")), txt(field(m, "type"))
            _remember_type(u, pname, typ, m)
            u["data_items"].append(data_item(pname, None, m))
            if simple_type(typ) == "DbSet" and generic_args(typ):
                u["dbsets"][pname] = generic_args(typ)[0]
            if u["entity_table"]:
                col = next((v[0] for a, v, _ in _attributes(m) if a == "Column" and v), None)
                u["entity_columns"].append({"name": (col or pname).upper(), "line": line(m)})

    methods = [m for m in members if m.type in {"method_declaration", "constructor_declaration"}]
    own = {txt(field(m, "name")) for m in methods}
    hooks = _hooks(u["field_types"])
    for m in methods:
        mname = txt(field(m, "name"))
        u["paragraphs"].append({"name": mname, "start_line": line(m), "end_line": end_line(m), "is_section": False})
        scope = Scope()
        params = []
        plist = field(m, "parameters")
        for p in (plist.named_children if plist else []):
            if p.type != "parameter":
                continue
            pname, ptype = txt(field(p, "name")), txt(field(p, "type"))
            if simple_type(ptype):
                scope.types[pname] = simple_type(ptype)
                for t in [simple_type(ptype)] + generic_args(ptype):
                    u["type_refs"].append({"type": t, "line": line(p), "how": "parameter"})
            pattrs = {a for a, _, _ in _attributes(p)}
            if "FromQuery" in pattrs:
                params.append({"name": pname, "in": "query", "required": "?" not in ptype})
            elif "FromBody" in pattrs:
                params.append({"name": pname, "in": "body", "required": True})
        if mname == "Main" and "static" in _modifiers(m):
            u["entry_point"] = True
        mattrs = _attributes(m)
        method_route = next((v[0] for a, v, _ in mattrs if a == "Route" and v), "")
        for aname, values, _ in mattrs:
            verb = HTTP_ATTRS.get(aname)
            if verb:
                sub = (values[0] if values else method_route).replace("[action]", mname)
                u["endpoints"].append(endpoint(verb, join_paths(base_route, sub), mname, name, m, params))
        body_node = field(m, "body") or next((c for c in m.named_children if c.type == "arrow_expression_clause"), None)
        scan_body(body_node, u, mname, scope, hooks, lines, own)
    return finalise(u, lines)


def _remember_type(u: dict, name: str, typ: str, node) -> None:
    st = simple_type(typ)
    if st:
        u["field_types"][name] = st
        for t in [st] + generic_args(typ):
            u["type_refs"].append({"type": t, "line": line(node), "how": "field"})


def _top_level(statements: list, path: str, lines: list[str]) -> dict:
    u = new_unit(path, "Program", "csharp", "module", line(statements[0]), end_line(statements[-1]), len(lines))
    u["entry_point"] = True
    u["paragraphs"].append({"name": "Main", "start_line": u["start_line"], "end_line": u["end_line"],
                            "is_section": False})
    hooks = _hooks(u["field_types"])
    scope = Scope()
    for st in statements:
        for n in walk(st):
            if n.type == "invocation_expression":
                fn = field(n, "function")
                verb = MINIMAL_API.get(txt(field(fn, "name"))) if fn is not None and fn.type == "member_access_expression" else None
                args = field(n, "arguments")
                route = next((_string(a.named_children[0]) for a in (args.named_children if args else [])
                              if a.named_children and a.named_children[0].type in STRING_TYPES), None)
                if verb and route:
                    u["endpoints"].append(endpoint(verb, route, f"{verb} {route}", "Program", n))
        scan_body(st, u, "Main", scope, hooks, lines, set())
    return finalise(u, lines)
