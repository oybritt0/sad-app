"""
typology_override.py  —  durable /set_typology endpoint for the Compare dashboard.

The dashboard lets a user confirm or change a district's typology in the 3D
panel. The client keeps its own copy in localStorage, but that is per-browser and
per-machine. This module gives that action a durable home on the server: it
writes a small JSON of {sad_id -> primary_typology} that survives restarts and is
shared by anyone hitting the same server (e.g. the Render deploy).

It mirrors the register(app, data_dir) pattern used by program_match.py and
structure_match.py, so make_app() wires it the same way:

    import typology_override
    typology_override.register(app, data_dir)

Routes added:
    POST /set_typology   body: {"sad_id": "...", "primary_typology": "Entertainment" | null}
                         writes/clears the override; returns the full current map.
    GET  /typology_overrides
                         returns the current {sad_id -> typology} map, so the
                         dashboard can load durable confirmations on boot.

Storage: data_dir/_compare_ui/typology_overrides.json  (created on first write).
A null/empty primary_typology removes the override (back to "unconfirmed").
"""
from __future__ import annotations
import json
import threading
from pathlib import Path

# The four canonical families (plus a permissive pass-through). We validate
# loosely: anything non-empty is accepted so the UI can introduce a label
# without a server change, but we trim and cap length to keep the file clean.
_MAX_LEN = 64
_LOCK = threading.Lock()


def _store_path(data_dir: Path) -> Path:
    return Path(data_dir) / "_compare_ui" / "typology_overrides.json"


def _load(data_dir: Path) -> dict:
    p = _store_path(data_dir)
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(data_dir: Path, d: dict) -> None:
    p = _store_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(p)  # atomic on the same volume


def _clean_id(sad_id) -> str:
    s = ("" if sad_id is None else str(sad_id)).strip()
    if not s or "/" in s or "\\" in s or ".." in s:
        return ""
    return s[:128]


def register(app, data_dir: Path):
    from flask import request, jsonify
    data_dir = Path(data_dir)
    n = len(_load(data_dir))
    print(f"  typology overrides: {n} durable confirmation(s) on disk")

    @app.route("/set_typology", methods=["POST", "OPTIONS"])
    def set_typology():
        if request.method == "OPTIONS":
            return ("", 204)
        try:
            body = request.get_json(force=True) or {}
            sad_id = _clean_id(body.get("sad_id"))
            if not sad_id:
                return jsonify({"ok": False, "error": "Missing or invalid sad_id."}), 400
            fam = body.get("primary_typology")
            fam = ("" if fam is None else str(fam)).strip()[:_MAX_LEN]
            with _LOCK:
                d = _load(data_dir)
                if fam:
                    d[sad_id] = fam
                else:
                    d.pop(sad_id, None)  # clearing returns it to unconfirmed
                _save(data_dir, d)
            return jsonify({"ok": True, "sad_id": sad_id,
                            "primary_typology": fam or None,
                            "count": len(d), "overrides": d})
        except Exception as e:
            import traceback
            traceback.print_exc()
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/typology_overrides", methods=["GET", "OPTIONS"])
    def typology_overrides():
        if request.method == "OPTIONS":
            return ("", 204)
        d = _load(data_dir)
        return jsonify({"ok": True, "count": len(d), "overrides": d})

    return _load(data_dir)
