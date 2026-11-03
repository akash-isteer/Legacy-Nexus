"""Java parser: classes, interfaces, enums and records with Spring / JAX-RS endpoints, JPA entities,
Spring Data repositories, JDBC SQL strings, constants and main() entry points."""
from __future__ import annotations

from app.parsers.code.scan import Hooks, Scope, scan_body
from app.parsers.code.tsutil import (
    data_item, end_line, endpoint, field, finalise, generic_args, join_paths, line, literal_value,
    new_unit, parse_tree, preceding_comment, simple_type, snake_upper, txt, walk,
)

CLASS_TYPES = {"class_declaration": "class", "interface_declaration": "interface", "enum_declaration": "enum",
               "record_declaration": "record"}
SPRING_VERBS = {"GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT", "DeleteMapping": "DELETE",
                "PatchMapping": "PATCH"}
JAXRS_VERBS = {"GET", "POST", "PUT", "DELETE", "PATCH"}
REPOSITORY_BASES = {"JpaRepository", "CrudRepository", "PagingAndSortingRepository", "ListCrudRepository",
                    "MongoRepository", "ReactiveCrudRepository"}


def _string(node) -> str | None:
    """Value of a string literal, text block or a '+' concatenation made only of string literals."""
    if node.type in {"string_literal", "text_block"}:
        raw = txt(node)
        return raw[3:-3] if raw.startswith('"""') else raw[1:-1]
    if node.type == "binary_expression" and txt(field(node, "operator")) == "+":
        left, right = _string(field(node, "left")), _string(field(node, "right"))
        if left is not None and right is not None:
            return left + right
    return None


def _string_value(node) -> str | None:
    if node.type not in {"string_literal", "text_block", "binary_expression"}:
        return None
    parent = node.parent
    if parent is not None and parent.type == "binary_expression" and _string(parent) is not None:
        return None  # the enclosing concatenation is reported once, as a whole
    return _string(node)


def _annotations(node) -> list[tuple[str, list[tuple[str | None, str]], object]]:
    """[(name, [(key, string value)], node)] for the annotations in a declaration's modifiers."""
    out = []
    mods = next((c for c in node.children if c.type == "modifiers"), None)
    for a in (mods.children if mods else []):
        if a.type not in {"annotation", "marker_annotation"}:
            continue
        values = []
        args = field(a, "arguments")
        for arg in (args.named_children if args else []):
            if arg.type == "element_value_pair":
                key, val = txt(field(arg, "key")), field(arg, "value")
                values += [(key, s) for s in _strings_in(val)]
                if val is not None and val.type in {"field_access", "identifier"}:
                    values.append((key, txt(val)))
            else:
                values += [(None, s) for s in _strings_in(arg)]
                if arg.type in {"field_access", "identifier"}:
                    values.append((None, txt(arg)))
        out.append((txt(field(a, "name")).split(".")[-1], values, a))
    return out


def _strings_in(node) -> list[str]:
    if node is None:
        return []
    return [s for s in (_string(n) for n in walk(node) if n.type == "string_literal") if s is not None]


def _first(values, keys=(None, "value", "path", "name")) -> str | None:
    return next((v for k, v in values if k in keys and not v.startswith("RequestMethod")), None)


def _is_modifier(node, word: str) -> bool:
    mods = next((c for c in node.children if c.type == "modifiers"), None)
    return mods is not None and any(txt(c) == word for c in mods.children)


def _hooks(field_types: dict) -> Hooks:
    def call_parts(n):
        if n.type == "method_invocation":
            return field(n, "object"), txt(field(n, "name"))
        return None

    def new_type(n):
        return simple_type(txt(field(n, "type"))) if n.type == "object_creation_expression" else None

    def if_parts(n):
        if n.type == "if_statement":
            return field(n, "condition"), field(n, "consequence"), field(n, "alternative")
        return None

    def assign_parts(n):
        if n.type == "assignment_expression":
            op = txt(field(n, "operator"))
            left, right = txt(field(n, "left")), txt(field(n, "right"))
            return (left, f"{left} {op[0]} {right}") if op in {"+=", "-=", "*=", "/="} else (left, right)
        return None

    def local_decls(n):
        if n.type != "local_variable_declaration":
            return []
        typ = simple_type(txt(field(n, "type")))
        return [(txt(field(d, "name")), typ, field(d, "value"))
                for d in n.named_children if d.type == "variable_declarator"]

    def receiver_type(r, scope, unit):
        if r.type == "identifier":
            name = txt(r)
            if name in scope.types:
                return scope.types[name]
            if name in field_types:
                return field_types[name]
            return name if name[:1].isupper() else None  # static call: Type.method()
        if r.type == "field_access" and txt(field(r, "object")) == "this":
            return field_types.get(txt(field(r, "field")))
        if r.type == "object_creation_expression":
            return new_type(r)
        return None

    return Hooks(
        nested={"class_declaration", "interface_declaration", "enum_declaration", "record_declaration",
                "class_body"},
        loops={"for_statement", "enhanced_for_statement", "while_statement", "do_statement"},
        call_parts=call_parts, new_type=new_type, if_parts=if_parts, assign_parts=assign_parts,
        local_decls=local_decls, string_value=_string_value, receiver_type=receiver_type,
        is_self=lambda r: r is not None and r.type == "this",
    )


def parse_java(path: str, text: str) -> dict:
    root = parse_tree("java", text).root_node
    lines = text.split("\n")
    pkg = next((txt(c).replace("package", "").strip(" ;") for c in root.named_children
                if c.type == "package_declaration"), "")
    units = []
    for node in walk(root):
        if node.type in CLASS_TYPES:
            units.append(_unit(node, path, pkg, lines))
    return {"units": units}


def _unit(node, path: str, pkg: str, lines: list[str]) -> dict:
    name = txt(field(node, "name"))
    u = new_unit(path, name, "java", CLASS_TYPES[node.type], line(node), end_line(node), len(lines))
    u["qualified_name"] = f"{pkg}.{name}" if pkg else name
    u["purpose"] = preceding_comment(node, lines)
    u["header_comment"] = u["purpose"]

    for key in ("superclass", "interfaces"):
        part = field(node, key)
        if part is not None:
            for t in walk(part):
                if t.type in {"type_identifier", "generic_type", "scoped_type_identifier"} and t.parent.type != "generic_type":
                    base = simple_type(txt(t))
                    if base:
                        u["extends"].append(base)
                        u["type_refs"].append({"type": base, "line": line(t), "how": "extends"})
                    if base in REPOSITORY_BASES and generic_args(txt(t)):
                        u["repository_entity"] = generic_args(txt(t))[0]
    ext = next((c for c in node.children if c.type == "extends_interfaces"), None)  # interface X extends Y<T>
    if ext is not None:
        for t in ext.named_children[0].named_children if ext.named_children else []:
            base = simple_type(txt(t))
            if base:
                u["extends"].append(base)
                u["type_refs"].append({"type": base, "line": line(t), "how": "extends"})
            if base in REPOSITORY_BASES and generic_args(txt(t)):
                u["repository_entity"] = generic_args(txt(t))[0]

    annotations = {a: v for a, v, _ in _annotations(node)}
    base_path = _first(annotations.get("RequestMapping", [])) or _first(annotations.get("Path", [])) or ""
    if "Entity" in annotations or "Table" in annotations:
        u["entity_table"] = (_first(annotations.get("Table", []), keys=("name", None, "value")) or name).upper()

    body = field(node, "body")
    members = body.named_children if body is not None else []
    for m in members:
        if m.type == "field_declaration":
            typ = txt(field(m, "type"))
            for d in m.named_children:
                if d.type != "variable_declarator":
                    continue
                fname = txt(field(d, "name"))
                if simple_type(typ):
                    u["field_types"][fname] = simple_type(typ)
                    for t in [simple_type(typ)] + generic_args(typ):
                        u["type_refs"].append({"type": t, "line": line(m), "how": "field"})
                value = field(d, "value")
                const = literal_value(txt(value)) if value is not None and _is_modifier(m, "final") else None
                u["data_items"].append(data_item(fname, const, m))
                if u["entity_table"]:
                    col = next((_first(v, keys=("name", None)) for a, v, _ in _annotations(m) if a == "Column"), None)
                    u["entity_columns"].append({"name": col.upper() if col else snake_upper(fname), "line": line(m)})

    methods = [m for m in members if m.type in {"method_declaration", "constructor_declaration"}]
    own = {txt(field(m, "name")) for m in methods}
    hooks = _hooks(u["field_types"])
    for m in methods:
        mname = txt(field(m, "name"))
        u["paragraphs"].append({"name": mname, "start_line": line(m), "end_line": end_line(m), "is_section": False})
        scope = Scope()
        params = []
        for p in (field(m, "parameters").named_children if field(m, "parameters") else []):
            if p.type not in {"formal_parameter", "spread_parameter"}:
                continue
            pname, ptype = txt(field(p, "name")), txt(field(p, "type"))
            if simple_type(ptype):
                scope.types[pname] = simple_type(ptype)
                for t in [simple_type(ptype)] + generic_args(ptype):
                    u["type_refs"].append({"type": t, "line": line(p), "how": "parameter"})
            anns = {a: v for a, v, _ in _annotations(p)}
            if "RequestParam" in anns or "QueryParam" in anns:
                req = not any(k == "required" and v == "false" for k, v in anns.get("RequestParam", []))
                params.append({"name": _first(anns.get("RequestParam", []) or anns.get("QueryParam", [])) or pname,
                               "in": "query", "required": req})
            elif "RequestBody" in anns:
                params.append({"name": pname, "in": "body", "required": True})
        if m.type == "method_declaration" and mname == "main" and _is_modifier(m, "static"):
            u["entry_point"] = True
        for aname, values, a in _annotations(m):
            verb = SPRING_VERBS.get(aname) or (aname if aname in JAXRS_VERBS else None)
            if aname == "RequestMapping":
                verb = next((v.split(".")[-1] for k, v in values if k == "method"), "GET")
            if verb:
                sub = _first(values) or ""
                if aname in JAXRS_VERBS:
                    sub = next((_first(v) for an, v, _ in _annotations(m) if an == "Path"), "") or ""
                u["endpoints"].append(endpoint(verb, join_paths(base_path, sub), mname, name, m, params))
        scan_body(field(m, "body"), u, mname, scope, hooks, lines, own)
    return finalise(u, lines)
