# Daily Geography Content Pipeline

Automated 45–60s vertical geography shorts for YouTube Shorts and Instagram
Reels: weighted topic draw, scripting, an independent fact-check gate, TTS,
render, publish.

> **Before anything else, run the API diagnostic.**
> ```bash
> ./venv/bin/python diagnose_api.py
> ```
> Every reel currently in `outputs/` was produced by a template fallback,
> because the configured Gemini model IDs never returned a response. That
> fallback has been removed — the pipeline now raises instead of quietly
> shipping filler — so nothing will render until `GEMINI_TEXT_MODEL` and
> `GEMINI_IMAGE_MODEL` in `.env` name models your key actually serves.

---

## Architecture

```
STAGE 0  one-time    seed.json ──► Prompt 1 ──► topics.json (558 topics, 5 series)
STAGE 1  weekly      refresh view stats ──► reweight topics.json
STAGE 2  daily       weighted draw (SHA1 dedup)
                     ──► Prompt 2  script + facts[]
                     ──► Prompt 3  independent fact-check gate
                     ──► cover art + 2 info slides
                     ──► Kokoro TTS ──► ffmpeg ──► publish
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
| `pipeline_daily.py` | Daily orchestrator. Retries the next topic on a hold or an asset failure, logs every run to `logs/runs.jsonl`. |
| `core/image_generator.py` | Cover + 2 slides. Text is measured and wrapped to fit 1080px. |
| `core/voice_generator.py` | Kokoro-82M via `narrate.py`, mastered to −16 LUFS. |
| `core/video_assembler.py` | 3-slide 1080×1920 render. Caption and slide timings derive from the measured voiceover duration. |
| `core/uploader.py` | Real YouTube resumable upload and Instagram Reels resumable upload + publish. |
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
./venv/bin/python pipeline_daily.py --generate-only    # render, don't publish
./venv/bin/python pipeline_daily.py --topic "Course and Origin of River Kaveri"
./venv/bin/python pipeline_daily.py                    # render and publish
crontab cron_jobs.sh                                   # install the schedule
```

Exit codes: `0` published (or rendered, with `--generate-only`), `1` nothing
publishable after `--max-attempts` topics, `2` unhandled error.

## Monitoring

```bash
tail -f logs/cron_daily.log        # this run
tail -5 logs/runs.jsonl            # outcome of the last 5 runs
cat logs/.last_daily               # heartbeat; stale means cron is not firing
cat hold_queue.json                # topics the fact-check gate rejected
```

`hold_queue.json` filling up is the gate working. An empty `hold_queue.json`
after many runs means the gate is passing everything — which is how the
original silent fallback went unnoticed.
