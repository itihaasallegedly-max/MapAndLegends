# Daily Geography Content Pipeline

Automated 45–60s vertical geography shorts for YouTube Shorts and Instagram
Reels: weighted topic draw, scripting, an independent fact-check gate, TTS,
render, publish.

> **Before anything else, run the preflight.**
> ```bash
> ./venv/bin/python preflight.py          # add --online to check tokens and model IDs
> ```
> It answers one question — could this pipeline publish tonight without you? —
> and names everything that would stop it: missing keys, an expired upload
> token, an empty backlog, no disk, a scheduler that has stopped firing. It
> also reports which of the two caption routes this machine will use.
>
> Reels in `outputs/` from before 13 Sep 2026 were produced by a template
> fallback that no longer exists. Do not post them.

---

## Architecture

```
STAGE 0  one-time    seed.json ──► Prompt 1 ──► topics.json (558 topics, 5 series)
STAGE 1  weekly      refresh view stats ──► reweight topics.json
STAGE 2  daily       weighted draw (SHA1 dedup)
                     ──► Prompt 2  script + facts[]
                     ──► Prompt 3  independent fact-check gate
                     ──► one still per scene + an animated map shot
                     ──► Kokoro TTS
                     ──► motion render: ~16 shots, cuts every 3-4s,
                         three-word captions ──► publish
```

Anything that cannot be done correctly raises. There are no silent
substitutions anywhere in the daily path — a run either produces a reel
built from real model output, or it stops and says why.

## Files

| File | Role |
|---|---|
| `diagnose_api.py` | Lists the model IDs your Gemini key serves. Start here. |
| `stage0_seed.py` | Writes `seed.json`. Polls Apify when `APIFY_API_TOKEN` is set; otherwise records `data_source="hand_seeded_priors"` so downstream weights are not mistaken for analytics. |
| `stage0_prompt1_backlog.py` | Prompt 1. Clusters series, excludes the last 7 days from medians, normalises weights, expands to 558 topics, and attaches a `subject` noun to each. |
| `stage1_refresh_stats.py` | Weekly refresh. Exits non-zero if it observed nothing. |
| `stage2_daily_draw.py` | Weighted draw filtered against `used_topics.sha1`. |
| `stage2_prompt2_script.py` | Prompt 2. Validates shape, runtime (45–60s) and that `facts[]` is non-empty and checkable. Raises otherwise. |
| `stage2_prompt3_factcheck.py` | Prompt 3. Recomputes `safe_to_publish` from the individual verdicts rather than trusting the model's own boolean. |
| `preflight.py` | Unattended-readiness check. Exit 1 means tonight's run would not publish. |
| `run_daily.sh` | One whole unattended run: preflight, pipeline, resume, publish retries, heartbeat. What the scheduler calls. |
| `install_schedule.sh` | Installs the LaunchAgents (daily 04:00, publish retries every 2h, weekly refresh). `--remove` uninstalls. |
| `pipeline_daily.py` | Daily orchestrator. Retries the next topic on a hold or an asset failure, logs every run to `logs/runs.jsonl`. `--resume` finishes scripts that never rendered. |
| `process_script.py` / `process_render.py` | The two phases on their own, for when you want to look at a script before it becomes a video. |
| `retry_publish.py` | Retries every publish that is due in `publish_queue.json`. |
| `core/model_client.py` | Every Gemini call. Retries transient failures, moves to a fallback model ID, aborts the run on depleted credit. |
| `core/publish_queue.py` | Failed uploads, with backoff. A Reel live on Instagram is never posted twice while YouTube retries. |
| `tests_automation.py` | The unattended paths, tested: quota, retries, queue, captions, resume. |
| `core/image_generator.py` | One still per scene from the shot prompts, plus the thumbnail. Falls back to brand backdrops per scene. |
| `core/map_animator.py` | The channel's signature shot: a zoom from the wide view to the subject, drawn from `geo/`. No API, no tiles, no key. |
| `core/motion_renderer.py` | Shot plan, per-shot motion, hard cuts, title card and kinetic captions. |
| `geo/` | Bundled Natural Earth geometry: 177 countries, 294 admin-1 regions (all 36 Indian states), 1367 named rivers. |
| `core/voice_generator.py` | Kokoro-82M via `narrate.py`, mastered to −16 LUFS. |
| `core/video_assembler.py` | 3-slide 1080×1920 render. Caption and slide timings derive from the measured voiceover duration. Burns captions with libass where it exists and with Pillow overlays where it does not, then checks they are on the pixels. |
| `core/uploader.py` | Real YouTube resumable upload and Instagram Reels resumable upload + publish. |
| `script_sheet.py` | Renders the one-file text production sheet for `--script-only`. |
| `naming.py` | The single place that decides output filenames. |
| `pipeline_errors.py` | The exception types that make failures visible. |
| `archive/` | The retired first-generation pipeline. See `archive/README.md`. |

## Setup

```bash
./venv/bin/pip install -r requirements.txt
```

Then in `.env`:

| Key | Notes |
|---|---|
| `GEMINI_TEXT_MODEL`, `GEMINI_IMAGE_MODEL` | Must appear in `diagnose_api.py` output. |
| `VOICE_NAME` | A Kokoro voice (e.g. `am_michael`). An Edge-TTS name is now a hard error, not a silent swap. |
| `CHANNEL_HANDLE` | **Required.** Burned into every slide footer. Left empty on purpose — the previous value was the reference account's handle. |
| `ALLOW_PLACEHOLDER_ART` | Keep `false`. `true` renders placeholder geometry for local tests only. |
| `IG_USER_ID`, `IG_ACCESS_TOKEN` | Instagram Reels publishing. |
| `APIFY_API_TOKEN` | Optional. Without it, series weights are hand-set priors. |

One-time YouTube authorisation, from your own terminal:

```bash
# place your OAuth desktop-app client JSON at ./client_secrets.json first
./venv/bin/python -c "from core.uploader import authorise_youtube; authorise_youtube()"
```

The token it saves is what lets cron publish unattended.

## Usage

```bash
./venv/bin/python diagnose_api.py                      # check model IDs
./venv/bin/python stage0_prompt1_backlog.py            # rebuild the backlog
./venv/bin/python pipeline_daily.py --script-only      # script sheet only
./venv/bin/python pipeline_daily.py --generate-only    # render, don't publish
./venv/bin/python pipeline_daily.py --topic "Course and Origin of River Kaveri"
./venv/bin/python pipeline_daily.py                    # render and publish
crontab cron_jobs.sh                                   # install the schedule
```

Exit codes: `0` published (or rendered / scripted), `1` nothing publishable
after `--max-attempts` topics, `2` unhandled error.

### `--script-only`

Draws a topic, writes the script, runs the fact-check gate, then stops. No
voiceover, no images, no render, no upload. It produces one file per video:

```
outputs/<topic>/<topic>.txt     the production sheet
outputs/<topic>/<topic>.json    the same data, for a machine
```

Plain text, for a workflow where you do the voice and the visuals yourself.
Each scene carries its narration, its timecode, one **text-to-video prompt**,
a line for **every second inside that scene**, and the **audio** it is mixed
to. Then the full narration as one block to paste into a speech engine; the
post caption; and the fact-check verdicts with a source against each claim.
`outputs/jharkhand_story/cultural_symbols_and_food_of_jharkhand.txt` is a
worked example.

### The scene grammar

`core/scene_grammar.py` holds one rule the rest of the pipeline is bent to:

**Every scene is 4, 6, 8 or 10 seconds. Nothing else.**

Those are the lengths text-to-video models actually serve, so a scene is one
generation and never a join. Prompt 2 is asked for them directly; whatever it
returns is snapped onto the grid afterwards, so a model that answers with a
15-second scene cannot break an unattended run. If the scenes it returned
cannot tile the 45-60s window, the longest are split at sentence boundaries
until they can — narration is only ever cut where the writer already put a
full stop, never rewritten.

**Every scene carries one prompt per second.** An 8-second scene has eight
`per_second` entries, one for each second of the take. They are beats inside
a single continuous shot — where the camera starts, what enters at 3s, where
it settles — never cuts. A second the model left undescribed is filled with
camera language derived from the scene's own shot, so the count is always
exact and nothing on screen is invented.

**Every word is attributed to a voice, on the second it is spoken.** Each
segment carries a `speaker` — `NARRATOR (V.O.)` unless the script names
another role — and the sheet places that scene's words across its seconds by
character weight at the channel's measured read (~133 wpm), so each second
shows `NARRATOR (V.O.): "the words for this second"` above the picture for
that second. A line shorter than its scene is spoken early and the remaining
seconds read `(silence)` — that is where the picture and the sound design
carry the shot alone. The words come from `text` alone and are never written
into the prompts, so they cannot drift out of sync. With more than one voice
in a script, the header lists the cast and the full-narration block is broken
out per voice to record separately.

**Every scene says what it sits on.** A `background` block gives its `type`
(`map`, `illustration`, `photo_plate`, `texture`, `solid`) and describes the
plate. When it is a **map** — inferred from the shot, or from a `map_scenes`
entry whose place or label appears in that line — it also gives what area the
map covers, **every region to highlight** paired with what to name it and what
figure stands on it, what the rest of the map looks like (the house style
desaturates everything outside the highlight), which features to draw, and
whether the map holds or zooms. Places are matched by name, never by position,
so a line naming four states lights all four in the order it speaks them.
Every background also carries the two rules the renderer depends on: no text,
names or legends baked into the generated image, and the subject composed
above y=1380, clear of the burned-in caption band.

**Every scene carries a full audio description**: `ambience` (the bed a mic
in that place would pick up), `music` (instruments and what they do here),
`sfx` (spot effects, each with the second it lands on, relative to the
scene), `voice` (direction for the read) and `mix` (levels, and what ducks
under what). Missing fields get a sensible default rather than an empty line.

Prompt 2 writes shots, not stills — subject, what moves, how the camera moves
— and is told to keep text, signage and labelled maps out of frame, since the
renderer burns in all the type.

Two settings shape this, both in `.env`:

- `VIDEO_STYLE` is appended to every scene prompt so a set of shots looks like
  a set. Change that line and the channel's whole look changes with it.
  (`IMAGE_STYLE` is still honoured if that is what your `.env` has.)
- `CLIP_SECONDS` is the longest clip your video model will generate in one go.
  It defaults to **10**, the top of the scene grid, so every scene is exactly
  one generation. Drop it to 8 (Veo, Sora) or 5 (Kling's short mode) and any
  scene above the cap is split across that many clips on the sheet, each
  showing the slice of timeline it has to cover, with a continuation line for
  the clips after the first.

The topic is recorded in `used_topics.sha1` on a script-only run, because the
script exists and has passed the gate. Use `--topic` to script something
without spending it.

## Collections — the shape that carries the account

The teardown measured it on @dailygeomap: everything above 1M views is a
**collection** (every state's fish, the mountains of India, the temples of one
state) and everything under 200K is a single subject (one river, one kingdom,
one institution). In the seed the pipeline now ships with, set-shaped posts
out-perform single-subject ones **12.7x** (1.45M vs 114K median views).

Three changes follow from that:

- **`stage0_seed.py`** falls back to `TEARDOWN_OBSERVED_POSTS` — the ~20 reels
  read off the live profile on 13 Sep 2026 with their real view counts —
  instead of the invented `SAMPLE_SEED_POSTS`. A configured `APIFY_API_TOKEN`
  still wins; `seed.json` records which source was used.
- **`stage0_prompt1_backlog.py`** classifies the seed on a second axis. The
  series buckets sort by *subject*; `is_set_shaped()` sorts by *shape*, and
  the ratio between the two medians is written to `topics.json` as
  `shape_basis.set_lift`. A new **Collections** series is weighted off the
  set-shaped median.
- **`stage0_collections.py`** builds the set topics themselves: 143 of them,
  from ten Indian groupings (all states, South, North East, Himalayan, coastal,
  UTs…) crossed with seventeen attributes (state animal, dance, dish, highest
  peak, longest river…), plus five world groupings. Each carries its
  `members`, how many to `cover`, and the `ask` that closes it. The places are
  geography; *what* belongs to each is left to Prompt 2 and the fact-check
  gate, because those are the claims.

Existing topic strings are untouched, so `used_topics.sha1` still resolves —
set topics are appended and out-rank the rest by weight, not by deletion.

**Designed incompleteness.** A set of 28 covers 8 and closes on "Which state
did I miss?". That is the teardown's engagement finding made mechanical: an
incomplete list about someone's home state is an invitation to correct it, and
the correcting is what carries the post. Prompt 2 receives the full list, the
count to name, and the exact closing question; `validate_set()` appends the
question if the model forgot it, and **rejects a set script with fewer than
three `map_scenes`** — the map walking the list is the format, and below three
the renderer falls back to guessing the pairings from prose, which is how they
got crossed before.

**`DRAW_WEIGHT_EXPONENT`** (default 2, in `.env`) tilts the daily draw toward
the topics worth making. A weight is an estimate of how a topic will do;
drawing in direct proportion to it still publishes the weakest topics two
draws in three, simply because there are more of them. At 2, set topics take
~40% of draws while every topic stays reachable. Set it to 1 for the old
proportional behaviour, 0 for uniform.

## The feedback loop, on free reads only

Stage 1 used to reweight the backlog from a fixed snapshot — first invented
numbers, then the 13 Sep teardown. Both are somebody else's channel, and
neither moves when you publish. The obvious fix was a paid scraper. The better
and cheaper one is to read back your own results:

- `core/performance.py` keeps **`performance.json`** — one row per published
  video, with its topic, series, whether it was a set, and its platform ids.
  `pipeline_daily` writes a row on every publish.
- `refresh()` reads view counts back: YouTube's `videos.list` and Instagram's
  media insights, both **free reads on the credentials the upload already
  uses**. `YOUTUBE_SCOPES` now includes `youtube.readonly`, so one
  authorisation covers uploading and reading.
- `as_seed_posts()` hands those rows to Stage 0 in exactly the shape
  `seed.json` already uses, so the series medians **and the set/single lift**
  are recomputed from your own channel with no other change anywhere.

The switch is automatic and cautious: below `PERFORMANCE_MIN_ROWS` (8)
measured reels the medians are noise, so Stage 0 stays on the teardown and
says so. Above it, `seed.json` records `data_source: own_channel` and every
weight describes your audience rather than a competitor's.

Preflight's `feedback loop` check says how close it is.

## Sound, and the grid against real audio

**`brand/audio/`** is the hook the per-scene audio descriptions were writing
for. Drop licensed files into `music/` and `ambience/` and every render lays
them under the narration; leave it empty and reels ship as narration only and
nothing breaks. The bed is ducked with a real sidechain off the voice, so it
lifts in the gaps — which is what the sheet's `(silence)` seconds are for.
Levels come from `.env` (`MUSIC_LEVEL_DB`, `AMBIENCE_LEVEL_DB`,
`MUSIC_DUCK_DB`) and match what the sheet prints, so the two never disagree.
The track is chosen by topic slug, so re-rendering a reel keeps the same
music. **You own the rights to whatever is in there.** Licensed music costs money and
unlicensed music costs a takedown, so there is a third option built in:

    ./venv/bin/python core/audio_bed.py --make

writes three authored pads and one air bed into `brand/audio/` — stacked sine
drones and filtered noise, no samples, no library, no rights holder but you.
Deliberately plain: a pad under a documentary read is doing atmosphere, and a
tune would fight the narration. Drop licensed tracks in later and they are
used instead; existing files are never overwritten unless you pass `--force`.

**The grid is no longer just a plan.** Prompt 2's seconds are an estimate, and
they have been measured drifting ~25% short of the real read; the renderer
then scaled everything by that factor, so the cuts landed wherever the drift
put them. Once the voiceover exists, `scene_grammar.retime_to_audio()`
re-tiles the scenes against its measured length and re-places every word,
then the script and the production sheet are rewritten. The grid steps in 2s
so it cannot hit an arbitrary duration exactly — it lands within about a
second, and prints the residual the renderer still absorbs.

**Style references.** `brand/references/` may hold several reels now.
Prompt 2 picks one deliberately — `REFERENCE_VIDEO` in `.env` if set, else a
filename sharing a word with the topic, else the newest file — rather than at
random. `USE_REFERENCE_VIDEO=0` turns the whole step off.

**Captions** are normalised to the shape the teardown measured: one line, then
exactly three hashtags. Generic tags (`#Geography`, `#Shorts`, `#India`…) are
dropped rather than kept as filler, and any missing tag is built only from
words the topic already carries — a place it names, its title, its group.

Preflight reports all of this: `audio bed`, `backlog shape`, `style
references`.

## Output filenames

Every artifact carries the topic slug in its own name, so a file still
identifies itself after it leaves the folder — otherwise a week of runs gives
you `script.md`, `script(1).md`, `script(2).md` in Downloads. `naming.py` is
the only place these are decided; change a suffix there and the whole pipeline
follows.

```
outputs/course_and_origin_of_river_kaveri/
  course_and_origin_of_river_kaveri.txt             production sheet
  course_and_origin_of_river_kaveri.json            sheet data
  course_and_origin_of_river_kaveri_script.json     raw Prompt 2 output
  course_and_origin_of_river_kaveri_factcheck.json  gate verdicts
  course_and_origin_of_river_kaveri_cover.jpg       + _cover_raw, _slide2, _slide3
  course_and_origin_of_river_kaveri_voiceover.mp3
  course_and_origin_of_river_kaveri.srt             + .ass
  course_and_origin_of_river_kaveri_reel.mp4
  course_and_origin_of_river_kaveri_publish.json
```

Output folders written before this change still use the old generic names
(`cover.jpg`, `voiceover.mp3`). Nothing reads them, so they can be left alone.

## Running unattended

```bash
./install_schedule.sh                  # LaunchAgents, not cron — see below
launchctl kickstart -k gui/$UID/com.mapandlegend.daily   # run tonight's job now
tail -f logs/cron_daily.log
```

**LaunchAgents, not cron.** cron silently skips a job that came due while the
Mac was asleep, and 04:00 is exactly when a laptop is asleep. launchd runs the
missed job on wake. The old crontab also called `/usr/bin/flock`, which does
not exist on macOS, so that line could never have run at all; `run_daily.sh`
now takes its own lock.

**What each failure costs**, which is the whole point of the design:

| Failure | Before | Now |
|---|---|---|
| Depleted Gemini credit | five topics burned in 40s, nothing produced | run aborts on the first call, backlog untouched, exit 3 |
| One transient 503 | that topic lost | retried on the same topic, 4 attempts, doubling delay |
| Model ID not served | run over | falls through to `GEMINI_*_FALLBACKS` |
| Image model unreachable | run aborted, no video | brand backdrop from `brand/backdrops`, recorded in `*_art_source.json` |
| ffmpeg has no libass | reel shipped with no captions | cues drawn with Pillow and composited with `overlay`, which every build has |
| Caption filter behaves differently per build | guesswork | the filter is proved on a probe render, and the captions are verified on the pixels afterwards |
| Upload token expired | video lost | queued in `publish_queue.json`, retried 1h/4h/12h/24h |
| Render died after the gate | topic spent, no video | `--resume` finishes it on the next run |
| Pillow cannot shape Devanagari | garbled sub-header published | sub-header skipped, warning in the log |

Exit codes: `0` published/rendered/scripted, `1` nothing publishable,
`2` unhandled error, `3` out of model credit (nothing was spent).

```bash
./venv/bin/python tests_automation.py   # 15 cases, all of them a night this would have lost
```

## The visual engine

The old renderer made three stills — a cover and two text cards — and held
them for 5s, 33s and 17s. Measured frame by frame, the video changed **twice**
in 56 seconds. That is a slideshow, and captions do not fix it.

Now: one still per scene from the shot prompts Prompt 2 already writes, and
each still is read as several distinct shots (push in, pull out, pan across a
detail), so the frame changes every 3-4 seconds without one image per cut.
Somewhere in the first ten seconds the map takes over — a zoom from the wide
view down to the state, country or river, labelled, with the river course
drawing itself. Captions arrive in three-word groups, numbers and dates in
ochre. The title sits over the opening shot behind a gradient scrim instead of
being its own slide. There are no text cards at all.

Typical 56s reel: 16 shots, 3.5s average on screen, 41 caption groups,
about 40 seconds to render.

`geo/` is the whole dependency for the map — bundled geometry, no key, no
tiles, no network. A subject that does not resolve simply gets no map shot.

## Monitoring

```bash
./venv/bin/python preflight.py     # everything below, in one screen
tail -f logs/cron_daily.log        # this run
tail -5 logs/runs.jsonl            # outcome of the last 5 runs
cat logs/.last_daily               # heartbeat; stale means the scheduler is not firing
cat hold_queue.json                # topics the fact-check gate rejected
cat publish_queue.json             # reels rendered but not yet live
```

`hold_queue.json` filling up is the gate working. An empty `hold_queue.json`
after many runs means the gate is passing everything — which is how the
original silent fallback went unnoticed.

## Google Flow Example Prompts

If you ever need to manually type a generated script into Google Flow's Omni 1.1 Flash model to generate video + audio, you must append the Voiceover text to the video prompt so the AI reads it aloud. 

Example from the **Louisiana Purchase** topic:

**Scene 1 (4 seconds)**
`A map of North America in 1803, zooming into the vast Louisiana Territory. Voiceover: Did you know the United States doubled its size for just three cents an acre in 1803?`

**Scene 2 (8 seconds)**
`Highlighting the purchased territory which stretches from the Mississippi River to the Rocky Mountains. Voiceover: The Louisiana Purchase involved the U.S. buying 828,000 square miles of land from France.`

**Scene 3 (8 seconds)**
`Portrait of Thomas Jefferson appearing alongside the city of New Orleans. Voiceover: President Thomas Jefferson orchestrated the deal, originally just wanting to buy New Orleans.`

**Scene 4 (8 seconds)**
`A French flag and a stack of coins appearing over the map. Voiceover: But Napoleon Bonaparte, needing funds for his wars in Europe, offered the entire territory for 15 million dollars.`

**Scene 5 (8 seconds)**
`The territory splitting into modern US state borders, glowing with bright colors. Voiceover: This massive acquisition eventually formed all or part of 15 modern American states.`

**Scene 6 (8 seconds)**
`The full modern map of the United States. Voiceover: It remains one of the most consequential land deals in human history.`
