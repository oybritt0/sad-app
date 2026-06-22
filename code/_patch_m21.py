import io, shutil
p = "module_21_transit_los.py"
shutil.copyfile(p, p + ".bak")
src = io.open(p, encoding="utf-8").read()
old = """    def read(table):
        hits = [n for n in z.namelist() if n.lower().endswith(table)]
        if not hits:
            return None
        with z.open(hits[0]) as fh:
            return pd.read_csv(fh, dtype=str, low_memory=False)"""
new = """    def read(table):
        # exact basename match - SEPTA ships route_stops.txt next to
        # stops.txt; endswith('stops.txt') wrongly grabbed the former.
        hits = [n for n in z.namelist() if Path(n).name.lower() == table]
        if not hits:
            return None
        with z.open(hits[0]) as fh:
            df = pd.read_csv(fh, dtype=str, low_memory=False, encoding="utf-8-sig")
        df.columns = [c.strip().lstrip("\ufeff") for c in df.columns]
        return df"""
if old not in src:
    raise SystemExit("OLD block not found - already patched or whitespace differs.")
io.open(p, "w", encoding="utf-8").write(src.replace(old, new, 1))
print("patched OK (backup at module_21_transit_los.py.bak)")
