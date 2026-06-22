"""
generate.py  (Module 20 - latent generation over the trained VGAE)

Uses the VGAE (M18) as a generative instrument on the attributed urban graphs.
Three operations that work at the corpus's real graph sizes (~hundreds of nodes):

  reconstruct  encode a real SAD/tile, decode it back, and report the per-block
               reconstruction error -- the Grietzer "excess" of the place over
               the model's learned gestalt.
  perturb      encode, jitter the node latents by sigma, decode a VARIANT layout
               anchored to the precedent. "Guide the hand toward the corpus."
  steer        find the latent direction correlated with a target attribute
               (e.g. retail program) and move along it -- "more/less retail."

Output is an ATTRIBUTED GRAPH: per-node program mix / building size / demographic
context / zone, plus a predicted adjacency. It is NOT drawn geometry -- turning a
generated graph into placed footprints is a separate downstream step.

INPUT   data/_graphs/ (the M17 corpus: *_norm.npz + _corpus/node_scaler.json)
        data/_graphs/_model/vgae.pt  (the M18 model)
OUTPUT  data/_graphs/_gen/<op>_<name>.csv   decoded node attributes
        ..._edges.csv                       predicted adjacency

USAGE
  python generate.py reconstruct --graphs-dir ..\\data\\_graphs --name 32_District-Detroit__1_2
  python generate.py perturb     --graphs-dir ..\\data\\_graphs --name 32_District-Detroit__1_2 --sigma 0.6
  python generate.py steer       --graphs-dir ..\\data\\_graphs --name 32_District-Detroit__1_2 \\
                                 --feature prog__retail_food_entertainment --amount 2.5
"""
from __future__ import annotations
import argparse, json
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from sklearn.linear_model import LinearRegression

from vgae_train import GraphVAE, load_corpus
from graph_builder import SIZE_FEATURES, HU_FEATURES, LOG_DEMO, ROSSETTI_BUCKETS


# ─── decode standardized latent features back to interpretable units ─────────

def load_scaler(graphs_dir: Path) -> dict:
    return json.loads((graphs_dir / '_corpus' / 'node_scaler.json').read_text())


def inverse_transform(X_std: np.ndarray, scaler: dict) -> pd.DataFrame:
    names = scaler['feature_names']
    center = np.array(scaler['center']); scale = np.array(scaler['scale'])
    X = X_std * scale + center                      # undo robust scaling
    out = X.copy()
    for j, n in enumerate(names):
        base = n.split('__', 1)[1]
        if n.startswith('shape__') and base in SIZE_FEATURES:
            out[:, j] = np.expm1(np.clip(X[:, j], 0, 14))            # <= ~1.2M m2
        elif n.startswith('shape__') and base in HU_FEATURES:
            out[:, j] = np.sign(X[:, j]) * np.power(10.0, np.clip(np.abs(X[:, j]), 0, 12))
        elif n.startswith('demo__') and base in LOG_DEMO:
            out[:, j] = np.expm1(np.clip(X[:, j], 0, 16))
        # else identity: program shares, zone, present, median_age
    df = pd.DataFrame(out, columns=names)
    # program shares: clip to [0,1] and renormalize for readability
    pcols = [f'prog__{b}' for b in ROSSETTI_BUCKETS if f'prog__{b}' in df.columns]
    if pcols:
        df[pcols] = df[pcols].clip(0, None)
        s = df[pcols].sum(axis=1).replace(0, 1)
        df[pcols] = df[pcols].div(s, axis=0).round(3)
    return df


def edges_from_latent(z: torch.Tensor, topk: int = 6) -> pd.DataFrame:
    """Predicted adjacency: top-k highest inner-product neighbours per node."""
    with torch.no_grad():
        S = (z @ z.t())
        S.fill_diagonal_(-1e9)
        k = min(topk, z.size(0) - 1)
        idx = S.topk(k, dim=1).indices.cpu().numpy()
    rows = [dict(src=i, dst=int(j)) for i in range(z.size(0)) for j in idx[i]]
    return pd.DataFrame(rows)


# ─── load model + a target graph ─────────────────────────────────────────────

def load_model(model_path: Path, device):
    ckpt = torch.load(model_path, map_location=device, weights_only=False)
    m = GraphVAE(**ckpt['cfg']).to(device)
    m.load_state_dict(ckpt['state_dict']); m.eval()
    return m, ckpt['feature_names']


def get_graph(graphs, name):
    for g in graphs:
        if g.name == name:
            return g
    matches = [g for g in graphs if name.lower() in g.name.lower()]
    if matches:
        return matches[0]
    raise SystemExit(f'no graph matching "{name}". e.g. {graphs[0].name}')


# ─── operations ──────────────────────────────────────────────────────────────

def op_reconstruct(model, g, scaler, device, out_dir):
    with torch.no_grad():
        _, mu, _ = model.encode(g.x.to(device), g.edge_index.to(device), sample=False)
        xhat = model.fdec(mu)
    err = torch.mean((xhat - g.x.to(device)) ** 2, dim=0).cpu().numpy()
    names = scaler['feature_names']
    def block(pfx): return float(np.mean([err[i] for i, n in enumerate(names) if n.startswith(pfx)]))
    print(f'  reconstruction error (standardized MSE):')
    print(f'    shape {block("shape__"):.3f} | program {block("prog__"):.3f} | '
          f'demographics {block("demo__"):.3f} | zone {block("zone__"):.3f}')
    df = inverse_transform(xhat.cpu().numpy(), scaler)
    df.insert(0, 'node', range(len(df)))
    df.to_csv(out_dir / f'reconstruct_{g.name}.csv', index=False)
    edges_from_latent(mu).to_csv(out_dir / f'reconstruct_{g.name}_edges.csv', index=False)
    print(f'  wrote reconstruct_{g.name}.csv (+_edges)')


def op_perturb(model, g, scaler, device, out_dir, sigma, seed):
    torch.manual_seed(seed)
    with torch.no_grad():
        _, mu, _ = model.encode(g.x.to(device), g.edge_index.to(device), sample=False)
        z = mu + sigma * torch.randn_like(mu)
        xhat = model.fdec(z)
    df = inverse_transform(xhat.cpu().numpy(), scaler)
    df.insert(0, 'node', range(len(df)))
    df.to_csv(out_dir / f'perturb_{g.name}_s{sigma}.csv', index=False)
    edges_from_latent(z).to_csv(out_dir / f'perturb_{g.name}_s{sigma}_edges.csv', index=False)
    print(f'  wrote perturb_{g.name}_s{sigma}.csv (sigma={sigma}) (+_edges)')


def discover_direction(model, graphs, device, feature, feat_names):
    """Regress node latent -> target standardized feature across the whole
    corpus; the (normalized) coefficients are the steering direction."""
    j = feat_names.index(feature)
    Z, Y = [], []
    with torch.no_grad():
        for g in graphs:
            _, mu, _ = model.encode(g.x.to(device), g.edge_index.to(device), sample=False)
            Z.append(mu.cpu().numpy()); Y.append(g.x[:, j].numpy())
    Z = np.vstack(Z); Y = np.concatenate(Y)
    reg = LinearRegression().fit(Z, Y)
    d = reg.coef_ / (np.linalg.norm(reg.coef_) + 1e-9)
    return torch.tensor(d, dtype=torch.float32, device=device), j


def op_steer(model, g, graphs, scaler, device, out_dir, feature, amount, seed):
    names = scaler['feature_names']
    if feature not in names:
        raise SystemExit(f'feature "{feature}" not in schema. options e.g.: '
                         + ', '.join(n for n in names if n.startswith('prog__')))
    d, j = discover_direction(model, graphs, device, feature, names)
    with torch.no_grad():
        _, mu, _ = model.encode(g.x.to(device), g.edge_index.to(device), sample=False)
        before = inverse_transform(model.fdec(mu).cpu().numpy(), scaler)[feature].mean()
        z = mu + amount * d
        xhat = model.fdec(z)
        after = inverse_transform(xhat.cpu().numpy(), scaler)[feature].mean()
    df = inverse_transform(xhat.cpu().numpy(), scaler)
    df.insert(0, 'node', range(len(df)))
    df.to_csv(out_dir / f'steer_{g.name}_{feature}_{amount}.csv', index=False)
    edges_from_latent(z).to_csv(out_dir / f'steer_{g.name}_{feature}_{amount}_edges.csv', index=False)
    print(f'  steered {feature}: mean {before:.3f} -> {after:.3f} (amount={amount})')
    print(f'  wrote steer_{g.name}_{feature}_{amount}.csv (+_edges)')


def main():
    ap = argparse.ArgumentParser(description='Latent generation over the VGAE (M20)')
    sub = ap.add_subparsers(dest='op', required=True)
    for name in ('reconstruct', 'perturb', 'steer'):
        p = sub.add_parser(name)
        p.add_argument('--graphs-dir', type=Path, required=True)
        p.add_argument('--model', type=Path, default=None)
        p.add_argument('--name', type=str, required=True)
        p.add_argument('--seed', type=int, default=0)
        if name == 'perturb':
            p.add_argument('--sigma', type=float, default=0.5)
        if name == 'steer':
            p.add_argument('--feature', type=str, required=True)
            p.add_argument('--amount', type=float, default=2.0)
    a = ap.parse_args()

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model_path = a.model or (a.graphs_dir / '_model' / 'vgae.pt')
    model, feat_names = load_model(model_path, device)
    scaler = load_scaler(a.graphs_dir)
    graphs, _ = load_corpus(a.graphs_dir)
    g = get_graph(graphs, a.name)
    out_dir = a.graphs_dir / '_gen'; out_dir.mkdir(parents=True, exist_ok=True)
    print(f'{a.op}: {g.name}  ({g.num_nodes} nodes)')

    if a.op == 'reconstruct':
        op_reconstruct(model, g, scaler, device, out_dir)
    elif a.op == 'perturb':
        op_perturb(model, g, scaler, device, out_dir, a.sigma, a.seed)
    elif a.op == 'steer':
        op_steer(model, g, graphs, scaler, device, out_dir, a.feature, a.amount, a.seed)


if __name__ == '__main__':
    main()
