"""The default word lists. Admins add to them from the admin page (stored in moderation_words), so this file only
needs the common cases. Matching is in service.py: whole words (plus simple endings: -s, -es, -ed, -ing), after
undoing look-alike spellings (sh1t, $hit, s.h.i.t, shiiit).

STRONG words are also caught inside other words ("fuckgame"), so only put words here that never sit inside a
normal word: "rapist" can't be strong (therapist), nor "retard" (flame retardant), "spic" (spicy), "pedo"
(torpedo) or "pussy" (pussycat). Everything else matches as a whole word only, so "assassin" and "classic" are fine.
"""

# Caught anywhere, even inside other words.
STRONG = {
    "fuck", "fuk", "fck", "motherfucker", "shit", "bullshit", "cunt", "nigger", "nigga", "faggot", "whore", "slut",
    "bitch", "wanker", "dickhead", "porn", "pedophile", "paedophile", "tranny",
}

# Caught as whole words only (and with simple endings: -s, -es, -ed, -ing).
WORDS = {
    "ass", "arse", "asshole", "arsehole", "bastard", "bollocks", "boob", "boobs", "dildo", "fag", "jizz", "nazi",
    "nude", "nudes", "penis", "rape", "rapist", "retard", "scrotum", "semen", "sex", "sexy", "tits", "titty", "twat",
    "vagina", "xxx", "hentai", "milf", "orgy", "anal", "blowjob", "handjob", "cum", "horny", "hitler", "kkk",
    "suicide", "kys", "negro", "coon", "gook", "wetback", "dyke", "pussy", "pedo", "paedo", "spic", "chink", "kike",
    # Nigerian Pidgin and Yoruba insults
    "ashawo", "ashewo", "olosho", "oloshi", "mumu", "werey",
}

# Fine on start.ng even though a blocked word sits inside them.
ALLOWED = {"scunthorpe", "penistone", "cockburn", "shitake"}
