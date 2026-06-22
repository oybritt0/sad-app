import io, shutil
p = "module_15_walkshed.py"
shutil.copyfile(p, p + ".bak2")
src = io.open(p, encoding="utf-8").read()
reps = [
 ("CONCAVE_RATIO = 0.25", "CONCAVE_RATIO = 0.7"),
 ("SMOOTHING_M = 25.0", "SMOOTHING_M = 60.0"),
 ("-smoothing_m * 0.6, resolution=8)", "-smoothing_m * 0.35, resolution=8)"),
]
miss = [a for a,_ in reps if a not in src]
if miss:
    raise SystemExit("not found: " + " | ".join(miss))
for a,b in reps:
    src = src.replace(a, b, 1)
io.open(p, "w", encoding="utf-8").write(src)
print("patched OK (backup at module_15_walkshed.py.bak2)")
