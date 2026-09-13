# Map & Legend — brand kit

Authored as vector, so every mark is resolution-independent and re-colourable.
No image model was involved; nothing here is a raster the size it happens to be.

## The mark

A bookmark and a map pin share a silhouette — both taper to a point. That one
shape carries both halves of the name. Knocked out of it are three legend
rows: the key on a map, and also lines of text. The top marker is the accent,
so the colour lives inside the legend instead of floating loose.

The rows shorten as they descend, which repeats the taper and keeps the shape
readable when it is very small.

Two versions:

- **`mark-on-ink`** — the primary, three rows. Use at 48px and above.
- **`mark-simple-on-ink`** — two chunky rows, no accent. Below 48px the
  three-row version fills in. Use for favicons and app icons.

## Palette

| Role | Hex | Notes |
|---|---|---|
| Ink | `#131E28` | Primary ground |
| Ink deep | `#0A1219` | Gradient foot, vignettes |
| Parchment | `#F0E7D6` | The mark, headings, body |
| Parchment dim | `#C9BFAC` | Taglines, secondary text |
| Ochre | `#D69A3C` | The accent. The ampersand, the rule, the summit dot |
| Terracotta | `#C0562F` | The accent on light grounds only |
| Teal | `#2C6B68` | Secondary backdrop colourway |

**Dark-first on purpose.** This niche is saturated with neon cyan on black —
your reference account included. A survey-map palette of ink, parchment and
ochre reads as the more considered account in a feed, and it already matches
your reels, which are all dark.

## Type

**Spectral SemiBold**, caps, letterspaced at 0.16em. It has the weight of an
atlas title page rather than a logotype, which is the right register for a
channel about places and their stories. Included in `source/` (SIL Open Font
License, free to redistribute and embed).

The `.svg` files reference the font by name. Install `Spectral-SemiBold.ttf`
before opening them, or use the `.png` exports, which have the type baked in.

## Files

**`logo/`** — mark, wordmark, and both lockups, each on ink and on parchment.
`mark-parchment-transparent.png` has an alpha channel for overlaying on your
own artwork. `mark-{256,128,64,32}.png` and `favicon-{64,48,32,16}.png` are
pre-sized.

**`social/`**
- `avatar-1080.png` — profile picture for YouTube and Instagram. The mark is
  inset generously so it survives the circular crop.
- `youtube-banner-2560x1440.png` — channel art. Everything essential sits
  inside the central 1546×423 safe area, so it holds on TV, desktop and phone.

**`backdrops/`** — four 1080×1920 contour grounds (ink, teal, terracotta,
parchment) for reel covers. The contour texture is the channel's recurring
signature; keep using it and it becomes recognisable at thumbnail size.

To wire these into the pipeline, point `core/image_generator.py` at a backdrop
instead of `render_placeholder_art()` — a real ground plus the title overlay
already beats what the placeholder produces, and it removes the dependency on
an image model for the base layer.

**`source/build.py`** — regenerates everything. `pip install cairosvg`, install
the two fonts, then `python3 build.py`. Change a hex in the palette block at
the top and the whole kit re-renders consistently.

## Using it

- The tagline is *Maps explain where. Legends explain why.* Keep it verbatim
  everywhere — repetition is what makes a tagline do its job.
- The ampersand is always ochre. It is the one piece of colour in the wordmark
  and it is what makes the lockup recognisable at a glance.
- Do not restyle the mark's interior. The legend rows are the idea.
