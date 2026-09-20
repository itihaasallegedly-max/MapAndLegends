"""Set topics — the shape the competitor's numbers actually reward.

The teardown measured it: on @dailygeomap everything above 1M views is a
collection (every state's fish, the mountains of India, the temples of one
state taken together) and everything under 200K is a single subject (one
river, one kingdom, one institution). The backlog Stage 0 built is the
opposite — 547 of its 558 topics are single-subject, so the pipeline was
optimised to produce the 70-200K shape once a day forever.

This module builds the other shape. A set topic is one video that walks a
list of places and names the thing that belongs to each: the state animal of
every South Indian state, the highest peak of every Himalayan state, the
longest river of every continent.

Two things make a set topic different from a framing phrase, and both are
carried as fields the rest of the pipeline reads:

- `members` — the actual places, so Prompt 2 does not have to guess the list
  and `map_scenes` has one entry to fill per member. The places are the
  geography; what belongs to each (which fish, which peak) is left to Prompt 2
  and checked by the gate, because those are the claims.
- `cover` and `ask` — the incompleteness is designed in. The teardown's
  engagement finding was that an incomplete list about someone's home state is
  an invitation to correct it, and the correcting is what carries the post. A
  set of 28 covered 8 at a time, closing on "which state did I miss?", is
  doing on purpose what their comment sections do by accident.

No facts live here beyond which places are in which region. Nothing is
invented: a member list is a list of states.
"""

SERIES = "Collections"

# How many members one 45-60s video can actually name before it becomes a
# list read aloud. Above this the topic still ships — it just covers `cover`
# of them and asks about the rest.
MAX_COVERED = 8

# Below this a "set" is a comparison, not a list — there is nothing to leave
# out, so the format's engine is missing. Central India (two states) is the
# case that forced this; it drops out rather than shipping as a set of two.
MIN_MEMBERS = 4

STATE_GROUPS = {
    "Indian state": [
        "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
        "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka",
        "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya",
        "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim",
        "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand",
        "West Bengal",
    ],
    "South Indian state": [
        "Andhra Pradesh", "Karnataka", "Kerala", "Tamil Nadu", "Telangana",
    ],
    "North Eastern state": [
        "Arunachal Pradesh", "Assam", "Manipur", "Meghalaya", "Mizoram",
        "Nagaland", "Tripura", "Sikkim",
    ],
    "North Indian state": [
        "Delhi", "Haryana", "Himachal Pradesh", "Jammu and Kashmir", "Ladakh",
        "Punjab", "Rajasthan", "Uttar Pradesh", "Uttarakhand",
    ],
    "Western Indian state": ["Goa", "Gujarat", "Maharashtra", "Rajasthan"],
    "Eastern Indian state": ["Bihar", "Jharkhand", "Odisha", "West Bengal"],
    "Central Indian state": ["Chhattisgarh", "Madhya Pradesh"],
    "Himalayan state": [
        "Himachal Pradesh", "Uttarakhand", "Sikkim", "Arunachal Pradesh",
        "Jammu and Kashmir", "Ladakh",
    ],
    "coastal state": [
        "Gujarat", "Maharashtra", "Goa", "Karnataka", "Kerala", "Tamil Nadu",
        "Andhra Pradesh", "Odisha", "West Bengal",
    ],
    "Union Territory": [
        "Andaman and Nicobar", "Chandigarh", "Dadra and Nagar Haveli", "Delhi",
        "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Puducherry",
    ],
}

WORLD_GROUPS = {
    "continent": [
        "Asia", "Africa", "Europe", "North America", "South America",
        "Australia", "Antarctica",
    ],
    "South Asian country": [
        "India", "Pakistan", "Bangladesh", "Nepal", "Bhutan", "Sri Lanka",
        "Maldives", "Afghanistan",
    ],
    "Southeast Asian country": [
        "Indonesia", "Thailand", "Vietnam", "Malaysia", "Philippines",
        "Myanmar", "Cambodia", "Laos", "Singapore", "Brunei", "Timor-Leste",
    ],
    "Nordic country": ["Denmark", "Finland", "Iceland", "Norway", "Sweden"],
    "Gulf country": [
        "Saudi Arabia", "United Arab Emirates", "Qatar", "Kuwait", "Bahrain",
        "Oman",
    ],
}

# (attribute, the noun for the title, which group keys it makes sense for)
# A "state fish of every Union Territory" is a fine video; a "highest peak of
# every coastal state" is not, so each attribute names its own groups rather
# than taking the full cross product.
_ALL_STATE_GROUPS = tuple(STATE_GROUPS)
_MAINLAND = ("Indian state", "South Indian state", "North Eastern state",
             "North Indian state", "Western Indian state", "Eastern Indian state",
             "Central Indian state")

STATE_ATTRIBUTES = [
    ("state animal", "STATE ANIMALS", _ALL_STATE_GROUPS),
    ("state bird", "STATE BIRDS", _ALL_STATE_GROUPS),
    ("state tree", "STATE TREES", _MAINLAND),
    ("state flower", "STATE FLOWERS", _MAINLAND),
    ("state fish", "STATE FISH", ("Indian state", "coastal state",
                                  "South Indian state", "North Eastern state")),
    ("folk or classical dance", "STATE DANCES", _ALL_STATE_GROUPS),
    ("biggest harvest festival", "STATE FESTIVALS", _ALL_STATE_GROUPS),
    ("signature dish", "STATE FOOD", _ALL_STATE_GROUPS),
    ("official language", "STATE LANGUAGES", _ALL_STATE_GROUPS),
    ("highest peak", "HIGHEST PEAKS", ("Indian state", "Himalayan state",
                                       "North Eastern state", "South Indian state")),
    ("longest river", "LONGEST RIVERS", _MAINLAND + ("Himalayan state",)),
    ("capital city", "STATE CAPITALS", _ALL_STATE_GROUPS),
    ("largest city", "BIGGEST CITIES", _MAINLAND),
    ("oldest temple", "OLDEST TEMPLES", ("Indian state", "South Indian state",
                                         "Eastern Indian state", "Western Indian state")),
    ("biggest national park", "NATIONAL PARKS", _ALL_STATE_GROUPS),
    ("GI-tagged craft", "GI CRAFTS", _MAINLAND),
    ("traditional dress", "STATE DRESS", _ALL_STATE_GROUPS),
]

WORLD_ATTRIBUTES = [
    ("highest mountain", "HIGHEST PEAKS", tuple(WORLD_GROUPS)),
    ("longest river", "LONGEST RIVERS", ("continent", "South Asian country",
                                         "Southeast Asian country")),
    ("capital city", "CAPITALS", ("South Asian country", "Southeast Asian country",
                                  "Nordic country", "Gulf country")),
    ("national animal", "NATIONAL ANIMALS", ("South Asian country",
                                             "Southeast Asian country",
                                             "Nordic country")),
    ("currency", "CURRENCIES", ("South Asian country", "Southeast Asian country",
                                "Gulf country")),
    ("largest desert", "DESERTS", ("continent",)),
]


def _ask(group_noun):
    """The closing line that invites the correction."""
    if group_noun.endswith("state"):
        return "Which state did I miss?"
    if group_noun.endswith("country"):
        return "Which country did I miss?"
    if group_noun == "continent":
        return "Which one did I get wrong?"
    if group_noun == "Union Territory":
        return "Which UT did I miss?"
    return "Which one did I miss?"


def _plural(group_noun):
    if group_noun.endswith("y"):
        return group_noun[:-1] + "ies"
    return group_noun + "s"


def _topic(attribute, group_noun, members, title, weight):
    covered = min(len(members), MAX_COVERED)
    return {
        "topic": f"The {attribute} of every {group_noun}",
        "series": SERIES,
        "kind": "set",
        "subject": f"the {attribute} of each {group_noun}",
        "title_en": title,
        "group": _plural(group_noun),
        "members": list(members),
        "set_size": len(members),
        "cover": covered,
        "ask": _ask(group_noun),
        "weight": round(weight, 3),
    }


def build_collection_topics(base_weight=1.0):
    """Every set topic, heaviest first.

    `base_weight` is the Collections series weight the caller computed from
    the seed. Within the series, an all-India set outranks a regional one —
    the teardown's biggest numbers were national sets and single-state temple
    sets, not four-member regional ones — and a set small enough to cover
    completely is nudged down, because the whole engine is the omission.
    """
    topics = []

    for source, attributes in ((STATE_GROUPS, STATE_ATTRIBUTES),
                               (WORLD_GROUPS, WORLD_ATTRIBUTES)):
        for attribute, title, group_keys in attributes:
            for group_noun in group_keys:
                members = source.get(group_noun)
                if not members or len(members) < MIN_MEMBERS:
                    continue
                scale = 1.0
                if group_noun not in ("Indian state", "continent"):
                    scale *= 0.92          # regional sets, a smaller audience
                if len(members) <= MAX_COVERED:
                    scale *= 0.9           # nothing left to correct
                if source is WORLD_GROUPS:
                    scale *= 0.85          # the account's global posts run lower
                topics.append(_topic(attribute, group_noun, members, title,
                                     base_weight * scale))

    topics.sort(key=lambda t: -t["weight"])
    return topics


if __name__ == "__main__":
    import json
    built = build_collection_topics(0.97)
    print(f"{len(built)} set topics")
    for item in built[:5]:
        print(json.dumps(item, indent=2, ensure_ascii=False))
