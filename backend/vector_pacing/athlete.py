"""Critical power (CP) / W′ balance model.

W′ (W-prime, joules) is the finite work capacity above critical power. This
module tracks the W′ balance (W′bal) step by step using the differential form
of the Skiba model (Skiba et al., 2015), integrated exactly for piecewise-
constant power over each step of duration ``dt``:

* Depletion (``P > CP``), linear::

      W′bal ← W′bal − (P − CP)·dt

* Recovery (``P ≤ CP``), exponential approach to the full capacity ``W′₀``::

      dW′bal/dt = (CP − P)·(W′₀ − W′bal)/W′₀
      ⇒ W′bal ← W′₀ − (W′₀ − W′bal)·exp(−(CP − P)·dt / W′₀)

Recovery is faster the further power is below CP and slows as the balance
approaches full. This is an approximation; real recovery kinetics vary between
athletes and conditions.

The balance is **never clamped**. If it falls below zero, the effort exceeds
the model's estimate of the athlete's capacity and the plan is reported as
infeasible, with the actual negative value shown.
"""

from __future__ import annotations

import math

import numpy as np

FEASIBILITY_TOLERANCE_J = 1e-6


def w_prime_balance(power_w: np.ndarray, dt_s: np.ndarray, cp_w: float, w_prime_j: float) -> np.ndarray:
    """Return W′bal [J] at the end of each step (start of course = ``w_prime_j``)."""
    if cp_w <= 0 or w_prime_j <= 0:
        raise ValueError("CP and W′ must be positive.")
    out = np.empty(len(power_w), dtype=float)
    w = float(w_prime_j)
    inv = 1.0 / w_prime_j
    for i, (p, dt) in enumerate(zip(power_w.tolist(), dt_s.tolist(), strict=True)):
        if p > cp_w:
            w -= (p - cp_w) * dt
        else:
            w = w_prime_j - (w_prime_j - w) * math.exp(-(cp_w - p) * dt * inv)
        out[i] = w
    return out
