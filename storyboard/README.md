# Story-reel generator (standalone)

Not wired into `pipeline_daily.py`. This is a parallel, illustration-led route
for the same topics: a narrative script, six hand-authored vector scenes, and a
Ken Burns stitch keyed to the real narration.

| File | Role |
|---|---|
| `artkit.py` | The visual system — palette and the drawing vocabulary (sal tree, sal leaf and blossom, palash flower, Sohrai border and vine, ridge, grain). |
| `scenes.py` | The six scene compositions. Each returns an SVG string. |
| `stitch.py` | Caption splitting, the ffmpeg filtergraph, and the render. |

## Requirements

```bash
pip install cairosvg kokoro-onnx soundfile
```

Kokoro model + voices (GitHub releases, no HuggingFace needed):

```bash
curl -sSL -o kokoro.onnx https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx
curl -sSL -o voices.bin  https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
```

## Build

```bash
python3 -c "
import json, cairosvg, scenes
d = json.load(open('story.json'))
for s in d['scenes']:
    t = scenes.SCENES[s['art']]()
    cairosvg.svg2png(bytestring=t.encode(), write_to=f\"img/scene_{s['id']}.png\",
                     output_width=1080, output_height=1920)
"
python3 stitch.py
```

`story.json` carries `scene_marks` — the measured start/end of each scene's
narration. Those drive both the image durations and the caption timings, so
re-synthesizing the voice and re-running `stitch.py` keeps everything in sync.

## Two things worth knowing

**Loop your overlay inputs.** A bare `-i overlay.png` is one frame at t=0, so
any `fade` on it evaluates once and holds. An alpha fade-in leaves the overlay
invisible for the entire video. Use `-loop 1 -framerate 25 -t <dur> -i x.png`.

**Feed zoompan exactly one frame.** `-framerate 1 -loop 1 -t 1 -i scene.png`.
With a plain `-loop 1`, zoompan emits `d` frames per input frame forever and
`concat` never advances past the first image.

Scene subjects are composed above y=1380 — below that is the caption band.
