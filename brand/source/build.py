"""Map & Legend — brand assets, authored as vector.

The mark is a bookmark and a map pin at the same time: both silhouettes taper
to a point, so one shape carries both halves of the name. Knocked out of it
are three legend rows — the key on a map, and also lines of text.

Palette is cartographic and deliberately dark-first: ink ground, parchment
type, one ochre accent. Two reasons. It differentiates hard from the neon-cyan
look that dominates this niche, and it matches the reels, which are all dark.
"""
import math
import os
import random

import cairosvg

OUT = "out"
os.makedirs(OUT, exist_ok=True)

INK        = "#131E28"
INK_DEEP   = "#0A1219"
PARCHMENT  = "#F0E7D6"
PARCH_DIM  = "#C9BFAC"
OCHRE      = "#D69A3C"
TERRACOTTA = "#C0562F"
TEAL       = "#2C6B68"
SERIF      = "Spectral"


# ----------------------------------------------------------------- the mark
def mark(fg=PARCHMENT, accent=OCHRE, knock=INK, size=1000, simple=False):
    """Bookmark + map pin, with a legend knocked out of it.

    Drawn in a 1000x1000 box. `knock` must match whatever sits behind it.
    """
    body = ("M 268 214 Q 268 150 332 150 L 668 150 Q 732 150 732 214 "
            "L 732 592 L 500 872 L 268 592 Z")
    g = f'<g transform="scale({size/1000:.4f})">'
    g += f'<path d="{body}" fill="{fg}"/>'
    if simple:
        # Favicon variant: two chunky rows, no accent. Below ~48px the
        # three-row version fills in and the accent dot muddies.
        for y in (330, 470):
            g += f'<circle cx="372" cy="{y}" r="44" fill="{knock}"/>'
            g += (f'<rect x="452" y="{y-22}" width="212" height="44" rx="22" '
                  f'fill="{knock}"/>')
        return g + "</g>"
    # legend rows: a symbol, then a rule. Decreasing width gives the shape
    # its taper a second time. The top marker carries the accent, so the
    # colour sits inside the legend rather than floating in the point.
    rows = [(288, 248, 34, accent), (398, 208, 30, knock), (508, 164, 26, knock)]
    for y, barw, r, dot in rows:
        g += f'<circle cx="372" cy="{y}" r="{r}" fill="{dot}"/>'
        g += (f'<rect x="{372 + r + 38}" y="{y - r * 0.50:.0f}" width="{barw}" '
              f'height="{r:.0f}" rx="{r * 0.5:.0f}" fill="{knock}"/>')
    g += "</g>"
    return g


def svg(w, h, body, bg=None, defs=""):
    b = f'<rect width="{w}" height="{h}" fill="{bg}"/>' if bg else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}"><defs>{defs}</defs>{b}{body}</svg>')


def render(name, svg_text, w, h):
    path = f"{OUT}/{name}"
    open(path + ".svg", "w").write(svg_text)
    cairosvg.svg2png(bytestring=svg_text.encode(), write_to=path + ".png",
                     output_width=w, output_height=h)
    return path + ".png"


from PIL import ImageFont as _IF

_SERIF_TTF = os.path.expanduser("~/.fonts/Spectral-SemiBold.ttf")


def wordmark_width(size, tracking_em=0.16, text="MAP & LEGEND"):
    """Measured advance width, so a lockup can be sized to its box."""
    f = _IF.truetype(_SERIF_TTF, size)
    return f.getlength(text) + size * tracking_em * (len(text) - 1)


def fit_wordmark_size(max_width, tracking_em=0.16, start=200, floor=24):
    size = start
    while size > floor and wordmark_width(size, tracking_em) > max_width:
        size -= 2
    return size


def wordmark(x, y, size, fill=PARCHMENT, accent=OCHRE, anchor="middle",
             tracking_em=0.16):
    """MAP & LEGEND, letterspaced caps — an atlas title page, not a logotype.

    Written as three positioned runs rather than one string with tspans:
    the renderer collapses the whitespace either side of a tspan, which turns
    "MAP & LEGEND" into "MAPLE&GEND".
    """
    tr = size * tracking_em
    total = wordmark_width(size, tracking_em)
    left = x if anchor == "start" else (x - total / 2 if anchor == "middle" else x - total)
    f = _IF.truetype(_SERIF_TTF, size)

    def run(txt, x0, colour):
        return (f'<text x="{x0:.1f}" y="{y}" text-anchor="start" font-family="{SERIF}" '
                f'font-weight="600" font-size="{size}" fill="{colour}" '
                f'letter-spacing="{tr:.2f}">{txt}</text>')

    def adv(txt):
        return f.getlength(txt) + tr * len(txt)

    out = run("MAP", left, fill)
    x1 = left + adv("MAP ")
    out += run("&amp;", x1, accent)
    x2 = x1 + adv("& ")
    out += run("LEGEND", x2, fill)
    return out


def tagline(x, y, size, fill=PARCH_DIM, anchor="middle"):
    return (f'<text x="{x}" y="{y}" text-anchor="{anchor}" font-family="{SERIF}" '
            f'font-weight="400" font-size="{size}" fill="{fill}" '
            f'letter-spacing="{size*0.10:.1f}">Maps explain where. Legends explain why.</text>')


# ------------------------------------------------------------- backgrounds
def contours(w, h, colour, opacity=0.10, rings=16, seed=1, cx=None, cy=None):
    """Concentric survey contours — the channel's recurring texture."""
    r = random.Random(seed)
    cx = cx if cx is not None else w * 0.5
    cy = cy if cy is not None else h * 0.46
    out = ""
    for i in range(rings):
        rad = 90 + i * (max(w, h) / rings) * 0.62
        pts = []
        for k in range(24):
            a = k / 24 * math.tau
            wob = 1 + 0.10 * math.sin(a * 3 + i * 0.5 + seed) + 0.06 * math.cos(a * 5 - i * 0.3)
            pts.append((cx + math.cos(a) * rad * wob, cy + math.sin(a) * rad * wob * 0.92))
        d = f"M {pts[0][0]:.0f} {pts[0][1]:.0f} "
        for j in range(len(pts)):
            p0 = pts[j]
            p1 = pts[(j + 1) % len(pts)]
            mx, my = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
            d += f"Q {p0[0]:.0f} {p0[1]:.0f} {mx:.0f} {my:.0f} "
        d += "Z"
        op = opacity * (1 - i / (rings * 1.7))
        out += (f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="2.4" '
                f'opacity="{op:.3f}"/>')
    return out


def graticule(w, h, colour, opacity=0.06, step=150):
    out = ""
    for x in range(0, w + step, step):
        out += (f'<path d="M {x} 0 L {x} {h}" stroke="{colour}" stroke-width="1.4" '
                f'opacity="{opacity}"/>')
    for y in range(0, h + step, step):
        out += (f'<path d="M 0 {y} L {w} {y}" stroke="{colour}" stroke-width="1.4" '
                f'opacity="{opacity}"/>')
    return out


def grain(w, h, n=1200, seed=5, opacity=0.05, fill=PARCHMENT):
    r = random.Random(seed)
    out = ""
    for _ in range(n):
        out += (f'<circle cx="{r.uniform(0,w):.0f}" cy="{r.uniform(0,h):.0f}" '
                f'r="{r.uniform(0.6,2.0):.1f}" fill="{fill}" '
                f'opacity="{opacity*r.uniform(0.3,1.0):.3f}"/>')
    return out


def backdrop(w, h, base, deep, line, accent, seed=1, light=False):
    defs = (f'<linearGradient id="bg" x1="0" y1="0" x2="0.3" y2="1">'
            f'<stop offset="0" stop-color="{base}"/>'
            f'<stop offset="1" stop-color="{deep}"/></linearGradient>')
    b = f'<rect width="{w}" height="{h}" fill="url(#bg)"/>'
    b += graticule(w, h, line, 0.05 if not light else 0.09)
    b += contours(w, h, line, 0.13 if not light else 0.16, seed=seed, cx=w*0.63, cy=h*0.335)
    b += grain(w, h, int(w * h / 1800), seed=seed + 9, opacity=0.05,
               fill=PARCHMENT if not light else INK)
    b += (f'<circle cx="{w*0.63:.0f}" cy="{h*0.335:.0f}" r="{max(w,h)*0.016:.0f}" '
          f'fill="{accent}" opacity="0.30"/>')
    return svg(w, h, b, defs=defs)


# ==================================================================== build
made = []

# 1. the mark, on ink and on parchment
made.append(render("mark-on-ink", svg(1000, 1000, mark(), bg=INK), 1000, 1000))
made.append(render("mark-on-parchment",
                   svg(1000, 1000, mark(fg=INK, accent=TERRACOTTA, knock=PARCHMENT),
                       bg=PARCHMENT), 1000, 1000))
# transparent versions for overlaying
made.append(render("mark-parchment-transparent", svg(1000, 1000, mark(knock=INK)), 1000, 1000))

# small sizes, to prove it survives a phone
for px in (256, 128, 64, 32):
    t = svg(1000, 1000, mark(), bg=INK)
    cairosvg.svg2png(bytestring=t.encode(), write_to=f"{OUT}/mark-{px}.png",
                     output_width=px, output_height=px)
made.append(render("mark-simple-on-ink", svg(1000, 1000, mark(simple=True), bg=INK), 1000, 1000))
for px in (64, 48, 32, 16):
    t = svg(1000, 1000, mark(simple=True), bg=INK)
    cairosvg.svg2png(bytestring=t.encode(), write_to=f"{OUT}/favicon-{px}.png",
                     output_width=px, output_height=px)

# 2. wordmark
w, h = 1800, 420
b = wordmark(w / 2, 250, fit_wordmark_size(w - 160))
made.append(render("wordmark-on-ink", svg(w, h, b, bg=INK), w, h))
made.append(render("wordmark-on-parchment",
                   svg(w, h, wordmark(w/2, 250, fit_wordmark_size(w - 160), fill=INK, accent=TERRACOTTA),
                       bg=PARCHMENT), w, h))

# 3. horizontal lockup
w, h = 2200, 620
b = f'<g transform="translate(300,60)">{mark(size=500)}</g>'
_ws = fit_wordmark_size(2200 - 1000 - 120)
b += wordmark(1000, 330, _ws, anchor="start")
b += (f'<text x="1000" y="424" font-family="{SERIF}" font-size="46" fill="{PARCH_DIM}" '
      f'letter-spacing="3">Maps explain where. Legends explain why.</text>')
made.append(render("lockup-horizontal-on-ink", svg(w, h, b, bg=INK), w, h))

# 4. stacked lockup
w, h = 1400, 1230
b = f'<g transform="translate(450,180)">{mark(size=500)}</g>'
b += wordmark(w / 2, 960, fit_wordmark_size(w - 200))
b += (f'<rect x="{w/2-160:.0f}" y="1010" width="320" height="4" fill="{OCHRE}"/>')
b += tagline(w / 2, 1100, 44)
made.append(render("lockup-stacked-on-ink", svg(w, h, b, bg=INK), w, h))

# 5. avatar — 1:1, mark only, generous padding so it survives the circle crop
S = 1080
b = graticule(S, S, PARCHMENT, 0.05, 135) + contours(S, S, PARCHMENT, 0.10, 10, 3)
b += f'<g transform="translate({S*0.185:.0f},{S*0.155:.0f})">{mark(size=S*0.63)}</g>'
made.append(render("avatar-1080", svg(S, S, b, bg=INK), S, S))

# 6. YouTube banner — 2560x1440, everything vital inside the 1546x423 safe area
W, Hh = 2560, 1440
sx, sy = (W - 1546) / 2, (Hh - 423) / 2
defs = (f'<linearGradient id="bg" x1="0" y1="0" x2="0.4" y2="1">'
        f'<stop offset="0" stop-color="#18242F"/><stop offset="1" stop-color="{INK_DEEP}"/>'
        f'</linearGradient>')
b = f'<rect width="{W}" height="{Hh}" fill="url(#bg)"/>'
b += graticule(W, Hh, PARCHMENT, 0.05, 160)
b += contours(W, Hh, PARCHMENT, 0.12, 18, 7, cx=W * 0.5, cy=Hh * 0.5)
b += grain(W, Hh, 2200, 11, 0.045)
b += f'<g transform="translate({sx+30:.0f},{sy+48:.0f})">{mark(size=330)}</g>'
b += wordmark(sx + 410, sy + 226, fit_wordmark_size(1546 - 410 - 30), anchor="start")
b += (f'<text x="{sx+414:.0f}" y="{sy+306:.0f}" font-family="{SERIF}" font-size="42" '
      f'fill="{PARCH_DIM}" letter-spacing="3">Maps explain where. Legends explain why.</text>')
made.append(render("youtube-banner-2560x1440", svg(W, Hh, b, defs=defs), W, Hh))

# 7. reel backdrops, 1080x1920, four colourways
schemes = [
    ("backdrop-ink",        INK,        INK_DEEP,   PARCHMENT, OCHRE,      False),
    ("backdrop-teal",       "#1C4746",  "#0E2726",  PARCHMENT, OCHRE,      False),
    ("backdrop-terracotta", "#6E2E1B",  "#3A160C",  PARCHMENT, OCHRE,      False),
    ("backdrop-parchment",  "#EFE5D2",  "#DCCFB6",  INK,       TERRACOTTA, True),
]
for i, (nm, base, deep, line, acc, light) in enumerate(schemes):
    made.append(render(nm, backdrop(1080, 1920, base, deep, line, acc, seed=i + 2,
                                    light=light), 1080, 1920))

for p in made:
    print(f"  {os.path.getsize(p)/1024:7.0f} KB  {p}")
print(f"\n  plus mark-32/64/128/256.png")
