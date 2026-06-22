"""
vgae_train.py  (Module 18 - VGAE over the SAD graph corpus)

Trains a variational graph autoencoder on the attributed urban graphs from
graph_builder.py (Module 17). The latent it learns does double duty:

  - reconstructs NODE FEATURES  -> the Grietzer-style attribute autoencoding
                                   (a learned, decodable manifold; recon error
                                    = the excess of a place over the model's gestalt)
  - reconstructs ADJACENCY       -> the urban structure / relations

That gives you (a) a graph-level embedding per SAD for retrieval ("recognize
comparable settings") and (b) the decodable latent the generative track needs.

INPUT   data/_graphs/*_norm.npz  (+ _corpus/node_scaler.json) from M17
OUTPUT  data/_graphs/_model/
          vgae.pt                weights + config (feature_names, dims)
          graph_embeddings.csv   one row per graph: sad_id, name, z0..z{D-1}
          node_embeddings.parquet  (only with --dump-nodes)
          training_log.json

USAGE
  pip install torch torch_geometric
  python vgae_train.py --graphs-dir ..\\data\\_graphs --epochs 200 --latent-dim 16
  # later, for district-level retrieval embeddings (build that corpus first):
  python graph_builder.py --all --data-dir ..\\data --level district
  python vgae_train.py --graphs-dir ..\\data\\_graphs --encode-only \\
      --model ..\\data\\_graphs\\_model\\vgae.pt
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
from torch_geometric.nn import GCNConv, global_mean_pool
from torch_geometric.utils import negative_sampling


# ─── Load corpus ─────────────────────────────────────────────────────────────

def sad_from_name(name: str) -> str:
    """'32_District-Detroit__1_2' -> '32_District-Detroit'; district names pass through."""
    return name.split('__')[0]


def load_corpus(graphs_dir: Path) -> tuple[list[Data], list[str]]:
    files = sorted(glob.glob(str(graphs_dir / '*_norm.npz')))
    if not files:  # fall back to raw if normalized not present
        files = [f for f in sorted(glob.glob(str(graphs_dir / '*.npz')))
                 if not f.endswith('_norm.npz')]
    graphs, names = [], []
    for f in files:
        d = np.load(f, allow_pickle=True)
        x = torch.tensor(d['X'], dtype=torch.float32).clamp_(-10, 10)
        ei = torch.tensor(d['edge_index'], dtype=torch.long)
        g = Data(x=x, edge_index=ei)
        g.name = Path(f).stem.replace('_norm', '')
        graphs.append(g); names.append(g.name)
    feat_names = list(np.load(files[0], allow_pickle=True)['feature_names'])
    return graphs, feat_names


def diagnose(graphs: list[Data], feat_names: list[str]):
    """Quick pre-training sanity report — gates obvious pathologies."""
    X = torch.cat([g.x for g in graphs], 0).numpy()
    deg = []
    iso = 0
    for g in graphs:
        n = g.num_nodes
        d = np.bincount(g.edge_index[0].numpy(), minlength=n) if g.edge_index.numel() else np.zeros(n)
        deg.append(d); iso += int((d == 0).sum())
    deg = np.concatenate(deg) if deg else np.array([0])
    const = [feat_names[i] for i in range(X.shape[1]) if X[:, i].std() < 1e-6]
    print(f'  corpus: {len(graphs)} graphs, {X.shape[0]} nodes, {X.shape[1]} features')
    print(f'  node degree: mean {deg.mean():.1f}, median {np.median(deg):.0f}, '
          f'isolated {iso} ({100*iso/max(len(deg),1):.1f}%)')
    print(f'  feature range: [{X.min():.2f}, {X.max():.2f}]'
          + (f'  | WARN constant cols: {const}' if const else ''))


# ─── Model ───────────────────────────────────────────────────────────────────

class Encoder(nn.Module):
    def __init__(self, in_dim, hid, lat):
        super().__init__()
        self.c1 = GCNConv(in_dim, hid)
        self.c_mu = GCNConv(hid, lat)
        self.c_ls = GCNConv(hid, lat)

    def forward(self, x, ei):
        h = F.relu(self.c1(x, ei))
        return self.c_mu(h, ei), self.c_ls(h, ei)


class FeatureDecoder(nn.Module):
    def __init__(self, lat, hid, out_dim):
        super().__init__()
        self.net = nn.Sequential(nn.Linear(lat, hid), nn.ReLU(), nn.Linear(hid, out_dim))

    def forward(self, z):
        return self.net(z)


class GraphVAE(nn.Module):
    """VGAE with both a structure (inner-product) and a feature (MLP) decoder."""
    def __init__(self, in_dim, hid=64, lat=16):
        super().__init__()
        self.enc = Encoder(in_dim, hid, lat)
        self.fdec = FeatureDecoder(lat, hid, in_dim)
        self.cfg = dict(in_dim=in_dim, hid=hid, lat=lat)

    def encode(self, x, ei, sample=True):
        mu, logstd = self.enc(x, ei)
        logstd = logstd.clamp(max=10)
        if not sample:
            return mu, mu, logstd
        z = mu + torch.randn_like(mu) * torch.exp(logstd)
        return z, mu, logstd

    def recon_struct(self, z, pos_ei):
        """BCE link-prediction loss with negative sampling."""
        def logits(ei):
            return (z[ei[0]] * z[ei[1]]).sum(-1)
        pos = logits(pos_ei)
        neg_ei = negative_sampling(pos_ei, num_nodes=z.size(0),
                                   num_neg_samples=pos_ei.size(1))
        neg = logits(neg_ei)
        y = torch.cat([torch.ones_like(pos), torch.zeros_like(neg)])
        return F.binary_cross_entropy_with_logits(torch.cat([pos, neg]), y)


def kl(mu, logstd):
    return -0.5 * torch.mean(1 + 2 * logstd - mu.pow(2) - torch.exp(2 * logstd))


# ─── Train / encode ──────────────────────────────────────────────────────────

def train(model, loader, device, epochs, lr, feat_w, struct_w, beta, log):
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    model.train()
    for ep in range(1, epochs + 1):
        tot = fl = sl = kll = 0.0
        for batch in loader:
            batch = batch.to(device)
            opt.zero_grad()
            z, mu, logstd = model.encode(batch.x, batch.edge_index, sample=True)
            lf = F.mse_loss(model.fdec(z), batch.x)
            ls = model.recon_struct(z, batch.edge_index)
            lk = kl(mu, logstd)
            loss = feat_w * lf + struct_w * ls + beta * lk
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=5.0)
            opt.step()
            tot += loss.item(); fl += lf.item(); sl += ls.item(); kll += lk.item()
        nb = len(loader)
        if ep % 10 == 0 or ep == 1:
            print(f'  epoch {ep:4d}  loss {tot/nb:.4f}  feat {fl/nb:.4f}  '
                  f'struct {sl/nb:.4f}  kl {kll/nb:.4f}')
        log.append(dict(epoch=ep, loss=tot/nb, feat=fl/nb, struct=sl/nb, kl=kll/nb))
    return model


@torch.no_grad()
def encode_graphs(model, graphs, device, dump_nodes=False):
    model.eval()
    rows, node_rows = [], []
    for g in graphs:
        g = g.to(device)
        _, mu, _ = model.encode(g.x, g.edge_index, sample=False)
        batch = torch.zeros(mu.size(0), dtype=torch.long, device=device)
        gz = global_mean_pool(mu, batch).squeeze(0).cpu().numpy()
        rows.append(dict(sad_id=sad_from_name(g.name), name=g.name,
                         **{f'z{i}': float(v) for i, v in enumerate(gz)}))
        if dump_nodes:
            m = mu.cpu().numpy()
            for i in range(m.shape[0]):
                node_rows.append(dict(name=g.name, node=i,
                                      **{f'z{j}': float(m[i, j]) for j in range(m.shape[1])}))
    return pd.DataFrame(rows), (pd.DataFrame(node_rows) if dump_nodes else None)


def main():
    ap = argparse.ArgumentParser(description='VGAE over SAD graph corpus (M18)')
    ap.add_argument('--graphs-dir', type=Path, required=True)
    ap.add_argument('--epochs', type=int, default=200)
    ap.add_argument('--latent-dim', type=int, default=16)
    ap.add_argument('--hidden', type=int, default=64)
    ap.add_argument('--lr', type=float, default=3e-3)
    ap.add_argument('--batch-size', type=int, default=16)
    ap.add_argument('--feat-weight', type=float, default=1.0)
    ap.add_argument('--struct-weight', type=float, default=1.0)
    ap.add_argument('--beta', type=float, default=0.1, help='KL weight')
    ap.add_argument('--dump-nodes', action='store_true')
    ap.add_argument('--encode-only', action='store_true')
    ap.add_argument('--model', type=Path, help='checkpoint for --encode-only')
    ap.add_argument('--seed', type=int, default=0)
    a = ap.parse_args()

    torch.manual_seed(a.seed); np.random.seed(a.seed)
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f'device: {device}')

    graphs, feat_names = load_corpus(a.graphs_dir)
    if not graphs:
        ap.error(f'no *.npz graphs found in {a.graphs_dir}')
    diagnose(graphs, feat_names)
    in_dim = graphs[0].x.size(1)
    out_dir = a.graphs_dir / '_model'; out_dir.mkdir(parents=True, exist_ok=True)

    if a.encode_only:
        ckpt = torch.load(a.model, map_location=device, weights_only=False)
        model = GraphVAE(**ckpt['cfg']).to(device)
        model.load_state_dict(ckpt['state_dict'])
        assert in_dim == ckpt['cfg']['in_dim'], 'feature dim mismatch vs checkpoint'
        emb, nodes = encode_graphs(model, graphs, device, a.dump_nodes)
        emb.to_csv(out_dir / 'graph_embeddings.csv', index=False)
        if nodes is not None: nodes.to_parquet(out_dir / 'node_embeddings.parquet')
        print(f'[OK] encoded {len(emb)} graphs -> {out_dir/"graph_embeddings.csv"}')
        return

    loader = DataLoader(graphs, batch_size=a.batch_size, shuffle=True)
    model = GraphVAE(in_dim, a.hidden, a.latent_dim).to(device)
    print(f'training VGAE: in_dim={in_dim} hidden={a.hidden} latent={a.latent_dim}')
    log: list[dict] = []
    model = train(model, loader, device, a.epochs, a.lr,
                  a.feat_weight, a.struct_weight, a.beta, log)

    torch.save(dict(state_dict=model.state_dict(), cfg=model.cfg,
                    feature_names=[str(x) for x in feat_names]), out_dir / 'vgae.pt')
    emb, nodes = encode_graphs(model, graphs, device, a.dump_nodes)
    emb.to_csv(out_dir / 'graph_embeddings.csv', index=False)
    if nodes is not None: nodes.to_parquet(out_dir / 'node_embeddings.parquet')
    (out_dir / 'training_log.json').write_text(json.dumps(log, indent=2))
    print(f'\n[OK] model -> {out_dir/"vgae.pt"}')
    print(f'     {len(emb)} graph embeddings (dim {a.latent_dim}) '
          f'-> {out_dir/"graph_embeddings.csv"}')


if __name__ == '__main__':
    main()
