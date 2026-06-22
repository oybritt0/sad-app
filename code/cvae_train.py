"""
cvae_train.py  (Module 21 - conditional graph VAE: generate for a brief)

Upgrades the VGAE (M18) into a CONDITIONAL generator. Each graph carries a
condition vector = typology one-hot + the district's demographic brief (read
from district_profile.json). The decoder sees [z ; condition], so at generation
you FIX the condition ("community typology, this income/age/density") and sample
z from the prior -> a de-novo attributed graph that answers the brief, rather
than a variant of an existing place.

Also fixes the weak-shape channel: per-feature loss weights down-weight the
noisy Hu moments so program / scale / adjacency get the latent's capacity.

Condition = [ typology one-hot | demo brief (z-scored) | demo_present ]
  demo brief = total_population, median_household_income, median_age,
               pct_bachelors_or_higher   (district-level, from M5)

INPUT   data/_graphs/*_norm.npz                (M17 corpus)
        data/<sad>/derived/district_profile.json (M5, for the condition)
OUTPUT  data/_graphs/_cmodel/
          cvae.pt              weights + cfg + feature_names
          condition_scaler.json  typology vocab, demo means/stds, N-by-typology
          training_log.json
        (generate) data/_graphs/_gen/cgen_<typology>_<n>.csv  (+_edges)

USAGE
  python cvae_train.py train    --graphs-dir ..\\data\\_graphs --data-dir ..\\data --epochs 200
  python cvae_train.py generate --graphs-dir ..\\data\\_graphs --typology community --n-nodes 80
  python cvae_train.py generate --graphs-dir ..\\data\\_graphs --typology entertainment \\
                                --income 85000 --median-age 31 --population 40000 --pct-bachelors 0.5
"""
from __future__ import annotations
import argparse, glob, json
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch_geometric.data import Data
from torch_geometric.loader import DataLoader
from torch_geometric.nn import GCNConv
from torch_geometric.utils import negative_sampling

from vgae_train import sad_from_name
from graph_builder import HU_FEATURES, ROSSETTI_BUCKETS
from generate import inverse_transform, edges_from_latent, load_scaler


DEMO_BRIEF = ['total_population', 'median_household_income',
              'median_age', 'pct_bachelors_or_higher']


# ─── conditions from district_profile.json ───────────────────────────────────

def find_profiles(data_dir: Path) -> dict[str, Path]:
    out = {}
    for p in data_dir.rglob('district_profile.json'):
        if '_graphs' in p.parts:
            continue
        prof = json.loads(p.read_text())
        out[prof.get('sad_id', p.parent.parent.name)] = p
    return out


def read_typologies(data_dir: Path) -> dict[str, str]:
    """Real typology labels: canonical _shared/typologies.json first, then
    per-SAD derived/typology.json. Returns {sad_id: primary_typology.lower()}."""
    out = {}
    shared = data_dir / '_shared' / 'typologies.json'
    if shared.exists():
        for sad, rec in json.loads(shared.read_text()).get('districts', {}).items():
            out[sad] = str(rec.get('primary_typology') or 'unspecified').lower()
        if out:
            return out
    for p in data_dir.rglob('typology.json'):
        if '_graphs' in p.parts:
            continue
        rec = json.loads(p.read_text())
        out[rec.get('sad_id', p.parent.parent.name)] = \
            str(rec.get('primary_typology') or 'unspecified').lower()
    return out


def build_conditions(graph_names: list[str], data_dir: Path):
    """Return (cond_by_sad: dict, scaler: dict). Condition layout:
    [ typology one-hot (V) | demo brief z-scored (4) | demo_present (1) ]."""
    profiles = find_profiles(data_dir)
    typ_map = read_typologies(data_dir)
    sad_ids = sorted({sad_from_name(n) for n in graph_names})

    raw_typ, raw_demo, present = {}, {}, {}
    for sad in sad_ids:
        typ = typ_map.get(sad, 'unspecified')
        demo, has = [np.nan] * len(DEMO_BRIEF), 0.0
        if sad in profiles:
            prof = json.loads(profiles[sad].read_text())
            d = prof.get('demographics', {}) or {}
            demo = [d.get(k) if d.get(k) is not None else np.nan for k in DEMO_BRIEF]
            has = 1.0 if (d.get('total_population') or 0) > 0 else 0.0
        raw_typ[sad] = typ; raw_demo[sad] = demo; present[sad] = has

    vocab = sorted({t for t in raw_typ.values()}) + ['unknown']
    D = np.array([raw_demo[s] for s in sad_ids], dtype='float64')
    mean = np.nanmean(np.where(D == None, np.nan, D), axis=0)
    std = np.nanstd(D, axis=0); std[~np.isfinite(std) | (std == 0)] = 1.0
    mean = np.nan_to_num(mean, nan=0.0)

    cond_by_sad = {}
    for sad in sad_ids:
        oh = np.zeros(len(vocab), 'float32')
        oh[vocab.index(raw_typ[sad] if raw_typ[sad] in vocab else 'unknown')] = 1.0
        dv = np.array(raw_demo[sad], 'float64')
        dz = np.where(np.isfinite(dv), (dv - mean) / std, 0.0).astype('float32')
        cond_by_sad[sad] = np.concatenate([oh, dz, [present[sad]]]).astype('float32')

    scaler = dict(typology_vocab=vocab, demo_brief=DEMO_BRIEF,
                  demo_mean=mean.tolist(), demo_std=std.tolist(),
                  cond_dim=len(vocab) + len(DEMO_BRIEF) + 1)
    return cond_by_sad, scaler


def load_corpus_cond(graphs_dir: Path, cond_by_sad: dict):
    files = sorted(glob.glob(str(graphs_dir / '*_norm.npz')))
    graphs, ncount = [], {}
    for f in files:
        d = np.load(f, allow_pickle=True)
        x = torch.tensor(d['X'], dtype=torch.float32).clamp_(-10, 10)
        ei = torch.tensor(d['edge_index'], dtype=torch.long)
        name = Path(f).stem.replace('_norm', '')
        sad = sad_from_name(name)
        c = torch.tensor(cond_by_sad[sad], dtype=torch.float32)
        g = Data(x=x, edge_index=ei, cond=c.repeat(x.size(0), 1))
        g.name = name
        graphs.append(g)
        ncount.setdefault(sad, []).append(x.size(0))
    return graphs, files


# ─── model ───────────────────────────────────────────────────────────────────

class CEncoder(nn.Module):
    def __init__(self, in_dim, cond_dim, hid, lat):
        super().__init__()
        self.c1 = GCNConv(in_dim + cond_dim, hid)
        self.mu = GCNConv(hid, lat); self.ls = GCNConv(hid, lat)

    def forward(self, x, c, ei):
        h = F.relu(self.c1(torch.cat([x, c], 1), ei))
        return self.mu(h, ei), self.ls(h, ei)


class CDecoder(nn.Module):
    def __init__(self, lat, cond_dim, hid, out_dim):
        super().__init__()
        self.net = nn.Sequential(nn.Linear(lat + cond_dim, hid), nn.ReLU(),
                                 nn.Linear(hid, out_dim))

    def forward(self, z, c):
        return self.net(torch.cat([z, c], 1))


class CondGraphVAE(nn.Module):
    def __init__(self, in_dim, cond_dim, hid=64, lat=16):
        super().__init__()
        self.enc = CEncoder(in_dim, cond_dim, hid, lat)
        self.dec = CDecoder(lat, cond_dim, hid, in_dim)
        self.cfg = dict(in_dim=in_dim, cond_dim=cond_dim, hid=hid, lat=lat)

    def encode(self, x, c, ei, sample=True):
        mu, ls = self.enc(x, c, ei); ls = ls.clamp(max=10)
        z = mu + torch.randn_like(mu) * torch.exp(ls) if sample else mu
        return z, mu, ls

    def recon_struct(self, z, pos_ei):
        f = lambda ei: (z[ei[0]] * z[ei[1]]).sum(-1)
        pos = f(pos_ei)
        neg = f(negative_sampling(pos_ei, z.size(0), pos_ei.size(1)))
        y = torch.cat([torch.ones_like(pos), torch.zeros_like(neg)])
        return F.binary_cross_entropy_with_logits(torch.cat([pos, neg]), y)


def feature_weights(feat_names: list[str]) -> torch.Tensor:
    """Down-weight noisy Hu moments so program/scale dominate."""
    w = torch.ones(len(feat_names))
    for i, n in enumerate(feat_names):
        if n.startswith('shape__') and n.split('__', 1)[1] in HU_FEATURES:
            w[i] = 0.2
    return w


def kl(mu, ls):
    return -0.5 * torch.mean(1 + 2 * ls - mu.pow(2) - torch.exp(2 * ls))


# ─── train ───────────────────────────────────────────────────────────────────

def train(model, loader, device, epochs, lr, w, beta, log):
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    w = w.to(device); model.train()
    for ep in range(1, epochs + 1):
        tot = fl = sl = kll = 0.0
        for b in loader:
            b = b.to(device); opt.zero_grad()
            z, mu, ls = model.encode(b.x, b.cond, b.edge_index, sample=True)
            lf = (w * (model.dec(z, b.cond) - b.x) ** 2).mean()
            lst = model.recon_struct(z, b.edge_index)
            lk = kl(mu, ls)
            loss = lf + lst + beta * lk
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            opt.step()
            tot += loss.item(); fl += lf.item(); sl += lst.item(); kll += lk.item()
        nb = len(loader)
        if ep % 10 == 0 or ep == 1:
            print(f'  epoch {ep:4d}  loss {tot/nb:.4f}  feat {fl/nb:.4f}  '
                  f'struct {sl/nb:.4f}  kl {kll/nb:.4f}')
        log.append(dict(epoch=ep, loss=tot/nb, feat=fl/nb, struct=sl/nb, kl=kll/nb))


# ─── generate ────────────────────────────────────────────────────────────────

def make_condition(scaler, typology, overrides: dict):
    vocab = scaler['typology_vocab']
    oh = np.zeros(len(vocab), 'float32')
    t = typology.lower()
    oh[vocab.index(t if t in vocab else 'unknown')] = 1.0
    mean = np.array(scaler['demo_mean']); std = np.array(scaler['demo_std'])
    dz, present = np.zeros(len(DEMO_BRIEF), 'float32'), 0.0
    for i, k in enumerate(DEMO_BRIEF):
        if overrides.get(k) is not None:
            dz[i] = (overrides[k] - mean[i]) / std[i]; present = 1.0
    return np.concatenate([oh, dz, [present]]).astype('float32')


def main():
    ap = argparse.ArgumentParser(description='Conditional graph VAE (M21)')
    sub = ap.add_subparsers(dest='op', required=True)

    pt = sub.add_parser('train')
    pt.add_argument('--graphs-dir', type=Path, required=True)
    pt.add_argument('--data-dir', type=Path, required=True)
    pt.add_argument('--epochs', type=int, default=200)
    pt.add_argument('--latent-dim', type=int, default=16)
    pt.add_argument('--hidden', type=int, default=64)
    pt.add_argument('--lr', type=float, default=3e-3)
    pt.add_argument('--batch-size', type=int, default=16)
    pt.add_argument('--beta', type=float, default=0.1)
    pt.add_argument('--seed', type=int, default=0)

    pg = sub.add_parser('generate')
    pg.add_argument('--graphs-dir', type=Path, required=True)
    pg.add_argument('--typology', type=str, required=True)
    pg.add_argument('--n-nodes', type=int, default=None)
    pg.add_argument('--income', type=float, default=None)
    pg.add_argument('--median-age', type=float, default=None)
    pg.add_argument('--population', type=float, default=None)
    pg.add_argument('--pct-bachelors', type=float, default=None)
    pg.add_argument('--seed', type=int, default=0)
    a = ap.parse_args()

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

    if a.op == 'train':
        torch.manual_seed(a.seed); np.random.seed(a.seed)
        names = [Path(f).stem.replace('_norm', '')
                 for f in glob.glob(str(a.graphs_dir / '*_norm.npz'))]
        cond_by_sad, cscaler = build_conditions(names, a.data_dir)
        feat_names = list(np.load(sorted(glob.glob(str(a.graphs_dir / '*_norm.npz')))[0],
                                  allow_pickle=True)['feature_names'])
        graphs, _ = load_corpus_cond(a.graphs_dir, cond_by_sad)
        # node-count stats per typology for sane generation defaults
        ntyp = {}
        for g in graphs:
            t = cscaler['typology_vocab'][int(np.argmax(
                cond_by_sad[sad_from_name(g.name)][:len(cscaler['typology_vocab'])]))]
            ntyp.setdefault(t, []).append(g.num_nodes)
        cscaler['n_by_typology'] = {t: int(np.median(v)) for t, v in ntyp.items()}
        cscaler['n_median'] = int(np.median([g.num_nodes for g in graphs]))

        out = a.graphs_dir / '_cmodel'; out.mkdir(parents=True, exist_ok=True)
        print(f'device: {device} | typologies: {cscaler["typology_vocab"]} | '
              f'cond_dim={cscaler["cond_dim"]}')
        model = CondGraphVAE(len(feat_names), cscaler['cond_dim'],
                             a.hidden, a.latent_dim).to(device)
        loader = DataLoader(graphs, batch_size=a.batch_size, shuffle=True)
        log = []
        train(model, loader, device, a.epochs, a.lr,
              feature_weights(feat_names), a.beta, log)
        torch.save(dict(state_dict=model.state_dict(), cfg=model.cfg,
                        feature_names=[str(x) for x in feat_names]), out / 'cvae.pt')
        (out / 'condition_scaler.json').write_text(json.dumps(cscaler, indent=2))
        (out / 'training_log.json').write_text(json.dumps(log, indent=2))
        print(f'\n[OK] conditional model -> {out/"cvae.pt"}')

    else:  # generate
        out = a.graphs_dir / '_cmodel'
        ckpt = torch.load(out / 'cvae.pt', map_location=device, weights_only=False)
        cscaler = json.loads((out / 'condition_scaler.json').read_text())
        node_scaler = load_scaler(a.graphs_dir)
        model = CondGraphVAE(**ckpt['cfg']).to(device)
        model.load_state_dict(ckpt['state_dict']); model.eval()

        n = (a.n_nodes or cscaler.get('n_by_typology', {}).get(
            a.typology.lower(), cscaler.get('n_median', 60)))
        overrides = dict(total_population=a.population,
                         median_household_income=a.income,
                         median_age=a.median_age,
                         pct_bachelors_or_higher=a.pct_bachelors)
        c = torch.tensor(make_condition(cscaler, a.typology, overrides),
                         device=device).repeat(n, 1)
        torch.manual_seed(a.seed)
        with torch.no_grad():
            z = torch.randn(n, ckpt['cfg']['lat'], device=device)
            xhat = model.dec(z, c)
        df = inverse_transform(xhat.cpu().numpy(), node_scaler)
        df.insert(0, 'node', range(len(df)))
        gen_dir = a.graphs_dir / '_gen'; gen_dir.mkdir(parents=True, exist_ok=True)
        stem = f'cgen_{a.typology.lower()}_{n}'
        df.to_csv(gen_dir / f'{stem}.csv', index=False)
        edges_from_latent(z).to_csv(gen_dir / f'{stem}_edges.csv', index=False)
        pcols = [f'prog__{b}' for b in ROSSETTI_BUCKETS if f'prog__{b}' in df.columns]
        mix = df[pcols].mean().sort_values(ascending=False)
        print(f'generated {n}-node {a.typology} graph -> {gen_dir/(stem+".csv")}')
        print('  mean program mix: ' + ', '.join(
            f'{c.split("__")[1]} {v:.2f}' for c, v in mix.head(4).items()))


if __name__ == '__main__':
    main()
