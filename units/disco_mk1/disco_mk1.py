# -*- coding: utf-8 -*-
"""
disco_mk1 -- MLP pair + weighted BCE + lambda * DisCo      (rung R2)

    R2 = BCE(NN1) + BCE(NN2) + lambda * dCor^2(s1, s2 | background)

Double-DisCo: the two scores are decorrelated on BACKGROUND events only, so
the ABCD plane built from (s1, s2) factorises for background.

Conventions used by every unit in this lineage
    logits : raw network outputs, shape (n,)
    y      : labels, shape (n,), 1 = signal, 0 = background
             (pass bkg_label=1 downstream if your target polarity is inverted)
    w      : per-event weights, shape (n,), or None for unit weights
"""
import torch
import torch.nn as nn
import torch.nn.functional as F


class MLP(nn.Module):
    """Plain fully connected classifier. forward() returns logits, shape (n,)."""

    def __init__(self, n_in, hidden=(64, 64), dropout=0.0):
        super().__init__()
        layers, d = [], n_in
        for h in hidden:
            layers += [nn.Linear(d, h), nn.ReLU()]
            if dropout > 0:
                layers.append(nn.Dropout(dropout))
            d = h
        layers.append(nn.Linear(d, 1))
        self.net = nn.Sequential(*layers)

    def forward(self, x):
        return self.net(x).squeeze(-1)


def bce(logits, y, w=None):
    """Weighted binary cross-entropy on logits; weighted mean over events."""
    per_event = F.binary_cross_entropy_with_logits(logits, y.float(), reduction="none")
    if w is None:
        return per_event.mean()
    return (w * per_event).sum() / w.sum().clamp_min(1e-12)


def _centered(x, p):
    """Weighted double-centred distance matrix of a 1-D sample. p sums to 1."""
    d = (x[:, None] - x[None, :]).abs()
    row = (d * p[None, :]).sum(dim=1)          # weighted mean over j, per i
    grand = (row * p).sum()
    return d - row[:, None] - row[None, :] + grand


def dcor2(a, b, w=None, eps=1e-12):
    """Weighted squared distance correlation of two 1-D samples, in [0, 1].

    dCov^2(a,b) = sum_ij p_i p_j A_ij B_ij   with p = w / sum(w)
    dCor^2      = dCov^2(a,b) / sqrt(dCov^2(a,a) * dCov^2(b,b))
    Memory is O(n^2): call it per batch, not on a full dataset.
    """
    p = torch.ones_like(a) if w is None else w
    p = p / p.sum().clamp_min(eps)
    A, B = _centered(a, p), _centered(b, p)
    pp = p[:, None] * p[None, :]
    ab, aa, bb = (pp * A * B).sum(), (pp * A * A).sum(), (pp * B * B).sum()
    return ab / (aa * bb).clamp_min(eps).sqrt()


def disco_bkg(logits1, logits2, y, w=None, bkg_label=0):
    """dCor^2 between the two sigmoid scores on background events only.
    Returns 0 (with a graph) when the batch has fewer than 2 background events."""
    m = y == bkg_label
    if int(m.sum()) < 2:
        return (logits1.sum() + logits2.sum()) * 0.0
    s1, s2 = torch.sigmoid(logits1[m]), torch.sigmoid(logits2[m])
    return dcor2(s1, s2, None if w is None else w[m])


def loss_r2(logits1, logits2, y, w=None, lam=1.0, bkg_label=0):
    """R2 = BCE(NN1) + BCE(NN2) + lam * dCor^2(bkg). Returns (total, parts)."""
    l1, l2 = bce(logits1, y, w), bce(logits2, y, w)
    dc = disco_bkg(logits1, logits2, y, w, bkg_label)
    total = l1 + l2 + lam * dc
    return total, {"bce1": l1.detach(), "bce2": l2.detach(), "dcor2": dc.detach()}
