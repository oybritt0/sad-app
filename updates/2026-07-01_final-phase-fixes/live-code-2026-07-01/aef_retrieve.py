"""
aef_retrieve.py  -  SAD pretrained lens, Phase 0 decision harness

Reads the AEF 64-vector corpus CSV written by sample_aef_embedding.py and runs
the honest test: cosine-similarity retrieval over the 37 AEF vectors, side by
side with the current hand-built retrieval, plus leave-one-out separation vs
the four-typology labels. Pure numpy, runs in the QGIS bundled python.

It answers two questions with numbers:
  1. Do AEF neighbors make sense (top-1 / top-3 primary-or-secondary hit vs the
     majority baseline, and vs the hand-built retrieval's 0.676 / 0.973)?
  2. Does AEF ADD signal the current lenses miss (how much do AEF top-3
     neighbor sets overlap the hand-built top-3; low overlap = orthogonal)?

Labels and the current hand-built top-3 neighbors are embedded below (parsed
from typology_suggestions_37.csv and typology_placement.csv), so this needs no
external files other than the AEF corpus CSV.

Usage:
  python-qgis-ltr.bat aef_retrieve.py --csv ...\\derived\\aef_embedding_corpus_<stamp>.csv
  ... --focus 32_District-Detroit_Detroit-MI 16_Mission-Rock_San-Francisco-CA ...
"""
from __future__ import annotations
import argparse
import csv as _csv
import sys

# hand-built baseline, computed on the current pipeline (identical probe):
HANDBUILT_TOP1 = 0.676   # 25/37 primary-or-secondary hit
HANDBUILT_TOP3 = 0.973   # 36/37

TYPOLOGY = {
    "02_Sportsmans-Park_Glendale-AZ": ("Entertainment", "Community"),
    "03_Titletown_Green-Bay-WI": ("Community", "Innovation"),
    "04_Hollywood-Park_Inglewood-CA": ("Entertainment", "Innovation"),
    "05_Downtown-East_Minneapolis-MN": ("Entertainment", "Innovation"),
    "06_Victory-Park_Dallas-TX": ("Entertainment", "Innovation"),
    "07_LA-Live_Los-Angeles-CA": ("Entertainment", "Innovation"),
    "08_Water-Street_Tampa-FL": ("Innovation", "Community"),
    "09_Maple-Leaf-Square_Toronto-ON": ("Entertainment", "Innovation"),
    "10_Pacific-Yards_Brooklyn-NY": ("Entertainment", "Community"),
    "11_Thrive-City_San-Francisco-CA": ("Entertainment", "Community"),
    "12_The-Battery_Atlanta-GA": ("Entertainment", "Innovation"),
    "13_Gallagher-Way_Chicago-IL": ("Entertainment", "Community"),
    "14_McGregor-Square_Denver-CO": ("Entertainment", "Innovation"),
    "15_Gallagher-Square_San-Diego-CA": ("Community", "Entertainment"),
    "16_Mission-Rock_San-Francisco-CA": ("Innovation", "Community"),
    "17_The-Boxyard_Seattle-WA": ("Entertainment", "Community"),
    "18_Ballpark-Village_St-Louis-MO": ("Entertainment", "Innovation"),
    "19_Arlington-Entertainment-District_Arlington-TX": ("Entertainment", "Community"),
    "20_Astor-Park_Columbus-OH": ("Community", "Innovation"),
    "21_Hub-on-Causeway_Boston-MA": ("Innovation", "Entertainment"),
    "22_True-North-Square_Winnipeg-MB": ("Innovation", "Community"),
    "23_Power-and-Light-District_Kansas-City-MO": ("Entertainment", "Innovation"),
    "24_The-Star_Frisco-TX": ("Innovation", "Entertainment"),
    "25_Viking-Lakes_Eagan-MN": ("Innovation", "Community"),
    "26_LECOM-Harborcenter_Buffalo-NY": ("Sports Park", "Entertainment"),
    "27_Treasure-Island-Center_St-Paul-MN": ("Sports Park", "Innovation"),
    "28_The-Rock-at-La-Cantera_San-Antonio-TX": ("Innovation", "Sports Park"),
    "29_Parmer-Pond-District_Austin-TX": ("Sports Park", "Community"),
    "30_WSFS-Bank-Sportsplex_Chester-PA": ("Sports Park", "Community"),
    "31_Patriot-Place_Foxborough-MA": ("Entertainment", "Innovation"),
    "32_District-Detroit_Detroit-MI": ("Entertainment", "Innovation"),
    "33_ICE-District_Edmonton-AB": ("Entertainment", "Innovation"),
    "34_Aquilini-Centre_Vancouver-BC": ("Innovation", "Entertainment"),
    "35_Deer-District_Milwaukee-WI": ("Entertainment", "Community"),
    "36_Downtown-Commons_Sacramento-CA": ("Entertainment", "Innovation"),
    "37_Galaxy-Park_Carson-CA": ("Sports Park", "Community"),
    "38_Bicentennial-Unity-Plaza_Indianapolis-IN": ("Community", "Entertainment"),
}

HANDBUILT_NN = {
    "02_Sportsmans-Park_Glendale-AZ": ["38_Bicentennial-Unity-Plaza_Indianapolis-IN", "16_Mission-Rock_San-Francisco-CA", "17_The-Boxyard_Seattle-WA"],
    "03_Titletown_Green-Bay-WI": ["16_Mission-Rock_San-Francisco-CA", "04_Hollywood-Park_Inglewood-CA", "18_Ballpark-Village_St-Louis-MO"],
    "04_Hollywood-Park_Inglewood-CA": ["28_The-Rock-at-La-Cantera_San-Antonio-TX", "03_Titletown_Green-Bay-WI", "16_Mission-Rock_San-Francisco-CA"],
    "05_Downtown-East_Minneapolis-MN": ["23_Power-and-Light-District_Kansas-City-MO", "33_ICE-District_Edmonton-AB", "08_Water-Street_Tampa-FL"],
    "06_Victory-Park_Dallas-TX": ["08_Water-Street_Tampa-FL", "27_Treasure-Island-Center_St-Paul-MN", "23_Power-and-Light-District_Kansas-City-MO"],
    "07_LA-Live_Los-Angeles-CA": ["22_True-North-Square_Winnipeg-MB", "14_McGregor-Square_Denver-CO", "21_Hub-on-Causeway_Boston-MA"],
    "08_Water-Street_Tampa-FL": ["27_Treasure-Island-Center_St-Paul-MN", "35_Deer-District_Milwaukee-WI", "16_Mission-Rock_San-Francisco-CA"],
    "09_Maple-Leaf-Square_Toronto-ON": ["21_Hub-on-Causeway_Boston-MA", "23_Power-and-Light-District_Kansas-City-MO", "34_Aquilini-Centre_Vancouver-BC"],
    "10_Pacific-Yards_Brooklyn-NY": ["13_Gallagher-Way_Chicago-IL", "21_Hub-on-Causeway_Boston-MA", "35_Deer-District_Milwaukee-WI"],
    "11_Thrive-City_San-Francisco-CA": ["36_Downtown-Commons_Sacramento-CA", "23_Power-and-Light-District_Kansas-City-MO", "33_ICE-District_Edmonton-AB"],
    "12_The-Battery_Atlanta-GA": ["20_Astor-Park_Columbus-OH", "16_Mission-Rock_San-Francisco-CA", "34_Aquilini-Centre_Vancouver-BC"],
    "13_Gallagher-Way_Chicago-IL": ["10_Pacific-Yards_Brooklyn-NY", "02_Sportsmans-Park_Glendale-AZ", "16_Mission-Rock_San-Francisco-CA"],
    "14_McGregor-Square_Denver-CO": ["22_True-North-Square_Winnipeg-MB", "07_LA-Live_Los-Angeles-CA", "16_Mission-Rock_San-Francisco-CA"],
    "15_Gallagher-Square_San-Diego-CA": ["38_Bicentennial-Unity-Plaza_Indianapolis-IN", "23_Power-and-Light-District_Kansas-City-MO", "35_Deer-District_Milwaukee-WI"],
    "16_Mission-Rock_San-Francisco-CA": ["17_The-Boxyard_Seattle-WA", "34_Aquilini-Centre_Vancouver-BC", "08_Water-Street_Tampa-FL"],
    "17_The-Boxyard_Seattle-WA": ["16_Mission-Rock_San-Francisco-CA", "22_True-North-Square_Winnipeg-MB", "23_Power-and-Light-District_Kansas-City-MO"],
    "18_Ballpark-Village_St-Louis-MO": ["38_Bicentennial-Unity-Plaza_Indianapolis-IN", "34_Aquilini-Centre_Vancouver-BC", "16_Mission-Rock_San-Francisco-CA"],
    "19_Arlington-Entertainment-District_Arlington-TX": ["28_The-Rock-at-La-Cantera_San-Antonio-TX", "35_Deer-District_Milwaukee-WI", "34_Aquilini-Centre_Vancouver-BC"],
    "20_Astor-Park_Columbus-OH": ["12_The-Battery_Atlanta-GA", "29_Parmer-Pond-District_Austin-TX", "02_Sportsmans-Park_Glendale-AZ"],
    "21_Hub-on-Causeway_Boston-MA": ["08_Water-Street_Tampa-FL", "23_Power-and-Light-District_Kansas-City-MO", "36_Downtown-Commons_Sacramento-CA"],
    "22_True-North-Square_Winnipeg-MB": ["17_The-Boxyard_Seattle-WA", "16_Mission-Rock_San-Francisco-CA", "14_McGregor-Square_Denver-CO"],
    "23_Power-and-Light-District_Kansas-City-MO": ["36_Downtown-Commons_Sacramento-CA", "33_ICE-District_Edmonton-AB", "08_Water-Street_Tampa-FL"],
    "24_The-Star_Frisco-TX": ["12_The-Battery_Atlanta-GA", "33_ICE-District_Edmonton-AB", "16_Mission-Rock_San-Francisco-CA"],
    "25_Viking-Lakes_Eagan-MN": ["28_The-Rock-at-La-Cantera_San-Antonio-TX", "24_The-Star_Frisco-TX", "29_Parmer-Pond-District_Austin-TX"],
    "26_LECOM-Harborcenter_Buffalo-NY": ["16_Mission-Rock_San-Francisco-CA", "35_Deer-District_Milwaukee-WI", "27_Treasure-Island-Center_St-Paul-MN"],
    "27_Treasure-Island-Center_St-Paul-MN": ["08_Water-Street_Tampa-FL", "35_Deer-District_Milwaukee-WI", "33_ICE-District_Edmonton-AB"],
    "28_The-Rock-at-La-Cantera_San-Antonio-TX": ["16_Mission-Rock_San-Francisco-CA", "04_Hollywood-Park_Inglewood-CA", "35_Deer-District_Milwaukee-WI"],
    "29_Parmer-Pond-District_Austin-TX": ["20_Astor-Park_Columbus-OH", "17_The-Boxyard_Seattle-WA", "16_Mission-Rock_San-Francisco-CA"],
    "30_WSFS-Bank-Sportsplex_Chester-PA": ["04_Hollywood-Park_Inglewood-CA", "28_The-Rock-at-La-Cantera_San-Antonio-TX", "20_Astor-Park_Columbus-OH"],
    "31_Patriot-Place_Foxborough-MA": ["17_The-Boxyard_Seattle-WA", "02_Sportsmans-Park_Glendale-AZ", "16_Mission-Rock_San-Francisco-CA"],
    "32_District-Detroit_Detroit-MI": ["08_Water-Street_Tampa-FL", "38_Bicentennial-Unity-Plaza_Indianapolis-IN", "34_Aquilini-Centre_Vancouver-BC"],
    "33_ICE-District_Edmonton-AB": ["36_Downtown-Commons_Sacramento-CA", "23_Power-and-Light-District_Kansas-City-MO", "08_Water-Street_Tampa-FL"],
    "34_Aquilini-Centre_Vancouver-BC": ["16_Mission-Rock_San-Francisco-CA", "38_Bicentennial-Unity-Plaza_Indianapolis-IN", "23_Power-and-Light-District_Kansas-City-MO"],
    "35_Deer-District_Milwaukee-WI": ["08_Water-Street_Tampa-FL", "38_Bicentennial-Unity-Plaza_Indianapolis-IN", "27_Treasure-Island-Center_St-Paul-MN"],
    "36_Downtown-Commons_Sacramento-CA": ["33_ICE-District_Edmonton-AB", "23_Power-and-Light-District_Kansas-City-MO", "11_Thrive-City_San-Francisco-CA"],
    "37_Galaxy-Park_Carson-CA": ["28_The-Rock-at-La-Cantera_San-Antonio-TX", "29_Parmer-Pond-District_Austin-TX", "16_Mission-Rock_San-Francisco-CA"],
    "38_Bicentennial-Unity-Plaza_Indianapolis-IN": ["02_Sportsmans-Park_Glendale-AZ", "08_Water-Street_Tampa-FL", "35_Deer-District_Milwaukee-WI"],
}

DEFAULT_FOCUS = [
    "32_District-Detroit_Detroit-MI",
    "16_Mission-Rock_San-Francisco-CA",
    "37_Galaxy-Park_Carson-CA",
    "07_LA-Live_Los-Angeles-CA",
    "26_LECOM-Harborcenter_Buffalo-NY",
]


def _short(sid: str) -> str:
    parts = sid.split("_")
    return parts[1] if len(parts) > 1 else sid


def _load_csv(path, np):
    ids, vecs, thin = [], [], {}
    with open(path, "r", encoding="utf-8", newline="") as f:
        rd = _csv.DictReader(f)
        acols = ["a%02d" % i for i in range(64)]
        miss = [c for c in acols if c not in (rd.fieldnames or [])]
        if miss:
            raise SystemExit("[FAIL] csv missing embedding columns, e.g. %s" % miss[:3])
        for row in rd:
            ids.append(row["sad_id"])
            vecs.append([float(row[c]) for c in acols])
            if "thin_sample" in row and str(row.get("thin_sample", "0")) in ("1", "True", "true"):
                thin[row["sad_id"]] = True
    X = np.asarray(vecs, dtype="float64")
    # re-normalize defensively so cosine == dot
    n = np.linalg.norm(X, axis=1, keepdims=True)
    X = X / (n + 1e-12)
    return ids, X, thin


def _neighbors(np, X, k):
    """Leave-one-out cosine neighbors; returns index lists ranked desc."""
    S = X @ X.T
    np.fill_diagonal(S, -2.0)
    order = np.argsort(-S, axis=1)
    return order[:, :k], S


def _hit(qid, neigh_ids, k):
    labs = set(TYPOLOGY.get(qid, ("", "")))
    for nid in neigh_ids[:k]:
        if TYPOLOGY.get(nid, ("", ""))[0] in labs:
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="aef_embedding_corpus_<stamp>.csv")
    ap.add_argument("--focus", nargs="*", default=None)
    ap.add_argument("--k", type=int, default=3)
    args = ap.parse_args()
    try:
        import numpy as np
    except Exception as e:
        print("[FAIL] need numpy from the QGIS python:", e); return 2

    ids, X, thin = _load_csv(args.csv, np)
    idx = {s: i for i, s in enumerate(ids)}
    n = len(ids)
    print("[aef] loaded %d district vectors, dim=%d" % (n, X.shape[1]))
    labelled = [s for s in ids if s in TYPOLOGY]
    if thin:
        print("[aef] thin-sample (dense-core) districts flagged: %d -> %s"
              % (len(thin), ", ".join(_short(s) for s in thin)))

    order3, S = _neighbors(np, X, max(args.k, 3))
    aef_nn = {ids[i]: [ids[j] for j in order3[i]] for i in range(n)}

    # ---- focus districts: AEF neighbors next to hand-built neighbors ----
    focus = args.focus or [s for s in DEFAULT_FOCUS if s in idx]
    print("\n=== retrieval: AEF vs hand-built (top-3) ===")
    for q in focus:
        if q not in idx:
            print("  [skip] %s not in AEF csv" % q); continue
        p, s = TYPOLOGY.get(q, ("?", "?"))
        print("\n%s  [%s / %s]" % (q, p, s))
        print("  AEF        : " + "; ".join(
            "%s[%s]" % (_short(nid), TYPOLOGY.get(nid, ("?",))[0]) for nid in aef_nn[q][:3]))
        hb = HANDBUILT_NN.get(q, [])
        print("  hand-built : " + "; ".join(
            "%s[%s]" % (_short(nid), TYPOLOGY.get(nid, ("?",))[0]) for nid in hb[:3]))
        ov = len(set(aef_nn[q][:3]) & set(hb[:3]))
        print("  shared top-3: %d/3" % ov)

    # ---- leave-one-out separation vs typology ----
    from collections import Counter
    prim = {s: TYPOLOGY[s][0] for s in labelled}
    cnt = Counter(prim.values())
    maj = max(cnt.values()) / len(labelled)
    for k in (1, 3):
        h = sum(_hit(q, aef_nn[q], k) for q in labelled)
        print("\n[aef] top-%d primary-or-secondary hit: %d/%d = %.3f  "
              "(hand-built %.3f, majority %.3f)"
              % (k, h, len(labelled), h / len(labelled),
                 HANDBUILT_TOP1 if k == 1 else HANDBUILT_TOP3, maj))

    # ---- does AEF add signal the current lenses miss? ----
    jac = []
    for q in labelled:
        a = set(aef_nn[q][:3]); b = set(HANDBUILT_NN.get(q, [])[:3])
        if a or b:
            jac.append(len(a & b) / len(a | b))
    mean_jac = sum(jac) / len(jac) if jac else 0.0
    print("\n[orthogonality] mean Jaccard(AEF top-3, hand-built top-3) = %.3f" % mean_jac)
    print("  low overlap => AEF sees something the current lenses do not (adds signal)")
    print("  high overlap => AEF largely restates the current retrieval (redundant)")

    # within-vs-cross primary cosine separation (like the nature-lens eta test)
    same, cross = [], []
    for i in range(n):
        if ids[i] not in prim:
            continue
        for j in range(i + 1, n):
            if ids[j] not in prim:
                continue
            (same if prim[ids[i]] == prim[ids[j]] else cross).append(float(S[i, j]) if S[i, j] > -1.5 else float(X[i] @ X[j]))
    if same and cross:
        ms = sum(same) / len(same); mc = sum(cross) / len(cross)
        print("[separation] mean cosine within-primary %.3f vs cross-primary %.3f (gap %.3f)"
              % (ms, mc, ms - mc))

    # ---- honest decision scaffold ----
    top3 = sum(_hit(q, aef_nn[q], 3) for q in labelled) / len(labelled)
    print("\n=== read ===")
    verdict_beats = top3 >= maj + 0.10
    verdict_orth = mean_jac <= 0.20
    print("  AEF top-3 %.3f vs majority %.3f  -> %s"
          % (top3, maj, "clears baseline" if verdict_beats else "at/below baseline"))
    print("  overlap with hand-built %.3f     -> %s"
          % (mean_jac, "orthogonal, adds signal" if verdict_orth else "overlaps current lenses"))
    if verdict_beats and verdict_orth:
        print("  => PROMOTE AEF to a standalone pretrained surface/form lens.")
    elif verdict_beats and not verdict_orth:
        print("  => AEF works but largely restates current lenses; keep only if the")
        print("     per-lens 'why' (surface/form) is worth showing. Judgement call.")
    else:
        print("  => negative result: AEF form is already captured by the current lenses,")
        print("     or does not separate typology at N=37. Document and park.")
    print("\n(Thresholds are a scaffold, not a verdict. Read the focus neighbors: do")
    print(" AEF's matches make architectural sense? That is the real test.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
