# Narration reference — transcribed from the competitor Shorts

These are the two videos `stage2_daily_draw.py` actually downloaded, transcribed
from their audio (Whisper small.en, offline; phrase boundaries from ffmpeg
`silencedetect` at −32 dB / 0.25s). Every number below is measured, not estimated.

This file supersedes the earlier `narration-spec.md`, which was measured from a
**different** video (`FALKLAND ISLANDS DISPUTE.mp4`, landscape 720p, another
creator). That one pauses for 17% of its runtime; these two do not pause at all.
The rules below come from the channel the pipeline is actually copying.

## The measurements

| | `D0AWufa4iv4` — Canada's lakes | `_qu75do_9oe` — Great Lakes |
|---|---|---|
| Duration | 66.52s | 79.19s |
| Words | 166 | 186 |
| Rate (wall clock) | 150 wpm · 14.5 chars/s | 141 wpm · 14.0 chars/s |
| **Silence** | **1.02s — 1.5%** | **1.51s — 1.9%** |
| Longest pause | 0.63s | 0.43s |
| Sentences | 11 | 16 |
| Words per sentence | mean 18.5, median 19, max 32 | mean 14.6, median 13.5, max 34 |
| Connective-led | 4 / 11 | 5 / 16 |
| Questions | 0 | 1 (at 3.3s) |
| Numbers | 1 | 7 |
| CTA | none | none |

Two things fall out immediately:

1. **`CHARS_PER_SECOND = 14.5` was right all along.** They run 14.5 and 14.0
   characters per second of wall clock. The density was never the problem.
2. **Our runtime window was wrong.** They run 66.5s and 79.2s; we were
   targeting 45–60s. Now 60–80s.

## Transcript — Canada's lakes (66.52s, 166 words)

> Canada, home to more than 2 million lakes, and at some point that became a
> real nightmare for cartographers. You see, during the 19th and 20th centuries,
> their job wasn't just to map all the lakes, which became easier with aviation
> and aerial photography, but also to name every single one. At first everything
> went smoothly, and they used beautiful names indigenous peoples already had
> for the lakes, like Athabasca, Mistassini, or Nipigon. Others were named after
> people — Lake Louise, Victoria, or Patricia. But after a while it seems like
> they ran out of ideas, which led to names like Long Lake, Clear Lake, as well
> as Frozen, Rocky, or Mirror Lakes. But the number of lakes just kept growing.
> So they started using what locals, hunters and loggers already called them.
> This Lake, Another Lake, and Another Lake, Stupid Lake, and even Crotch Lake.
> And there are thousands more like these. As a result, the province of Nova
> Scotia officially has a lake called Big Ass. But just look at this one — it
> looks like the cartographer's cat just ran across the keyboard, but it
> actually comes from an indigenous language and roughly means "the lake where
> trout are caught with a hook."

Three phrases, three pauses (0.63s, 0.39s), nothing else.

## Transcript — Great Lakes (79.19s, 186 words)

> Along the border between the US and Canada lie the Great Lakes. But what's so
> great about them? The Great Lakes are made up of five lakes — Superior,
> Michigan, Huron, Erie and Ontario. Together they hold about 20% of all the
> surface freshwater on the planet. That's almost 23 quadrillion litres of
> water. But that number is probably hard to picture. So just imagine spreading
> all that water across North and South America, Africa, Eurasia, Australia and
> even Antarctica — literally all the land on Earth — and the water would still
> be about 15 centimetres thick. Lake Superior alone is the largest freshwater
> lake in the world by surface area. For comparison, it's almost the size of
> Austria, and larger than Czechia, Ireland, Panama and many other countries.
> These lakes are often called inland seas, because from the shore they look
> more like the open ocean than lakes. And just like at sea, there are real
> storms on these lakes. Waves can reach nearly 9 metres, turning some areas
> into literal ship graveyards. Lake Superior alone has seen around 350 ships
> sink, including the legendary Edmund Fitzgerald. The Great Lakes are connected
> by rivers and straits and form a single water system that flows into the
> Atlantic Ocean. They are a source of fresh water for 40 million people and
> home to around 4,000 species of plants and animals. So yeah, they really are
> great.

## The eight rules, and where each one comes from

1. **Continuous read.** 98% speech. No scripted pauses, no one-line scenes that
   leave air.
2. **Long, clause-chained sentences.** Median 15–19 words, longest ~32, joined
   with *and / which / but also / as well as*. Clipped documentary lines are the
   main thing that made ours sound unlike theirs.
3. **Open by locating, then undercutting.** One plain statement of where we are
   plus a fact of scale; then the second sentence takes it away. At most one
   question in the script, and only here.
4. **Talk to the viewer.** "You see," "For comparison," "But that number is
   probably hard to picture," "So just imagine," "just look at this one."
   About a third of sentences open with But / So / And / Then.
5. **Never leave a big number unconverted.** 23 quadrillion litres → spread over
   every continent, still 15 cm deep. Lake Superior → the size of Austria,
   bigger than Czechia, Ireland, Panama. **This is their signature move and ours
   had none of it.**
6. **Proper nouns are the texture.** Athabasca, Nipigon, Lake Louise, the Edmund
   Fitzgerald. "A famous lake" is the amateur tell.
7. **A list is allowed — and it is the joke.** But it must escalate to a
   punchline: Long Lake → Clear Lake → This Lake → Another Lake → Stupid Lake →
   Crotch Lake. Order the set so the strangest member lands last. (The earlier
   teardown's "never read a list aloud" was wrong; the ordering was the point.)
8. **End on a payoff or callback, never a fact and never a CTA.** "So yeah, they
   really are great." Neither video says subscribe, follow, or comment.

## How to re-measure

HuggingFace and openaipublic are both blocked from the cloud container, so
faster-whisper and openai-whisper are dead ends. `k2-fsa/sherpa-onnx` publishes
its models as **GitHub release assets**, and github.com is reachable:

```
pip install sherpa-onnx
curl -LO https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-whisper-small.en.tar.bz2
ffmpeg -i original_video.mp4 -vn -ac 1 -ar 16000 narration.wav
ffmpeg -i narration.wav -af silencedetect=noise=-32dB:d=0.25 -f null -   # phrase boundaries
```

Then `sherpa_onnx.OfflineRecognizer.from_whisper(...)` per phrase. Same trick as
the Kokoro weights on 13 Sep.
