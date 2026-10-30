"""Offline tests for the KL alignment guard in night/train_adapter.py (CPU torch, no model):

  python3 -m unittest discover -s night/tests

Skipped where torch is missing (the Mac); the training hosts' venv has it.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import train_adapter as ta  # noqa: E402

try:
    import torch
except ImportError:
    torch = None

VOCAB = 4096


def full_forward(n, seed=0):
    """Logits for n positions at the real scale (|logit| up to ~30), each position its own distribution."""
    g = torch.Generator().manual_seed(seed)
    x = torch.randn(n, VOCAB, generator=g) * 6
    x[torch.arange(n), torch.randint(0, VOCAB, (n,), generator=g)] = 30.0  # a clear top token per position
    return x.bfloat16().float()


def regemm(x):
    """The same logits through a differently ordered bf16 GEMM: one or two ulps off on some entries."""
    g = torch.Generator().manual_seed(1)
    ulp = torch.tensor([ta.bf16_ulp(float(v)) for v in x.abs().amax(-1)]).unsqueeze(-1)
    return (x + torch.randint(-1, 2, x.shape, generator=g) * ulp * (torch.rand(x.shape, generator=g) < 0.02)).bfloat16().float()


@unittest.skipIf(torch is None, "torch is not installed")
class AlignmentStats(unittest.TestCase):
    def setUp(self):
        self.n, self.start = 400, 99
        self.full = full_forward(self.n)
        self.pos = list(range(self.start + 1, self.start + self.n - 1))  # reply positions inside the forward

    def tail(self, shift=0):
        return regemm(self.full[[p + shift - self.start for p in self.pos]])

    def test_ulp(self):
        self.assertEqual(ta.bf16_ulp(30.0), 0.125)
        self.assertEqual(ta.bf16_ulp(1.0), 2.0 ** -7)

    def test_aligned_bf16_rounding_passes(self):
        s = ta.alignment_stats(self.tail(), self.full, self.pos, self.start)
        self.assertGreater(s["aligned_max_abs"], 0.05)  # the old absolute bound would have stopped this
        self.assertTrue(s["passed"], s)
        self.assertEqual(s["top_token_agree"], 1.0)
        self.assertLess(s["top_token_agree_off_by_one"], 0.05)

    def test_off_by_one_fails(self):
        for shift in (-1, 1):
            s = ta.alignment_stats(self.tail(shift), self.full, self.pos, self.start)
            self.assertFalse(s["passed"], s)
            self.assertLess(s["top_token_agree"], 0.05)

    def test_hard_top_token_flip_fails(self):
        t = self.tail()
        i = 7
        t[i, t[i].argmax()] -= 20.0  # a real disagreement, far above the bf16 tolerance
        s = ta.alignment_stats(t, self.full, self.pos, self.start)
        self.assertFalse(s["passed"], s)
        self.assertGreaterEqual(s["hard_disagreements"], 1)

    def test_near_tie_flip_is_not_hard(self):
        full = self.full.clone()
        i = 7
        top = int(full[i + self.pos[0] - self.start].argmax())
        other = (top + 1) % VOCAB
        full[i + self.pos[0] - self.start, other] = 30.0  # an exact tie in the full forward
        t = full[[p - self.start for p in self.pos]].clone()
        t[i, other] += ta.bf16_ulp(30.0)  # the tail breaks the tie the other way
        s = ta.alignment_stats(t, full, self.pos, self.start)
        self.assertEqual(s["hard_disagreements"], 0, s)
        self.assertTrue(s["passed"], s)


if __name__ == "__main__":
    unittest.main()
