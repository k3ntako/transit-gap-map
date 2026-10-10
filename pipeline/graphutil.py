"""Shared graph helpers: undirected CSR construction and cached-graph I/O."""

from __future__ import annotations

from typing import NamedTuple

import numpy as np
from scipy.sparse import csr_matrix


def build_undirected_csr(u, v, w, n_nodes: int) -> csr_matrix:
    """Symmetric CSR adjacency keeping the MINIMUM weight per node pair.

    This must not be done by handing duplicate coordinates to csr_matrix:
    that *sums* duplicates, which silently inflates the cost of any street
    represented by parallel ways or appearing in both directions. OSM walk
    networks contain plenty of both.
    """
    u = np.asarray(u, dtype=np.int64)
    v = np.asarray(v, dtype=np.int64)
    w = np.asarray(w, dtype=np.float64)

    keep = (u != v) & np.isfinite(w) & (w >= 0)   # drop self-loops and junk
    u, v, w = u[keep], v[keep], w[keep]

    # Mirror every edge, then collapse duplicates to their minimum weight.
    uu = np.concatenate([u, v])
    vv = np.concatenate([v, u])
    ww = np.concatenate([w, w])

    key = uu * np.int64(n_nodes) + vv
    order = np.lexsort((ww, key))           # ties broken by ascending weight
    key, uu, vv, ww = key[order], uu[order], vv[order], ww[order]
    first = np.ones(len(key), dtype=bool)
    first[1:] = key[1:] != key[:-1]         # first row per pair holds the min

    return csr_matrix((ww[first], (uu[first], vv[first])),
                      shape=(n_nodes, n_nodes))


def save_graph(path, csr: csr_matrix, x, y, osmid, component) -> None:
    np.savez_compressed(
        path,
        indptr=csr.indptr, indices=csr.indices, data=csr.data,
        n_nodes=np.array([csr.shape[0]]),
        x=np.asarray(x, dtype=np.float64),
        y=np.asarray(y, dtype=np.float64),
        osmid=np.asarray(osmid, dtype=np.int64),
        component=np.asarray(component, dtype=np.int32),
    )


class WalkGraph(NamedTuple):
    """The cached pedestrian network: CSR adjacency plus the per-node arrays."""

    csr: csr_matrix
    x: np.ndarray
    y: np.ndarray
    osmid: np.ndarray
    component: np.ndarray


def load_graph(path) -> WalkGraph:
    """Load the cached walk graph.

    There is deliberately no `with_components` flag. save_graph always writes
    the component labels, so the flag only ever chose whether to hand them
    back -- and it did so by changing the *shape* of the return value based on
    an argument's value, which no type checker can narrow. Every one of the
    five call sites therefore reported a spurious assignment error, which is a
    lot of noise to buy nothing. Returning one named shape costs the callers
    that want only coordinates a single extra line.
    """
    z = np.load(path)
    n = int(z["n_nodes"][0])
    return WalkGraph(
        csr=csr_matrix((z["data"], z["indices"], z["indptr"]), shape=(n, n)),
        x=z["x"], y=z["y"], osmid=z["osmid"], component=z["component"])
