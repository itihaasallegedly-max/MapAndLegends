"""The six scene illustrations."""
import math
import random

from artkit import *  # noqa: F403


# ---------------------------------------------------------------- scene 1
def dawn_forest():
    defs = vgrad("sky", [("0", "#0d1b26"), ("0.40", "#26404f"), ("0.70", "#8f553b"), ("1", "#dd9142")])
    defs += rgrad("glow", [("0", "#ffdf9e"), ("0.4", "#f5b45f80"), ("1", "#e0913f00")])

    b = f'<rect width="{W}" height="{H}" fill="url(#sky)"/>'
    b += f'<circle cx="{W*0.5:.0f}" cy="{H*0.60:.0f}" r="520" fill="url(#glow)"/>'
    b += f'<circle cx="{W*0.5:.0f}" cy="{H*0.615:.0f}" r="118" fill="{SUN}" opacity="0.95"/>'

    # far ridges, cooling into the haze
    b += ridge(H * 0.585, 34, "#5d5566", seed=1.0, opacity=0.55)
    b += ridge(H * 0.635, 40, "#4a4657", seed=2.2, opacity=0.7)
    b += ridge(H * 0.695, 46, "#33384a", seed=3.4, opacity=0.85)

    # mist bands over the valley
    for i, (yy, op) in enumerate([(H * 0.615, 0.20), (H * 0.655, 0.16), (H * 0.700, 0.12)]):
        b += (f'<ellipse cx="{W*0.5:.0f}" cy="{yy:.0f}" rx="{W*0.75:.0f}" ry="{20 + i*6}" '
              f'fill="{BONE}" opacity="{op}"/>')

    b += ridge(H * 0.770, 30, FOREST_DK, seed=4.1)

    # near treeline: sal in silhouette
    r = random.Random(11)
    for x, sc in [(70, 0.78), (215, 0.95), (360, 0.7), (520, 1.05), (690, 0.82),
                  (845, 1.0), (1000, 0.74)]:
        b += sal_tree(x, H * 0.80 + r.uniform(0, 26), sc, NIGHT, 0.97)
    b += ridge(H * 0.895, 18, "#0b161d", seed=5.6)
    for x, sc in [(-20, 1.15), (200, 0.9), (430, 1.2), (760, 1.0), (990, 1.1)]:
        b += sal_tree(x, H * 0.985, sc, "#081218")

    b += grain(900, seed=3, opacity=0.045)
    return svg(b, bg=NIGHT, defs=defs)


# ---------------------------------------------------------------- scene 2
def sal_branch():
    defs = vgrad("bg", [("0", "#123027"), ("1", "#0c1f1b")])
    b = f'<rect width="{W}" height="{H}" fill="url(#bg)"/>'

    # faint Sohrai vines in the background, very low contrast
    b += sohrai_vine(150, H * 0.92, 200, 700, BONE, 4, 0.07)
    b += sohrai_vine(900, H * 0.88, -190, 620, BONE, 4, 0.06)

    # the branch: a slow diagonal across the frame
    d = "M -40 1560 C 240 1440 380 1180 520 980 C 660 780 820 560 1140 400"
    b += f'<path d="{d}" stroke="{EARTH_DK}" stroke-width="26" fill="none" stroke-linecap="round"/>'
    b += f'<path d="{d}" stroke="{EARTH}" stroke-width="16" fill="none" stroke-linecap="round" opacity="0.85"/>'

    # sample points along the branch for leaves and flowers
    def pt(t):
        p0, p1, p2, p3 = (-40, 1560), (240, 1440), (380, 1180), (520, 980)
        q0, q1, q2, q3 = (520, 980), (660, 780), (820, 560), (1140, 400)
        if t <= 0.5:
            u = t / 0.5
            a, bb, c, dd = p0, p1, p2, p3
        else:
            u = (t - 0.5) / 0.5
            a, bb, c, dd = q0, q1, q2, q3
        mu = 1 - u
        x = mu**3 * a[0] + 3 * mu**2 * u * bb[0] + 3 * mu * u**2 * c[0] + u**3 * dd[0]
        y = mu**3 * a[1] + 3 * mu**2 * u * bb[1] + 3 * mu * u**2 * c[1] + u**3 * dd[1]
        return x, y

    r = random.Random(5)
    for i in range(16):
        t = 0.06 + i * 0.058
        x, y = pt(t)
        for sgn in (-1, 1):
            ang = sgn * r.uniform(38, 78) + 18
            ln = r.uniform(120, 178)
            shade = SAL if (i + (sgn > 0)) % 2 else SAL_LT
            b += sal_leaf(x, y, ln, ang, shade, 0.96)

    # blossom panicles at the branch tips
    for t, n in [(0.30, 6), (0.58, 7), (0.82, 8), (0.95, 6)]:
        x, y = pt(t)
        for k in range(n):
            b += sal_blossom(
                x + r.uniform(-90, 90), y + r.uniform(-80, 80),
                r.uniform(13, 21), CREAM, 0.95,
            )
    # a few fallen flowers drifting
    for _ in range(9):
        b += sal_blossom(r.uniform(60, 1020), r.uniform(1300, 1860), r.uniform(8, 13), CREAM, 0.5)

    b += grain(700, seed=9, opacity=0.05)
    return svg(b, bg=FOREST_DK, defs=defs)


# ---------------------------------------------------------------- scene 3
def sarhul_grove():
    defs = vgrad("sky", [("0", "#1d3346"), ("0.55", "#5a6360"), ("1", "#cf8f5c")])
    b = f'<rect width="{W}" height="{H}" fill="url(#sky)"/>'
    b += f'<circle cx="{W*0.60:.0f}" cy="{H*0.40:.0f}" r="330" fill="{SUN}" opacity="0.13"/>'
    b += f'<circle cx="{W*0.60:.0f}" cy="{H*0.40:.0f}" r="165" fill="{SUN}" opacity="0.18"/>'

    # high canopy closing over the grove
    b += (f'<path d="M -40 0 L {W+40} 0 L {W+40} 300 '
          f'C {W*0.78:.0f} 200 {W*0.60:.0f} 360 {W*0.44:.0f} 268 '
          f'C {W*0.28:.0f} 190 {W*0.12:.0f} 330 -40 250 Z" fill="{FOREST_DK}"/>')
    r = random.Random(3)
    for _ in range(46):
        x, y = r.uniform(0, W), r.uniform(150, 430)
        b += sal_leaf(x, y, r.uniform(74, 128), r.uniform(-150, -30), FOREST, 0.85)

    # shafts of first light, angled and soft
    for x, wd, op in [(210, 70, 0.10), (450, 96, 0.13), (700, 74, 0.11), (900, 54, 0.08)]:
        b += (f'<path d="M {x} 300 L {x+wd} 300 L {x+wd*2.3:.0f} {H*0.70:.0f} '
              f'L {x-wd*1.1:.0f} {H*0.84:.0f} Z" fill="{SUN}" opacity="{op}"/>')

    b += ridge(H * 0.60, 20, "#26443a", seed=2.0, opacity=0.95)

    # sal boles: thick, pale grey-brown bark, gently tapered
    for x, wdt, col, sh in [(70, 74, "#5b4a3b", "#7a6350"), (256, 96, "#67543f", "#8a7157"),
                            (486, 118, "#6f5943", "#93795d"), (742, 88, "#5f4d3c", "#816a53"),
                            (960, 104, "#573f31", "#75594a")]:
        top = r.uniform(220, 330)
        b += (f'<path d="M {x-wdt/2:.0f} {H*0.715:.0f} L {x-wdt*0.36:.0f} {top:.0f} '
              f'L {x+wdt*0.36:.0f} {top:.0f} L {x+wdt/2:.0f} {H*0.715:.0f} Z" fill="{col}"/>')
        b += (f'<path d="M {x+wdt*0.06:.0f} {H*0.715:.0f} L {x+wdt*0.16:.0f} {top:.0f} '
              f'L {x+wdt*0.36:.0f} {top:.0f} L {x+wdt/2:.0f} {H*0.715:.0f} Z" '
              f'fill="{sh}" opacity="0.55"/>')
        for k in range(9):  # bark fissures
            yy = top + (H*0.715 - top) * (k + 0.5) / 9
            b += (f'<path d="M {x-wdt*0.26:.0f} {yy:.0f} l {r.uniform(6,20):.0f} {r.uniform(30,64):.0f}" '
                  f'stroke="#3f3227" stroke-width="3" opacity="0.5" fill="none"/>')

    # forest floor
    b += f'<path d="M 0 {H*0.712:.0f} L {W} {H*0.688:.0f} L {W} {H} L 0 {H} Z" fill="#2b1d14"/>'
    b += f'<path d="M 0 {H*0.752:.0f} L {W} {H*0.732:.0f} L {W} {H} L 0 {H} Z" fill="#3d2a1a"/>'

    # the sarna stone, vermilion-banded, flowers heaped at its foot
    sx, sy = W * 0.27, H * 0.745
    b += (f'<ellipse cx="{sx:.0f}" cy="{sy+6:.0f}" rx="120" ry="26" fill="#1d130c" opacity="0.55"/>')
    b += (f'<path d="M {sx-56:.0f} {sy:.0f} L {sx-42:.0f} {sy-208:.0f} Q {sx:.0f} {sy-256:.0f} '
          f'{sx+42:.0f} {sy-208:.0f} L {sx+56:.0f} {sy:.0f} Z" fill="#6b6058"/>')
    b += (f'<path d="M {sx+10:.0f} {sy:.0f} L {sx+18:.0f} {sy-204:.0f} Q {sx+34:.0f} {sy-228:.0f} '
          f'{sx+42:.0f} {sy-208:.0f} L {sx+56:.0f} {sy:.0f} Z" fill="{BONE}" opacity="0.16"/>')
    for yy, cc in [(sy-150, PALASH), (sy-126, SUN)]:
        b += (f'<path d="M {sx-49:.0f} {yy:.0f} Q {sx:.0f} {yy+22:.0f} {sx+49:.0f} {yy:.0f}" '
              f'stroke="{cc}" stroke-width="12" fill="none"/>')
    rr = random.Random(21)
    for _ in range(30):
        b += sal_blossom(sx + rr.uniform(-124, 124), sy + rr.uniform(-16, 30),
                         rr.uniform(9, 17), CREAM, 0.95)

    # the Pahan: shoulders, dhoti, an arm cradling the offering pot
    px, py = W * 0.73, H * 0.765
    b += f'<ellipse cx="{px:.0f}" cy="{py+4:.0f}" rx="72" ry="18" fill="#1d130c" opacity="0.5"/>'
    b += (f'<path d="M {px-46:.0f} {py:.0f} C {px-40:.0f} {py-74:.0f} {px-34:.0f} {py-118:.0f} '
          f'{px-34:.0f} {py-150:.0f} L {px+34:.0f} {py-150:.0f} C {px+34:.0f} {py-118:.0f} '
          f'{px+40:.0f} {py-74:.0f} {px+46:.0f} {py:.0f} Z" fill="#241a12"/>')   # dhoti
    b += (f'<path d="M {px-34:.0f} {py-150:.0f} C {px-40:.0f} {py-206:.0f} {px-30:.0f} {py-252:.0f} '
          f'{px-16:.0f} {py-262:.0f} L {px+16:.0f} {py-262:.0f} C {px+30:.0f} {py-252:.0f} '
          f'{px+40:.0f} {py-206:.0f} {px+34:.0f} {py-150:.0f} Z" fill="{NIGHT}"/>')  # torso
    b += f'<circle cx="{px:.0f}" cy="{py-296:.0f}" r="34" fill="{NIGHT}"/>'
    b += (f'<path d="M {px-30:.0f} {py-318:.0f} q 30 -22 60 0 l 4 10 l -68 0 Z" fill="{BONE}" '
          f'opacity="0.85"/>')  # head cloth
    b += (f'<path d="M {px-22:.0f} {py-248:.0f} C {px-64:.0f} {py-226:.0f} {px-86:.0f} {py-196:.0f} '
          f'{px-88:.0f} {py-172:.0f}" stroke="{NIGHT}" stroke-width="20" fill="none" '
          f'stroke-linecap="round"/>')  # arm
    b += (f'<path d="M {px-126:.0f} {py-170:.0f} a 40 36 0 0 0 80 0 Z" fill="#5d3a22"/>'
          f'<ellipse cx="{px-86:.0f}" cy="{py-170:.0f}" rx="40" ry="12" fill="#6f4728"/>')
    for k in range(7):
        b += sal_blossom(px - 116 + k * 10, py - 178 - (k % 3) * 7, 9, CREAM, 0.95)

    b += grain(800, seed=15, opacity=0.05)
    return svg(b, bg=INDIGO, defs=defs)


# ---------------------------------------------------------------- scene 4
def elephant_silhouette(cx, cy, s=1.0, col=NIGHT, shade="#050b0f"):
    """Asian elephant, side on, facing right. Domed head, small ear, humped back."""
    g = f'<g transform="translate({cx:.0f},{cy:.0f}) scale({s:.3f}) translate(-310,-400)">'
    # far legs first, a shade darker
    for x, w in [(360, 52), (110, 56)]:
        g += (f'<path d="M {x} 250 L {x-6} 392 q {w/2:.0f} 16 {w} 0 L {x+w-4} 250 Z" fill="{shade}"/>')
    # rump, barrel and shoulder as overlapping masses
    g += f'<ellipse cx="150" cy="228" rx="126" ry="118" fill="{col}"/>'
    g += f'<ellipse cx="278" cy="234" rx="180" ry="112" fill="{col}"/>'
    g += f'<ellipse cx="392" cy="196" rx="112" ry="92" fill="{col}"/>'
    # tail
    g += (f'<path d="M 34 190 C 8 236 6 300 22 340 l 16 -4 C 26 300 30 244 50 200 Z" fill="{col}"/>'
          f'<path d="M 20 332 q 12 34 30 30 q -14 -12 -14 -34 Z" fill="{col}"/>')
    # head: one mass plus a single domed forehead, not a cluster of circles
    g += f'<ellipse cx="470" cy="180" rx="98" ry="92" fill="{col}"/>'
    g += f'<ellipse cx="470" cy="112" rx="80" ry="48" fill="{col}"/>'
    # trunk: thick at the base, tapering to the tip. Drawn before the ear so the
    # ear reads as the nearer plane.
    g += ('<path d="M 534 236 C 562 296 560 352 544 390 '
          'C 538 408 508 408 504 388 C 514 348 510 292 452 244 Z" fill="' + col + '"/>')
    # tusk, clear of the trunk's front edge
    g += ('<path d="M 528 258 C 574 282 600 312 606 344" stroke="' + BONE +
          '" stroke-width="17" fill="none" stroke-linecap="round" opacity="0.96"/>')
    # ear: the smaller, rounded Asian ear
    g += (f'<path d="M 432 108 C 360 104 336 178 358 234 C 378 284 440 278 458 236 Z" fill="{shade}"/>')
    g += (f'<path d="M 432 108 C 360 104 336 178 358 234" stroke="{col}" stroke-width="5" '
          f'fill="none" opacity="0.9"/>')
    g += f'<circle cx="502" cy="158" r="9" fill="{shade}"/>'  # eye
    # near legs
    for x, w in [(406, 60), (150, 64)]:
        g += (f'<path d="M {x} 250 L {x-8} 396 q {w/2:.0f} 18 {w} 0 L {x+w-6} 250 Z" fill="{col}"/>')
    g += "</g>"
    return g


def koel(x, y, s=1.0, col=NIGHT):
    """Male Asian koel: glossy black, long graduated tail, red eye, pale bill."""
    g = f'<g transform="translate({x:.0f},{y:.0f}) scale({s:.3f})">'
    g += ('<path d="M 0 0 C 26 -34 78 -46 116 -30 C 150 -16 162 14 150 40 '
          'C 138 66 92 76 56 66 C 20 56 -14 30 0 0 Z" fill="' + col + '"/>')
    g += f'<ellipse cx="140" cy="-18" rx="40" ry="35" fill="{col}"/>'          # head
    g += f'<path d="M 174 -22 L 232 -8 L 172 6 Z" fill="#c9d6a8"/>'            # bill
    g += f'<circle cx="150" cy="-26" r="8" fill="#c62828"/>'                   # red eye
    g += f'<circle cx="150" cy="-26" r="3" fill="#2b0000"/>'
    g += ('<path d="M 34 56 C -40 92 -104 128 -156 168 C -120 172 -60 150 -6 116 '
          'C 22 98 42 80 48 66 Z" fill="' + col + '"/>')                        # long tail
    g += (f'<path d="M 74 70 l -4 30 M 106 68 l -2 30" stroke="{col}" stroke-width="7" '
          f'stroke-linecap="round"/>')
    g += "</g>"
    return g


def emblems():
    defs = vgrad("bg", [("0", "#14302a"), ("0.5", "#1d3c30"), ("1", "#95592f")])
    b = f'<rect width="{W}" height="{H}" fill="url(#bg)"/>'
    b += f'<circle cx="{W*0.5:.0f}" cy="{H*0.47:.0f}" r="430" fill="{SUN}" opacity="0.08"/>'
    b += ridge(H * 0.58, 30, "#1b3a30", seed=1.4, opacity=0.9)
    b += ridge(H * 0.685, 26, "#16312a", seed=3.1)

    # koel on a bare branch, top left
    b += ('<path d="M -30 452 C 190 430 330 476 470 540" stroke="' + EARTH_DK +
          '" stroke-width="15" fill="none" stroke-linecap="round"/>')
    b += ('<path d="M 168 444 l 46 -56 M 262 446 l 34 -66" stroke="' + EARTH_DK +
          '" stroke-width="8" fill="none" stroke-linecap="round"/>')
    b += koel(196, 436, 0.95)

    # the elephant carries the frame
    b += elephant_silhouette(W * 0.50, H * 0.615, 1.34)

    # palash along the foreground bank
    b += f'<path d="M 0 {H*0.828:.0f} L {W} {H*0.80:.0f} L {W} {H} L 0 {H} Z" fill="#603b20"/>'
    b += f'<path d="M 0 {H*0.862:.0f} L {W} {H*0.842:.0f} L {W} {H} L 0 {H} Z" fill="#4e2f19"/>'
    r = random.Random(31)
    for i in range(12):
        x = 20 + i * 95 + r.uniform(-20, 20)
        y = H * 0.885 + r.uniform(-40, 30)
        b += (f'<path d="M {x:.0f} {H} C {x + r.uniform(-20,20):.0f} {(y+H)/2:.0f} '
              f'{x + r.uniform(-16,16):.0f} {y+40:.0f} {x:.0f} {y:.0f}" '
              f'stroke="{EARTH_DK}" stroke-width="9" fill="none"/>')
        b += palash_flower(x, y, r.uniform(0.78, 1.14))

    b += grain(700, seed=23, opacity=0.045)
    return svg(b, bg=FOREST_DK, defs=defs)


# ---------------------------------------------------------------- scene 5
def rugra(x, y, rad, r):
    """A rugra: a pale puffball shouldering up out of wet earth."""
    out = (f'<ellipse cx="{x}" cy="{y+rad*0.30:.0f}" rx="{rad*1.34:.0f}" ry="{rad*0.40:.0f}" '
           f'fill="#1c1309" opacity="0.6"/>')
    # cracked soil ring
    for k in range(7):
        a = math.radians(k * 51 + r.uniform(-12, 12))
        out += (f'<path d="M {x+math.cos(a)*rad*1.05:.0f} {y+rad*0.28+math.sin(a)*rad*0.32:.0f} '
                f'l {math.cos(a)*rad*0.42:.0f} {math.sin(a)*rad*0.16:.0f}" stroke="#5a4028" '
                f'stroke-width="4" opacity="0.8"/>')
    out += (f'<path d="M {x-rad} {y+rad*0.30:.0f} A {rad} {rad*1.04:.0f} 0 0 1 {x+rad} '
            f'{y+rad*0.30:.0f} Z" fill="{BONE_DIM}"/>')
    out += (f'<path d="M {x-rad*0.30:.0f} {y-rad*0.62:.0f} A {rad*0.86:.0f} {rad*0.86:.0f} 0 0 1 '
            f'{x+rad*0.62:.0f} {y-rad*0.18:.0f}" stroke="{CREAM}" stroke-width="{rad*0.20:.0f}" '
            f'fill="none" opacity="0.7" stroke-linecap="round"/>')
    return out


def monsoon_kitchen():
    defs = vgrad("sky", [("0", "#182a30"), ("0.5", "#22403e"), ("1", "#3f3327")])
    b = f'<rect width="{W}" height="{H}" fill="url(#sky)"/>'

    b += ridge(H * 0.22, 40, "#15302b", seed=1.1, opacity=0.92)
    r = random.Random(41)
    for x, sc in [(70, 0.6), (270, 0.7), (500, 0.58), (730, 0.72), (960, 0.62)]:
        b += sal_tree(x, H * 0.285, sc, "#122824", 0.6)

    for _ in range(340):
        x = r.uniform(-60, W + 60); y = r.uniform(0, H * 0.94); ln = r.uniform(26, 84)
        b += (f'<path d="M {x:.0f} {y:.0f} L {x-14:.0f} {y+ln:.0f}" stroke="{BONE}" '
              f'stroke-width="{r.uniform(1.1,2.5):.1f}" opacity="{r.uniform(0.10,0.32):.2f}"/>')

    b += f'<path d="M 0 {H*0.475:.0f} L {W} {H*0.45:.0f} L {W} {H} L 0 {H} Z" fill="#2d2116"/>'
    b += f'<path d="M 0 {H*0.515:.0f} L {W} {H*0.495:.0f} L {W} {H} L 0 {H} Z" fill="#3e2d1c"/>'
    for _ in range(18):
        x, y = r.uniform(0, W), r.uniform(H * 0.51, H * 0.59)
        b += (f'<ellipse cx="{x:.0f}" cy="{y:.0f}" rx="{r.uniform(30,86):.0f}" '
              f'ry="{r.uniform(5,12):.0f}" fill="{DUSK}" opacity="{r.uniform(0.20,0.38):.2f}"/>')

    # rugra breaking the ground, left and centre
    for x, y, rad in [(190, 1058, 80), (352, 1110, 58), (88, 1132, 50),
                      (296, 1000, 40), (448, 1040, 33)]:
        b += rugra(x, y, rad, r)

    # a sal-leaf plate of dhuska, lower right
    px, py = 742, 1236
    b += f'<ellipse cx="{px}" cy="{py+40}" rx="268" ry="72" fill="#1e160e" opacity="0.55"/>'
    b += (f'<ellipse cx="{px}" cy="{py}" rx="262" ry="124" fill="{SAL}"/>'
          f'<ellipse cx="{px}" cy="{py}" rx="262" ry="124" fill="none" stroke="{FOREST_DK}" '
          f'stroke-width="8"/>')
    for k in range(9):
        a = math.radians(-160 + k * 40)
        b += (f'<path d="M {px + math.cos(a)*250:.0f} {py + math.sin(a)*116:.0f} '
              f'l {math.cos(a)*18:.0f} {math.sin(a)*18:.0f}" stroke="{FOREST_DK}" stroke-width="6"/>')
    for dx, dy, rad in [(-112, -18, 82), (22, -40, 78), (128, 6, 72), (-26, 40, 68)]:
        b += (f'<ellipse cx="{px+dx}" cy="{py+dy}" rx="{rad}" ry="{rad*0.62:.0f}" fill="{EARTH}"/>'
              f'<ellipse cx="{px+dx}" cy="{py+dy-5}" rx="{rad*0.90:.0f}" ry="{rad*0.54:.0f}" fill="{OCHRE}"/>'
              f'<ellipse cx="{px+dx}" cy="{py+dy-9}" rx="{rad*0.70:.0f}" ry="{rad*0.40:.0f}" fill="{OCHRE_LT}"/>'
              f'<ellipse cx="{px+dx-rad*0.24:.0f}" cy="{py+dy-rad*0.24:.0f}" rx="{rad*0.28:.0f}" '
              f'ry="{rad*0.15:.0f}" fill="{SUN}" opacity="0.6"/>')
    b += (f'<path d="M {px+232} {py+34} a 78 78 0 0 0 156 0 Z" fill="#4c3524"/>'
          f'<ellipse cx="{px+310}" cy="{py+34}" rx="78" ry="22" fill="#31612e"/>')

    b += grain(600, seed=33, opacity=0.05)
    return svg(b, bg="#182a30", defs=defs)


# ---------------------------------------------------------------- scene 6
def sohrai_wall():
    defs = vgrad("sky", [("0", "#243a52"), ("0.6", "#7a5a5e"), ("1", "#c07a4e")])
    defs += vgrad("wall", [("0", "#a35a2e"), ("0.5", "#94502a"), ("1", "#7b4222")])
    b = f'<rect width="{W}" height="{H}" fill="url(#sky)"/>'
    b += f'<circle cx="{W*0.78:.0f}" cy="{H*0.16:.0f}" r="86" fill="{SUN}" opacity="0.5"/>'
    b += ridge(H * 0.235, 22, "#3b3a49", seed=2.6, opacity=0.8)

    # roof line
    b += (f'<path d="M -30 {H*0.285:.0f} L {W*0.5:.0f} {H*0.205:.0f} L {W+30} {H*0.285:.0f} '
          f'L {W+30} {H*0.325:.0f} L -30 {H*0.325:.0f} Z" fill="#3a2a1c"/>')

    # the wall
    wy = H * 0.315
    b += f'<rect x="0" y="{wy:.0f}" width="{W}" height="{H-wy:.0f}" fill="url(#wall)"/>'
    r = random.Random(51)
    for _ in range(120):  # mud-plaster mottling
        x, y = r.uniform(0, W), r.uniform(wy, H)
        b += (f'<ellipse cx="{x:.0f}" cy="{y:.0f}" rx="{r.uniform(30,120):.0f}" '
              f'ry="{r.uniform(14,50):.0f}" fill="{EARTH_DK}" opacity="{r.uniform(0.05,0.13):.2f}"/>')

    b += sohrai_border(int(wy + 44))
    b += sohrai_border(int(H * 0.745), comb=False)

    # the mural: a horned bull, a deer, vines and dot rows — Sohrai vocabulary
    def bull(x, y, s=1.0, col=BONE):
        g = (f'<g transform="translate({x},{y}) scale({s})" fill="none" stroke="{col}" '
             f'stroke-width="6" stroke-linejoin="round">')
        g += ('<path d="M -150 40 C -150 -30 -110 -62 -40 -62 L 70 -62 C 130 -62 160 -30 160 30 '
              'L 160 60 L 122 60 L 122 150 L 96 150 L 96 60 L 22 60 L 22 150 L -4 150 L -4 60 '
              'L -84 60 L -84 150 L -110 150 L -110 60 L -150 60 Z"/>')
        g += '<path d="M 160 20 C 206 4 236 -18 244 -52"/>'
        g += '<ellipse cx="188" cy="-92" rx="46" ry="38"/>'
        g += '<path d="M 152 -120 C 128 -160 150 -186 182 -178"/>'
        g += '<path d="M 224 -120 C 248 -160 226 -186 194 -178"/>'
        g += '<circle cx="176" cy="-96" r="5" fill="' + col + '"/>'
        g += '<path d="M -150 20 C -196 6 -212 -26 -206 -60"/>'
        for k in range(5):
            g += f'<circle cx="{-110 + k*54}" cy="-10" r="9" fill="{col}"/>'
        g += "</g>"
        return g

    def deer(x, y, s=1.0, col=BONE):
        g = (f'<g transform="translate({x},{y}) scale({s})" fill="none" stroke="{col}" '
             f'stroke-width="5" stroke-linejoin="round">')
        g += ('<path d="M -96 20 C -96 -26 -66 -46 -18 -46 L 52 -46 C 92 -46 112 -22 112 14 '
              'L 112 34 L 88 34 L 88 104 L 70 104 L 70 34 L 12 34 L 12 104 L -6 104 L -6 34 '
              'L -62 34 L -62 104 L -80 104 L -80 34 L -96 34 Z"/>')
        g += '<path d="M 112 6 C 146 -6 166 -24 172 -50"/>'
        g += '<ellipse cx="140" cy="-76" rx="32" ry="26"/>'
        g += ('<path d="M 118 -98 C 106 -140 122 -166 140 -158 M 118 -98 C 92 -118 88 -140 100 -148 '
              'M 160 -98 C 172 -140 156 -166 138 -158 M 160 -98 C 186 -118 190 -140 178 -148"/>')
        g += f'<circle cx="130" cy="-80" r="4" fill="{col}"/>'
        for k in range(4):
            g += f'<circle cx="{-60 + k*44}" cy="-6" r="7" fill="{col}"/>'
        g += "</g>"
        return g

    b += bull(W * 0.46, H * 0.52, 1.06)
    b += deer(W * 0.26, H * 0.695, 0.88)
    b += sohrai_vine(118, H * 0.700, 120, 290, BONE, 5, 0.9, 5)
    b += sohrai_vine(962, H * 0.706, -130, 320, BONE, 5, 0.9, 6)
    b += sohrai_vine(872, H * 0.560, 90, 175, BONE, 4, 0.7, 4)

    # sun/rosette motif, a Sohrai staple
    cx, cy = W * 0.80, H * 0.405
    b += f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="52" fill="none" stroke="{BONE}" stroke-width="6"/>'
    b += f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="22" fill="{BONE}"/>'
    for k in range(12):
        a = math.radians(k * 30)
        b += (f'<path d="M {cx+math.cos(a)*58:.0f} {cy+math.sin(a)*58:.0f} '
              f'L {cx+math.cos(a)*84:.0f} {cy+math.sin(a)*84:.0f}" stroke="{BONE}" stroke-width="6"/>')

    b += grain(900, seed=63, opacity=0.06, fill=CREAM)
    return svg(b, bg=EARTH, defs=defs)


SCENES = {
    "dawn_forest": dawn_forest,
    "sal_branch": sal_branch,
    "sarhul_grove": sarhul_grove,
    "emblems": emblems,
    "monsoon_kitchen": monsoon_kitchen,
    "sohrai_wall": sohrai_wall,
}
