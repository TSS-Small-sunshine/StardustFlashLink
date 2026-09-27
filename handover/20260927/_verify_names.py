import ast, builtins, sys, os

TARGETS = sys.argv[1:]

def analyze(path):
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src, filename=path)
    B = set(dir(builtins))
    defined = set()
    injected = set()          # names injected via globals()["x"] = ...
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                defined.add(a.asname or a.name.split('.')[0])
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                defined.add(a.asname or a.name)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(n.name)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store,)):
            defined.add(n.id)
        elif isinstance(n, ast.arg):
            defined.add(n.arg)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            defined.add(n.name)
        elif isinstance(n, ast.Global):
            defined.update(n.names)
        elif isinstance(n, ast.Attribute):
            # globals()["key"] = value   /   g["key"] = value
            if isinstance(n.value, ast.Name) and n.value.id in ("globals", "g") and n.attr == 'setitem':
                pass
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant) \
                   and isinstance(t.slice.value, str):
                    if isinstance(t.value, ast.Name) and t.value.id in ("globals", "g"):
                        injected.add(t.slice.value)
    # comprehension targets
    for n in ast.walk(tree):
        if isinstance(n, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            for t in ast.walk(n.generators[0].target):
                if isinstance(t, ast.Name):
                    defined.add(t.id)
    # module-level annotated names
    for n in ast.walk(tree):
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            defined.add(n.target.id)

    used = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            used.setdefault(n.id, n.lineno)

    missing = sorted(k for k in used if k not in defined and k not in B and k not in injected)

    print("=" * 68)
    print(path, "  (共", len(src.splitlines()), "行)")
    print("-" * 68)
    print("globals()[...] 注入的裸名:", sorted(injected) or "（无）")
    print("未定义也未注入的裸名 (%d):" % len(missing))
    for m in missing:
        print("   L%-5d %s" % (used[m], m))
    if not missing:
        print("   （无）")


for t in TARGETS:
    analyze(t)
