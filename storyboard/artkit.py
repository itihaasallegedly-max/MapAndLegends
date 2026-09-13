"""Shared visual system for the Jharkhand story scenes.

Flat vector illustration, one palette across all six frames, drawn from the
subject rather than a generic template: laterite earth, sal-forest green,
palash orange, and the bone-white line vocabulary of Sohrai wall painting.
"""

W, H = 1080, 1920

# --- palette -----------------------------------------------------------
NIGHT      = "#101e28"
INDIGO     = "#1b3446"
DUSK       = "#31465a"
FOREST_DK  = "#16302b"
FOREST     = "#1f4438"
SAL        = "#2f5c43"
SAL_LT     = "#487a55"
EARTH_DK   = "#6b3520"
EARTH      = "#94502a"
LATERITE   = "#b0603089"
OCHRE      = "#c07a3e"
OCHRE_LT   = "#d99a5c"
PALASH     = "#e2701f"
PALASH_LT  = "#f4913a"
SUN        = "#f6c85f"
BONE       = "#efe4d0"
BONE_DIM   = "#cbbda6"
CREAM      = "#f7efdf"


def svg(body, bg=NIGHT, defs=""):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}">'
        f"<defs>{defs}</defs>"
        f'<rect width="{W}" height="{H}" fill="{bg}"/>'
        f"{body}</svg>"
    )


def vgrad(name, stops):
    s = "".join(f'<stop offset="{o}" stop-color="{c}"/>' for o, c in stops)
    return f'<linearGradient id="{name}" x1="0" y1="0" x2="0" y2="1">{s}</linearGradient>'


def rgrad(name, stops, cx="50%", cy="50%", r="50%"):
    s = "".join(f'<stop offset="{o}" stop-color="{c}"/>' for o, c in stops)
    return f'<radialGradient id="{name}" cx="{cx}" cy="{cy}" r="{r}">{s}</radialGradient>'


def ridge(y, amp, fill, seed=0, steps=7, opacity=1.0):
    """A soft hill silhouette spanning the frame, closed to the bottom."""
    import math

    pts = []
    for i in range(steps + 1):
        x = W * i / steps
        yy = y + math.sin(i * 1.3 + seed) * amp + math.cos(i * 0.7 + seed * 2) * amp * 0.5
        pts.append((x, yy))
    d = f"M -40 {H} L -40 {pts[0][1]:.0f} "
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        cx = (x0 + x1) / 2
        d += f"Q {cx:.0f} {y0:.0f} {x1:.0f} {y1:.0f} "
    d += f"L {W + 40} {H} Z"
    return f'<path d="{d}" fill="{fill}" opacity="{opacity}"/>'


def sal_tree(x, base_y, scale=1.0, fill=FOREST_DK, opacity=1.0):
    """Sal: a tall straight bole with a high, rounded, layered crown."""
    s = scale
    trunk = (
        f'<path d="M {x - 9 * s} {base_y} '
        f"C {x - 7 * s} {base_y - 120 * s} {x - 6 * s} {base_y - 240 * s} {x - 5 * s} {base_y - 330 * s} "
        f"L {x + 5 * s} {base_y - 330 * s} "
        f"C {x + 6 * s} {base_y - 240 * s} {x + 7 * s} {base_y - 120 * s} {x + 9 * s} {base_y} Z\" "
        f'fill="{fill}" opacity="{opacity}"/>'
    )
    crown = ""
    blobs = [
        (0, -430, 92, 74), (-62, -376, 66, 52), (62, -380, 62, 50),
        (-30, -486, 60, 48), (34, -492, 56, 46), (0, -534, 44, 34),
        (-92, -334, 44, 34), (94, -338, 40, 32),
    ]
    for dx, dy, rx, ry in blobs:
        crown += (
            f'<ellipse cx="{x + dx * s:.0f}" cy="{base_y + dy * s:.0f}" '
            f'rx="{rx * s:.0f}" ry="{ry * s:.0f}" fill="{fill}" opacity="{opacity}"/>'
        )
    # a couple of branch stubs into the crown
    br = (
        f'<path d="M {x} {base_y - 300 * s} L {x - 70 * s} {base_y - 372 * s} '
        f'M {x} {base_y - 330 * s} L {x + 68 * s} {base_y - 380 * s}" '
        f'stroke="{fill}" stroke-width="{7 * s:.1f}" fill="none" opacity="{opacity}"/>'
    )
    return trunk + br + crown


def sal_leaf(x, y, length, angle, fill=SAL_LT, opacity=1.0):
    """Sal leaf: broad, oval, pointed tip, prominent midrib."""
    w = length * 0.42
    return (
        f'<g transform="translate({x:.0f},{y:.0f}) rotate({angle})">'
        f'<path d="M 0 0 C {w:.0f} {-length * 0.22:.0f} {w * 0.9:.0f} {-length * 0.78:.0f} 0 {-length:.0f} '
        f'C {-w * 0.9:.0f} {-length * 0.78:.0f} {-w:.0f} {-length * 0.22:.0f} 0 0 Z" '
        f'fill="{fill}" opacity="{opacity}"/>'
        f'<path d="M 0 {-length * 0.06:.0f} L 0 {-length * 0.92:.0f}" stroke="{FOREST_DK}" '
        f'stroke-width="{max(length * 0.022, 1.2):.1f}" opacity="0.45"/>'
        f"</g>"
    )


def sal_blossom(x, y, r, fill=CREAM, opacity=1.0):
    """Sal flowers: small cream stars in loose panicles."""
    out = ""
    import math

    for k in range(5):
        a = k * 72
        out += (
            f'<ellipse cx="{x + math.cos(math.radians(a)) * r * 0.62:.1f}" '
            f'cy="{y + math.sin(math.radians(a)) * r * 0.62:.1f}" '
            f'rx="{r * 0.52:.1f}" ry="{r * 0.34:.1f}" '
            f'transform="rotate({a} {x:.1f} {y:.1f})" fill="{fill}" opacity="{opacity}"/>'
        )
    out += f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r * 0.26:.1f}" fill="{SUN}" opacity="{opacity}"/>'
    return out


def palash_flower(x, y, s=1.0, opacity=1.0):
    """Palash: the flame-of-the-forest claw, curved orange petals."""
    out = ""
    for i, (rot, sc) in enumerate([(-38, 1.0), (-12, 1.12), (14, 1.06), (40, 0.92)]):
        c = PALASH if i % 2 else PALASH_LT
        out += (
            f'<g transform="translate({x:.0f},{y:.0f}) rotate({rot}) scale({s * sc:.2f})">'
            f'<path d="M 0 0 C -16 -34 -13 -70 0 -92 C 13 -70 16 -34 0 0 Z" fill="{c}" '
            f'opacity="{opacity}"/></g>'
        )
    out += (
        f'<path d="M {x - 16:.0f} {y + 2:.0f} Q {x:.0f} {y + 20 * s:.0f} {x + 16:.0f} {y + 2:.0f} Z" '
        f'fill="{FOREST_DK}" opacity="{opacity}"/>'
    )
    return out


def sohrai_border(y, fill=BONE, opacity=0.85, comb=True):
    """The comb-and-dot band that edges a Sohrai wall painting."""
    out = f'<rect x="0" y="{y}" width="{W}" height="3" fill="{fill}" opacity="{opacity}"/>'
    step = 30
    for i in range(0, W + step, step):
        if comb:
            out += (
                f'<path d="M {i} {y + 3} L {i} {y + 26}" stroke="{fill}" '
                f'stroke-width="3" opacity="{opacity}"/>'
            )
        out += f'<circle cx="{i + step / 2:.0f}" cy="{y + 40}" r="4" fill="{fill}" opacity="{opacity * 0.8}"/>'
    return out


def sohrai_vine(x, y, w, h, fill=BONE, sw=4, opacity=0.9, leaves=7):
    """A climbing vine with paired leaves — the commonest Khovar motif."""
    d = f"M {x} {y} C {x + w * 0.5:.0f} {y - h * 0.28:.0f} {x + w * 0.5:.0f} {y - h * 0.72:.0f} {x} {y - h}"
    out = f'<path d="{d}" stroke="{fill}" stroke-width="{sw}" fill="none" opacity="{opacity}"/>'
    for i in range(1, leaves + 1):
        t = i / (leaves + 1)
        ly = y - h * t
        lx = x + w * 0.5 * (4 * t * (1 - t))
        for sgn in (-1, 1):
            out += (
                f'<ellipse cx="{lx + sgn * 26:.0f}" cy="{ly:.0f}" rx="22" ry="10" '
                f'transform="rotate({sgn * 26} {lx + sgn * 26:.0f} {ly:.0f})" '
                f'fill="none" stroke="{fill}" stroke-width="{sw - 1}" opacity="{opacity}"/>'
            )
    return out


def grain(n=1400, seed=7, opacity=0.05, fill=BONE):
    """A light speckle so large flats do not read as plastic."""
    import random

    r = random.Random(seed)
    out = ""
    for _ in range(n):
        x = r.uniform(0, W)
        y = r.uniform(0, H)
        rr = r.uniform(0.6, 2.1)
        out += f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{rr:.1f}" fill="{fill}" opacity="{opacity * r.uniform(0.4, 1.0):.3f}"/>'
    return out
