"""Does a Hindi line say this English name? A consonant-skeleton match.

Labels are English ("Somnath", "Dwarka"), the narration is Devanagari
(सोमनाथ, द्वारका). A model asked "is this label spoken?" said no on 29 Sep and
every label in the Gujarat reel was dropped. Both scripts are reduced to their
consonant skeletons, aspirates folded (kh->k, ध->d, w/v, f/ph) and vowels
dropped: Somnath -> smnt, सोमनाथ -> smnt; Dwarka -> dvrk, द्वारका -> dvrk.
"""
import re

_DEV = {
    "क": "k", "ख": "k", "ग": "g", "घ": "g", "ङ": "n", "च": "c", "छ": "c", "ज": "j", "झ": "j",
    "ञ": "n", "ट": "t", "ठ": "t", "ड": "d", "ढ": "d", "ण": "n", "त": "t", "थ": "t", "द": "d",
    "ध": "d", "न": "n", "प": "p", "फ": "p", "ब": "b", "भ": "b", "म": "m", "य": "y", "र": "r",
    "ल": "l", "ळ": "l", "व": "v", "श": "s", "ष": "s", "स": "s", "ह": "h", "ं": "n", "ँ": "n",
    "क़": "k", "ख़": "k", "ग़": "g", "ज़": "j", "ड़": "d", "ढ़": "d", "फ़": "p", "य़": "y",
}
_NUKTA = "़"
_LAT = [("c", "k"), ("C", "c"), ("ph", "p"), ("bh", "b"), ("kh", "k"), ("gh", "g"),
        ("jh", "j"), ("th", "t"), ("dh", "d"), ("sh", "s"), ("f", "p"), ("w", "v"),
        ("z", "j"), ("q", "k"), ("x", "ks")]
GENERIC = set("""temple temples mandir river rivers dam lake hill hills fort district state
national park city town sea bay ocean island islands valley mountain mountains the of and
jyotirlinga tirth shree shri sri lord mata ji dev devi basin delta""".split())
NUM_WORDS = re.compile(
    r"\d|एक|दो|तीन|चार|पांच|पाँच|छह|सात|आठ|नौ|दस|बीस|तीस|चालीस|पचास|साठ|सत्तर|अस्सी|नब्बे|सौ|हज़ार|हजार|"
    r"लाख|करोड़|करोड|पच्चीस|पैंतीस|पचपन|शताब्दी|सदी|प्रतिशत|किलोमीटर|मीटर|वर्ग")


_STOP = set("""में के की का को से पर और है हैं था थी थे ने भी तो ही यह वह इस उस एक इसी इन उन जो कि
तक लिए साथ बाद पहले रूप तरह वाला वाली वाले""".split())


def dev_skeleton(text):
    out = []
    for ch in str(text).replace(_NUKTA, ""):
        c = _DEV.get(ch)
        if c:
            out.append(c)
    return _squash("".join(out))


def lat_skeleton(word):
    w = re.sub(r"[^a-z]", "", str(word).lower())
    w = w.replace("chh", "C").replace("ch", "C")
    for a, b in _LAT:
        w = w.replace(a, b)
    w = re.sub(r"[aeiouy]", "", w)
    w = w.replace("h", "") if len(w) > 3 else w
    return _squash(w)


def _squash(s):
    return re.sub(r"(.)\1+", r"\1", s).replace("h", "")


def name_words(label):
    """The distinctive words of a label, as skeletons of at least 3 letters."""
    ws = [w for w in re.findall(r"[A-Za-z]+", str(label)) if w.lower() not in GENERIC]
    return [s for s in (lat_skeleton(w) for w in ws) if len(s) >= 3]


def spoken(label, hindi_text):
    """True when the Hindi line says the label (a name) or a number (a figure)."""
    if re.search(r"\d", str(label)):
        return bool(NUM_WORDS.search(str(hindi_text)))
    # Word by word, as a prefix: द्वारकाधीश (dvrkds) says Dwarka (dvrk), but
    # समुद्र (smdr) must not say Modhera (mdr) just because it contains it.
    # Postpositions and helpers never start a name: "में गंगा" (mn+gng) must not
    # say "mango" (National Symbols reel, 29 Sep).
    toks = [dev_skeleton(w) for w in re.findall(r"[\u0900-\u097F]+", str(hindi_text))
            if w not in _STOP]
    toks = [t for t in toks if t]
    spans = toks + [a + b for a, b in zip(toks, toks[1:])]
    # EVERY distinctive word of the label must be said ("Bhagwan Swaminarayan"
    # is not said by "भगवान कृष्ण"); a short word (Gir, Nandi) must match a whole
    # Hindi word, a longer one may be its prefix (Dwarka in द्वारकाधीश).
    ws = [w for w in re.findall(r"[A-Za-z]+", str(label)) if w.lower() not in GENERIC]
    sks = [lat_skeleton(w) for w in ws]
    sks = [k for k in sks if k]
    if not sks or all(len(k) < 3 for k in sks):
        return False
    return all(any((sp == k) if len(k) < 3 else sp.startswith(k) for sp in spans) for k in sks)
