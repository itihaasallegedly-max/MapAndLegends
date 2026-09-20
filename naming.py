"""One place that decides what an output file is called.

Every artifact carries the topic slug in its own filename, so a file still
identifies itself once it has been dragged out of its folder — six daily
runs otherwise leave you with script.md, script(1).md, script(2).md.
"""
import os
import re


def slugify(text, limit=80):
    """Filesystem-safe topic slug. Stable: the same topic always maps here."""
    s = re.sub(r"[^A-Za-z0-9\s\-_]", "", str(text)).strip().lower()
    s = re.sub(r"[\s\-]+", "_", s)
    return s[:limit].strip("_") or "topic"


# suffix -> extension, for every file the pipeline writes
KINDS = {
    "sheet": ".txt",           # the human production sheet
    "data": ".json",           # the sheet's machine copy
    "script": "_script.json",  # raw Prompt 2 output
    "factcheck": "_factcheck.json",
    "cover": "_cover.jpg",
    "cover_raw": "_cover_raw.jpg",
    "slide2": "_slide2.jpg",
    "slide3": "_slide3.jpg",
    "voiceover": "_voiceover.mp3",
    "srt": ".srt",
    "ass": ".ass",
    "reel": "_reel.mp4",
    "publish": "_publish.json",
    "topic_spec": "_topic_spec.json",   # the draw, kept for the render phase
    "art_source": "_art_source.json",   # where the cover art actually came from
}

# One still per scene. The renderer derives several shots from each, so a
# handful of images still cuts every few seconds.
for _i in range(1, 9):
    KINDS[f"scene{_i}"] = f"_scene{_i}.jpg"


def path(output_dir, slug, kind):
    try:
        suffix = KINDS[kind]
    except KeyError:
        raise KeyError(f"unknown artifact kind {kind!r}; known: {sorted(KINDS)}")
    return os.path.join(output_dir, f"{slug}{suffix}")


def all_paths(output_dir, slug):
    return {k: path(output_dir, slug, k) for k in KINDS}
