# Getting a clean full-size emblem master

The ribbon on the current emblem was repaired by editing pixels: the "S" was
removed and the two halves of the lettering shifted together. It holds
perfectly at avatar sizes (176px and below — which is everywhere the logo is
actually seen) but the repaired band is visible at full resolution, so don't
use `emblem-fixed-1024-*.png` as a large hero graphic or in print.

For a clean master, regenerate with this:

> A circular vintage cartography emblem logo. A compass rose with four gold
> points behind an open two-page map; the left page shows green terrain with
> river lines, the right page shows a blue sea with small gold islands. A deep
> navy circular border with a thin gold rim and small tick marks. Across the
> lower third, a navy ribbon banner with gold rope edges bearing the words
> "MAP & LEGEND" in gold Roman capitals — singular MAP, not MAPS. Colour
> palette: deep navy #0A1728, antique gold #D9A94C, cream #EEE4CD, forest
> green, sea blue. Flat vector crest style with subtle aged paper texture.
> Square, centred, transparent background, no extra text, no logos.

Then check three things before you use it:

1. **The ribbon reads MAP & LEGEND**, singular. This is the mistake that got
   through last time.
2. **No watermark.** Crop the corners and look. The banner you generated had a
   Grok mark in the bottom right.
3. **At least 1024x1024 and square.** The first one came out 356x349, below
   YouTube's 800x800 minimum and not quite square, which distorts on upload.

Send it over and I'll drop it into the banner and the reel renderer.
