"""
cvae_form.py  (Module 26 - FORM-conditioned graph VAE)

The honest conditional generator. Conditions each node on its FORM FAMILY (the
validated channel: district separation 0.069, vs dead typology 0.036 / program
0.006) plus the district's demographics. Typology is dropped entirely.

  condition (per node) = [ form-family one-hot (k) | demo brief (4) | demo_present (1) ]

At generation you give a TARGET form-family mix (directly, or copied from a
retrieved comparable) and demographics; the model samples a family per node from
that mix and decodes a building of that form. Because form is a channel the data
actually carries, the output moves with the brief -- unlike the typology version.

Proof of life: after decoding, generated shapes are re-assigned to families via
the M24 centroids; the realized mix should track the target.

INPUT   data/_graphs/*_norm.npz, _form/{form_labels.parquet, form_clusters.json},
        _corpus/node_scaler.json, and per-SAD district_profile.json (demographics)
OUTPUT  data/_graphs/_fmodel/{cvae_form.pt, form_condition_scaler.json}
        (generate) data/_graphs/_gen/fgen_<tag>.csv (+_edges)

USAGE
  python cvae_form.py train    --graphs-dir ..\\data\\_graphs --data-dir ..\\data --epochs 300 --beta 0.05
  python cvae_form.py generate --graphs-dir ..\\data\\_graphs --like 32_District-Detroit --n-nodes 120
  python cvae_form.py generate --graphs-dir ..\\data\\_graphs --form-mix "0=0.5,6=0.2,5=0.3" --income 90000
"""
from __future__ import annotations
import argparse, glob, json
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from torch_geometric.data import Data
from torch_geometric.loader import DataLoader

from vgae_train import sad_from_name
from cvae_train import (CondGraphVAE, feature_weights, train, find_profiles, DEMO_BRIEF)
from generate import inverse_transform, edges_from_latent, load_scaler
from graph_builder import ROSSETTI_BUCKETS


# ─── conditions: per-node form family + district demographics ────────────────

def build_demo(sad_ids, data_dir):
    profs = find_profiles(data_dir)
    raw, present = {}, {}
    for sad in sad_ids:
        demo, has = [np.nan] * len(DEMO_BRIEF), 0.0
        if sad in profs:
            d = json.loads(profs[sad].read_text()).get('demographics', {}) or {}
            demo = [d.get(k) if d.get(k) is not None else np.nan for k in DEMO_BRIEF]
            has = 1.0 if (d.get('total_population') or 0) > 0 else 0.0
        raw[sad] = demo; present[sad] = has
    D = np.array([raw[s] for s in sad_ids], 'float64')
    mean = np.nanmean(D, axis=0); std = np.nanstd(D, axis=0)
    std[~np.isfinite(std) | (std == 0)] = 1.0; mean = np.nan_to_num(mean)
    demo_z = {s: np.where(np.isfinite(raw[s]), (np.array(raw[s]) - mean) / std, 0.0).astype('float32')
              for s in sad_ids}
    return demo_z, present, mean.tolist(), std.tolist()


def load_corpus_form(graphs_dir, data_dir, k):
    fl = pd.read_parquet(graphs_dir / '_form' / 'form_labels.parquet')
    fam_by_graph = {g: sub.sort_values('node')['form_type'].to_numpy()
                    for g, sub in fl.groupby('graph_name')}
    files = sorted(glob.glob(str(graphs_dir / '*_norm.npz')))
    sad_ids = sorted({sad_from_name(Path(f).stem.replace('_norm', '')) for f in files})
    demo_z, present, dmean, dstd = build_demo(sad_ids, data_dir)

    graphs = []
    for f in files:
        d = np.load(f, allow_pickle=True)
        x = torch.tensor(d['X'], dtype=torch.float32).clamp_(-10, 10)
        ei = torch.tensor(d['edge_index'], dtype=torch.long)
        gn = Path(f).stem.replace('_norm', ''); sad = sad_from_name(gn)
        fam = fam_by_graph.get(gn, np.zeros(x.size(0), int))[:x.size(0)]
        oh = np.eye(k, dtype='float32')[fam]                       # per-node family one-hot
        dz = np.broadcast_to(demo_z[sad], (x.size(0), len(DEMO_BRIEF)))
        pr = np.full((x.size(0), 1), present[sad], 'float32')
        cond = torch.tensor(np.concatenate([oh, dz, pr], axis=1), dtype=torch.float32)
        g = Data(x=x, edge_index=ei, cond=cond); g.name = gn
        graphs.append(g)
    scaler = dict(k=int(k), demo_brief=DEMO_BRIEF, demo_mean=dmean, demo_std=dstd,
                  cond_dim=k + len(DEMO_BRIEF) + 1)
    return graphs, scaler


# ─── form family assignment from decoded shapes (proof of life) ──────────────

def nearest_family(Xhat_std, feat_names, clusters):
    """Assign decoded nodes to families via M24 centroids (euclidean)."""
    shp = clusters['feature_names']; centers = np.array(clusters['centers_std'])
    idx = [feat_names.index(n) for n in shp]
    S = Xhat_std[:, idx]
    d = ((S[:, None, :] - centers[None, :, :]) ** 2).sum(-1)
    return d.argmin(1)


def parse_mix(spec, k):
    mix = np.zeros(k, 'float32')
    for part in spec.split(','):
        i, v = part.split('='); mix[int(i)] = float(v)
    return mix / mix.sum()


def main():
    ap = argparse.ArgumentParser(description='Form-conditioned graph VAE (M26)')
    sub = ap.add_subparsers(dest='op', required=True)

    pt = sub.add_parser('train')
    pt.add_argument('--graphs-dir', type=Path, required=True)
    pt.add_argument('--data-dir', type=Path, required=True)
    pt.add_argument('--epochs', type=int, default=300)
    pt.add_argument('--latent-dim', type=int, default=16)
    pt.add_argument('--hidden', type=int, default=64)
    pt.add_argument('--lr', type=float, default=3e-3)
    pt.add_argument('--batch-size', type=int, default=16)
    pt.add_argument('--beta', type=float, default=0.05)
    pt.add_argument('--seed', type=int, default=0)

    pg = sub.add_parser('generate')
    pg.add_argument('--graphs-dir', type=Path, required=True)
    pg.add_argument('--like', type=str, default=None, help="copy a district's form mix")
    pg.add_argument('--form-mix', type=str, default=None, help='"0=0.5,6=0.2,5=0.3"')
    pg.add_argument('--n-nodes', type=int, default=120)
    pg.add_argument('--income', type=float, default=None)
    pg.add_argument('--median-age', type=float, default=None)
    pg.add_argument('--population', type=float, default=None)
    pg.add_argument('--pct-bachelors', type=float, default=None)
    pg.add_argument('--seed', type=int, default=0)
    a = ap.parse_args()

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    clusters = json.loads((a.graphs_dir / '_form' / 'form_clusters.json').read_text())
    k = clusters['k']

    if a.op == 'train':
        torch.manual_seed(a.seed); np.random.seed(a.seed)
        feat_names = list(np.load(sorted(glob.glob(str(a.graphs_dir / '*_norm.npz')))[0],
                                  allow_pickle=True)['feature_names'])
        graphs, scaler = load_corpus_form(a.graphs_dir, a.data_dir, k)
        out = a.graphs_dir / '_fmodel'; out.mkdir(parents=True, exist_ok=True)
        print(f'device: {device} | form families k={k} | cond_dim={scaler["cond_dim"]} '
              f'| {len(graphs)} graphs')
        model = CondGraphVAE(len(feat_names), scaler['cond_dim'], a.hidden, a.latent_dim).to(device)
        loader = DataLoader(graphs, batch_size=a.batch_size, shuffle=True)
        log = []
        train(model, loader, device, a.epochs, a.lr, feature_weights(feat_names), a.beta, log)
        torch.save(dict(state_dict=model.state_dict(), cfg=model.cfg,
                        feature_names=[str(x) for x in feat_names]), out / 'cvae_form.pt')
        (out / 'form_condition_scaler.json').write_text(json.dumps(scaler, indent=2))
        (out / 'training_log.json').write_text(json.dumps(log, indent=2))
        print(f'\n[OK] form-conditioned model -> {out/"cvae_form.pt"}')

    else:  # generate
        out = a.graphs_dir / '_fmodel'
        ckpt = torch.load(out / 'cvae_form.pt', map_location=device, weights_only=False)
        scaler = json.loads((out / 'form_condition_scaler.json').read_text())
        node_scaler = load_scaler(a.graphs_dir)
        model = CondGraphVAE(**ckpt['cfg']).to(device)
        model.load_state_dict(ckpt['state_dict']); model.eval()
        feat_names = ckpt['feature_names']

        # target form mix
        if a.form_mix:
            mix = parse_mix(a.form_mix, k); tag = 'mix'
        elif a.like:
            prof = pd.read_csv(a.graphs_dir / '_form' / 'form_profiles.csv', index_col=0)
            row = prof[[a.like.lower() in s.lower() for s in prof.index]]
            mix = row.iloc[0].to_numpy('float32'); mix /= mix.sum(); tag = a.like.split('_')[0]
        else:
            fl = pd.read_parquet(a.graphs_dir / '_form' / 'form_labels.parquet')
            mix = (np.bincount(fl['form_type'], minlength=k) / len(fl)).astype('float32'); tag = 'corpus'

        n = a.n_nodes
        rng = np.random.default_rng(a.seed)
        fam = rng.choice(k, size=n, p=mix)
        oh = np.eye(k, dtype='float32')[fam]
        mean = np.array(scaler['demo_mean']); std = np.array(scaler['demo_std'])
        dz = np.zeros(len(DEMO_BRIEF), 'float32'); present = 0.0
        ov = dict(total_population=a.population, median_household_income=a.income,
                  median_age=a.median_age, pct_bachelors_or_higher=a.pct_bachelors)
        for i, kk in enumerate(DEMO_BRIEF):
            if ov[kk] is not None:
                dz[i] = (ov[kk] - mean[i]) / std[i]; present = 1.0
        cond = np.concatenate([oh, np.broadcast_to(dz, (n, len(DEMO_BRIEF))),
                               np.full((n, 1), present, 'float32')], axis=1)
        cond = torch.tensor(cond, dtype=torch.float32, device=device)

        torch.manual_seed(a.seed)
        with torch.no_grad():
            z = torch.randn(n, ckpt['cfg']['lat'], device=device)
            xhat = model.dec(z, cond).cpu().numpy()

        realized = nearest_family(xhat, feat_names, clusters)
        rmix = np.bincount(realized, minlength=k) / n
        df = inverse_transform(xhat, node_scaler); df.insert(0, 'node', range(n))
        gen = a.graphs_dir / '_gen'; gen.mkdir(parents=True, exist_ok=True)
        stem = f'fgen_{tag}_{n}'
        df.to_csv(gen / f'{stem}.csv', index=False)
        edges_from_latent(z).to_csv(gen / f'{stem}_edges.csv', index=False)

        print(f'generated {n}-node graph, form mix "{tag}"')
        print('  target  mix: ' + ' '.join(f'{i}:{v:.2f}' for i, v in enumerate(mix) if v > 0.01))
        print('  realized mix: ' + ' '.join(f'{i}:{v:.2f}' for i, v in enumerate(rmix) if v > 0.01))
        print(f'  mean decoded area: {df["shape__area_m2"].mean():.0f} m2  -> {gen/(stem+".csv")}')


if __name__ == '__main__':
    main()
