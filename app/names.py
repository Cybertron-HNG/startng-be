"""A game's name for people, worked out from its address: "cork-fc" -> "Cork FC"."""

# Short words that are written in capitals, so title-casing the address doesn't turn "FC" into "Fc".
ACRONYMS = frozenset({"2d", "3d", "ai", "atm", "dj", "fc", "fps", "hq", "ng", "pc", "rpg", "tv", "ui", "uk", "usa", "vip", "vr"})


def title_of(name: str) -> str:
    return " ".join(w.upper() if w in ACRONYMS else w.capitalize() for w in name.split("-"))
