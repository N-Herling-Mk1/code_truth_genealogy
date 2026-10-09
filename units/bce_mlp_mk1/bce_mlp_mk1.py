# -*- coding: utf-8 -*-
"""
bce_mlp_mk1 -- vanilla MLP pair + weighted BCE            (rung R1)

    R1 = BCE(NN1) + BCE(NN2)

Two independent MLPs (NN1, NN2) score the same events from two disjoint
feature sets. Nothing ties the two scores together at this rung; it is the
baseline every later rung is diffed against.

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


def loss_r1(logits1, logits2, y, w=None):
    """R1 = BCE(NN1) + BCE(NN2). Returns (total, parts)."""
    l1, l2 = bce(logits1, y, w), bce(logits2, y, w)
    total = l1 + l2
    return total, {"bce1": l1.detach(), "bce2": l2.detach()}
