import io, shutil
p = "module_15_walkshed.py"
shutil.copyfile(p, p + ".bak")
src = io.open(p, encoding="utf-8").read()
old = """    'tertiary', 'secondary', 'tertiary_link', 'secondary_link',
}"""
new = """    'tertiary', 'secondary', 'tertiary_link', 'secondary_link',
    'primary', 'primary_link',
}"""
if old not in src:
    raise SystemExit("OLD block not found - already patched or whitespace differs.")
io.open(p, "w", encoding="utf-8").write(src.replace(old, new, 1))
print("patched OK (backup at module_15_walkshed.py.bak)")
