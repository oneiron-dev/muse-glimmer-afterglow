"""Statistics for the Afterglow evals (BUILD-PLAN section 8). Pure Python.

- paired_cluster_bootstrap: mean per-cluster difference with a percentile 95% interval (clusters = facts).
- paired_proportions: before/after on the same items: exact McNemar p and the Newcombe (1998, method 10)
  hybrid-score 95% interval for after - before.
"""

import math
import random

BOOTSTRAP = 10_000
SEED = 20261014
Z = 1.959963984540054


def paired_cluster_bootstrap(a, b, resamples=BOOTSTRAP, seed=SEED):
    """a, b: dict cluster -> value. Returns mean(b - a) and a 95% percentile interval."""
    keys = sorted(set(a) & set(b))
    diffs = [b[k] - a[k] for k in keys]
    if not diffs:
        return {"delta": None, "ci95": None, "clusters": 0}
    rng = random.Random(seed)
    n = len(diffs)
    means = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(resamples))
    return {"delta": round(sum(diffs) / n, 4), "ci95": [round(means[int(0.025 * resamples)], 4),
                                                       round(means[int(0.975 * resamples) - 1], 4)], "clusters": n}


def wilson(x, n):
    if n == 0:
        return 0.0, 1.0
    p = x / n
    denom = 1 + Z * Z / n
    centre = (p + Z * Z / (2 * n)) / denom
    half = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / denom
    return centre - half, centre + half


def mcnemar_exact(b, c):
    """Two-sided exact McNemar p for b, c discordant pairs."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def paired_proportions(before, after):
    """before, after: dict item -> bool. Returns rates, discordant counts, McNemar p, Newcombe CI of after - before."""
    keys = sorted(set(before) & set(after))
    n = len(keys)
    a = sum(1 for k in keys if before[k] and after[k])
    b = sum(1 for k in keys if before[k] and not after[k])  # lost
    c = sum(1 for k in keys if not before[k] and after[k])  # gained
    d = n - a - b - c
    p1, p2 = (a + b) / n, (a + c) / n
    l1, u1 = wilson(a + b, n)
    l2, u2 = wilson(a + c, n)
    den = math.sqrt((a + b) * (c + d) * (a + c) * (b + d))
    phi = (a * d - b * c) / den if den else 0.0
    theta = p1 - p2
    delta = math.sqrt(max(0.0, (p1 - l1) ** 2 - 2 * phi * (p1 - l1) * (u2 - p2) + (u2 - p2) ** 2))
    eps = math.sqrt(max(0.0, (u1 - p1) ** 2 - 2 * phi * (u1 - p1) * (p2 - l2) + (p2 - l2) ** 2))
    lo, hi = -(theta + eps), -(theta - delta)
    return {"n": n, "before": round(p1, 4), "after": round(p2, 4), "after_minus_before": round(p2 - p1, 4),
            "newcombe95": [round(lo, 4), round(hi, 4)], "lost": b, "gained": c, "mcnemar_p": round(mcnemar_exact(b, c), 4)}
