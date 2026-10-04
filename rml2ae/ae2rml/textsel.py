"""AE text range selectors evaluated per unit (character / word / line), for what Rive's TextModifierRange cannot
express: ease high / low, randomize order, wiggly selectors, and the animator properties Rive has no modifier for
(tracking). The values are baked per unit and per frame into one Rive range per unit.

The range formulas are lottie-web's (TextSelectorProp.getMult, MIT), which reproduce AE's shapes, smoothness and
ease; the wiggly selector and the random order use AE's documented behaviour, not its exact noise (approximated)."""
import math
import random


def _bezier_ease(x1, y1, x2, y2):
    """cubic-bezier(x1, y1, x2, y2) as a function of x in [0, 1]"""
    if abs(x1 - y1) < 1e-9 and abs(x2 - y2) < 1e-9:
        return lambda x: x

    def bx(t):
        return 3 * (1 - t) ** 2 * t * x1 + 3 * (1 - t) * t * t * x2 + t ** 3

    def by(t):
        return 3 * (1 - t) ** 2 * t * y1 + 3 * (1 - t) * t * t * y2 + t ** 3

    def f(x):
        if x <= 0:
            return 0.0
        if x >= 1:
            return 1.0
        lo, hi = 0.0, 1.0
        for _ in range(40):
            m = (lo + hi) / 2
            if bx(m) < x:
                lo = m
            else:
                hi = m
        return by((lo + hi) / 2)
    return f


def range_mult(ind, s, e, shape, smooth, ease_high, ease_low):
    """lottie-web getMult: strength 0..1 of unit `ind` (0-based) for a range [s, e] in units"""
    x1, y1, x2, y2 = 0.0, 0.0, 1.0, 1.0
    if ease_low > 0:
        x1 = ease_low / 100.0
    else:
        y1 = -ease_low / 100.0
    if ease_high > 0:
        x2 = 1.0 - ease_high / 100.0
    else:
        y2 = 1.0 + ease_high / 100.0
    easer = _bezier_ease(x1, y1, x2, y2)
    if s > e:
        s, e = e, s
    mult = 0.0
    if shape == 2:                                    # ramp up
        mult = (1.0 if ind >= e else 0.0) if e == s else max(0.0, min(0.5 / (e - s) + (ind - s) / (e - s), 1.0))
        mult = easer(mult)
    elif shape == 3:                                  # ramp down
        mult = (0.0 if ind >= e else 1.0) if e == s else 1.0 - max(0.0, min(0.5 / (e - s) + (ind - s) / (e - s), 1.0))
        mult = easer(mult)
    elif shape == 4:                                  # triangle
        if e != s:
            mult = max(0.0, min(0.5 / (e - s) + (ind - s) / (e - s), 1.0))
            mult = mult * 2 if mult < 0.5 else 1 - 2 * (mult - 0.5)
        mult = easer(mult)
    elif shape == 5:                                  # round
        if e != s:
            tot = e - s
            i = min(max(0.0, ind + 0.5 - s), tot)
            x = -tot / 2 + i
            a = tot / 2
            mult = math.sqrt(max(0.0, 1 - (x * x) / (a * a)))
        mult = easer(mult)
    elif shape == 6:                                  # smooth
        if e != s:
            i = min(max(0.0, ind + 0.5 - s), e - s)
            mult = (1 + math.cos(math.pi + math.pi * 2 * i / (e - s))) / 2
        mult = easer(mult)
    else:                                             # square: the share of the unit inside the range
        if ind >= math.floor(s):
            if ind - s < 0:
                mult = max(0.0, min(min(e, ind + 1) - s, 1.0))
            else:
                mult = max(0.0, min(e - ind, 1.0))
        mult = easer(mult)
    if abs(smooth - 100.0) > 1e-9 and shape == 1:
        sm = max(smooth / 100.0, 1e-8)
        th = 0.5 - sm * 0.5
        mult = 0.0 if mult < th else min(1.0, (mult - th) / sm)
    return mult


def random_order(n, seed):
    """a fixed permutation of the units (AE shuffles them by its random seed; its own generator is not public)"""
    order = list(range(n))
    random.Random(1000003 * (int(seed) + 1) + n).shuffle(order)
    return order


def combine(mode, acc, v):
    """AE selector modes, applied in order (the first selector starts from 0 in add mode)"""
    if mode == "add":
        return acc + v
    if mode == "subtract":
        return acc - v
    if mode == "multiply":
        return acc * v
    if mode == "min":
        return min(acc, v)
    if mode == "max":
        return max(acc, v)
    if mode == "difference":
        return abs(acc - v)
    return acc + v


def smooth_noise(seed, x):
    """1D value noise in [-1, 1] (smoothstep between seeded lattice values)"""
    i = math.floor(x)
    f = x - i

    def v(k):
        return random.Random(seed * 7919 + k * 104729).uniform(-1.0, 1.0)
    u = f * f * (3 - 2 * f)
    return v(i) * (1 - u) + v(i + 1) * u
