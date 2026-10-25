"""The tag list and the rules that suggest tags. A rule only suggests: a person approves (service.approve).
Each rule matches words in a game's name and description. Edit here to add or change a tag."""

# group -> [(slug, label, one line, regex on the lowercase "name description")]
# A regex of None means the tag is on every game (the words people search).
GROUPS: dict[str, list[tuple[str, str, str, str | None]]] = {
    "How you play": [
        ("endless-runners", "Endless runners", "Dash, dodge and keep going", r"dash|rush|\brun\b|runner|sprint|skyrun|glide|\bhop\b|hopper|crossing|courier|lanedash|endless"),
        ("racing-and-driving", "Racing and driving", "Cars, bikes and danfo", r"race|racer|racing|drift|buggy|bike|wheelie|velocity|danfo|go.slow|derby|stunt"),
        ("shooting-and-action", "Shooting and action", "Aim, fire and fight", r"sniper|slash|kingpin|quickfire|breaker|shoot|agent|protocol|zombie"),
        ("archery", "Archery", "Draw, aim and release", r"archery|arrow|bullseye"),
        ("platformers", "Platformers", "Run, jump and climb", r"platformer|whimsy|dreamy|\bhop\b|hopper"),
        ("brainy-games", "Brainy games", "Think fast, think smart", r"grid|bloom|\bink\b|inkbreak|sort|maths|word|follow|\biso\b|circuit|riftsync|junction|zobo|puzzle|logic|quiz"),
        ("board-and-card", "Board and card", "Classics with a Naija twist", r"\bayo\b|drafts|jackpot|pick.me|chess|ludo|\bcard\b|board"),
        ("catch-and-dodge", "Catch and dodge", "Quick, casual, easy to learn", r"catch|dodge|bucket|balloon|bounce|bump|splash|glitter|collector|flappy|coconut"),
        ("coin-collecting", "Coin collecting", "Grab everything that shines", r"coin|\bgem\b|gems|crystal|naira|\borb\b"),
        ("stack-and-timing", "Stack and timing", "Tap at the right moment", r"stack|tapper|\btower\b|timing"),
        ("survival", "Survival", "How long can you last?", r"surviv|flood|shard|crowd|scrap|apocalypto|downshaft|dead.signal"),
        ("tower-defence-and-strategy", "Tower defence and strategy", "Plan, place and protect", r"defen[cs]e|strategy|sneaky|squad"),
        ("football-and-sports", "Football and sports", "Goals, penalties and tactics", r"penalty|football|\bfc\b|cork|derby|showball|soccer|sport"),
        ("simulation-and-management", "Simulation and management", "Run a stall, plan a budget", r"budget|job.hunt|city.hustle|\boven\b|market.hustle|panic|soup|manage|simulat"),
        ("escape-games", "Escape games", "Find the way out", r"escape|\bvault\b"),
    ],
    "Setting and theme": [
        ("nigerian-themed", "Nigerian themed", "Lagos, jollof, danfo and more", r"lagos|naija|nigeria|jollof|danfo|okada|agege|mama.put|puff.puff|suya|zobo|naira|owambe|nepa|\bayo\b|boju|ile.ere|yankari|gidi|sharp.sharp|\bmami\b|suwe|streetpk|finding.mama|wahala|go.slow|murewa|onithsa|abuja|kano|ibadan"),
        ("african-themed", "African themed", "Stories and places from across Africa", r"africa|salone|yankari|lagos|naija|nigeria|jollof|danfo|okada|agege|nairobi|kenya|ghana|accra|safari"),
        ("lagos", "Lagos", "The city that never sleeps", r"lagos|danfo|okada|agege|gidi|go.slow|lagos"),
        ("markets-and-street-life", "Markets and street life", "Haggle, hustle, survive", r"market|hustle|mama.put|streetpk|suya|puff|zobo"),
        ("food-and-cooking", "Food and cooking", "Jollof, suya and puff puff", r"jollof|suya|puff|zobo|soup|\boven\b|mama.put|mango|coconut|cook|recipe"),
        ("space-and-sci-fi", "Space and sci-fi", "Stars, orbits and robots", r"\bstar\b|cosmic|space|orbit|nebula|perihelion|quantum|robot|tesla|starfall|skybund|galaxy|alien"),
        ("neon-and-cyberpunk", "Neon and cyberpunk", "Glowing grids and fast lights", r"neon|cyber|\bpulse\b|glide|alley|synth"),
        ("fantasy-and-magic", "Fantasy and magic", "Wizards, wicks and dreams", r"whimsy|dreamy|\becho\b|lantern|glitter|magic|wizard|dragon|fantasy|portal"),
        ("ancient-ruins-and-treasure", "Ancient ruins and treasure", "Temples, relics and secrets", r"relic|temple|treasure|vault|yankari|ruins|tomb|pyramid"),
        ("horror-and-spooky", "Horror and spooky", "Ghosts and dark corners", r"ghost|dead.signal|hooded|apocalypto|nightfall|haunt|zombie|horror|spooky"),
        ("mystery-and-detective", "Mystery and detective", "Follow the clues", r"mystery|detective|noir|clue|whodunit"),
        ("cosy-and-relaxing", "Cosy and relaxing", "Slow down, take a breath", r"cozy|cosy|relax|calm|chill"),
    ],
    "Learn and grow": [
        ("educational", "Educational", "Learn while you play", r"maths|\bword\b|library|\bgit\b|zedu|job.hunt|learn|educat|quiz"),
        ("maths-and-numbers", "Maths and numbers", "Sums, scores and strategy", r"maths|math|numbers|arithmetic"),
        ("words-and-reading", "Words and reading", "Letters, words and stories", r"\bword\b|spelling|reading|library"),
        ("coding-and-tech", "Coding and tech", "For future builders", r"\bgit\b|zedu|coding|code|developer|debug"),
        ("money-and-careers", "Money and careers", "Budgets, jobs and hustle", r"budget|job.hunt|career|salary|naira|money"),
    ],
    "Play together": [
        ("with-friends", "With friends", "Play with people, send a link", r"multiplayer|with.friends|co-?op|hangout|versus|\bpvp\b"),
        ("for-a-class-or-cohort", "For a class or a cohort", "Great for a group session", r"quiz|bingo|classroom|cohort|bootcamp"),
        ("two-player-classics", "Two-player classics", "Head to head", r"\bayo\b|drafts|chess|ludo|checkers"),
        ("five-minute-fun", "5-minute fun", "A short break, a quick win", r"dash|rush|catch|dodge|bucket|bounce|quick|endless"),
    ],
    "Look and feel": [
        ("3d-games", "3D games", "Look around, go deep", r"\b3d\b|three\.?js|webgl"),
        ("retro-and-pixel", "Retro and pixel", "Simple, bright and quick", r"retro|pixel|arcade|8.?bit"),
    ],
    "Words people search": [
        ("web-games", "Web games", "Play in any browser", None),
        ("browser-games", "Browser games", "No download, no install", None),
        ("free-online-games", "Free online games", "Free to play, right now", None),
        ("html5-games", "HTML5 games", "Built with the modern web", None),
    ],
}
GROUP_ORDER = list(GROUPS)
MIN_GAMES = 3  # a tag gets a public page only when this many approved games have it
