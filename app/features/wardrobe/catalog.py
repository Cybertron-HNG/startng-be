"""Every wardrobe item: its slot, its rarity and how a player gets it. Add an item with one `Item` here (and, for an
earned one, the rule in rewards.py or rules.py that grants it).

Rarity says how hard an item is to get, not only its price. Epic, Legendary and Limited items can never be bought,
so wearing one always means the player did something.
"""
from dataclasses import dataclass
from datetime import date

# Lowest to highest. The website colours items by rarity.
RARITIES = ("common", "uncommon", "rare", "epic", "legendary", "limited")
SHOP_RARITIES = ("common", "uncommon", "rare")

# `hat` and `accessory` are the character style's own fields: a worn item becomes their value (`hat: "gele"`), and games
# that don't know it fall back to their default. The other slots are new and only reach games in the `items` list.
STYLE_SLOTS = ("hat", "accessory")
SLOTS = ("hat", "accessory", "outfit", "neck", "back", "aura")


@dataclass(frozen=True)
class Item:
    key: str
    name: str
    slot: str
    rarity: str
    how: str  # how to get it, shown on locked items
    price: int | None = None  # coins in the shop; None = earned only
    available_from: date | None = None  # Limited items: only in the shop between these days (Nigeria time)
    available_until: date | None = None


CATALOG: list[Item] = [
    # common: the shop, 100-300 coins
    Item("bucket_hat", "Bucket hat", "hat", "common", "Buy it in the shop", price=150),
    Item("sunglasses", "Sunglasses", "accessory", "common", "Buy it in the shop", price=120),
    Item("wristband", "Wristband", "accessory", "common", "Buy it in the shop", price=100),
    Item("plain_hoodie", "Plain hoodie", "outfit", "common", "Buy it in the shop", price=250),
    # uncommon: the shop, 500-1,000
    Item("kufi", "Kufi cap", "hat", "uncommon", "Buy it in the shop", price=600),
    Item("headphones", "Headphones", "accessory", "uncommon", "Buy it in the shop", price=700),
    Item("ankara_top", "Ankara top", "outfit", "uncommon", "Buy it in the shop", price=900),
    Item("bead_necklace", "Bead necklace", "neck", "uncommon", "Buy it in the shop", price=500),
    # rare: the shop, 2,000-5,000, or a goal
    Item("gele", "Gele", "hat", "rare", "Buy it in the shop", price=2500),
    Item("agbada", "Agbada", "outfit", "rare", "Buy it in the shop", price=4000),
    Item("gold_chain", "Gold chain", "neck", "rare", "Buy it in the shop", price=3000),
    Item("explorer_cap", "Explorer cap", "hat", "rare", "Play 5 different games"),
    # epic: goals only
    Item("silver_crown", "Silver crown", "hat", "epic", "Hold a top 10 place on a game with 30 or more players"),
    Item("streak_scarf", "Streak scarf", "neck", "epic", "Play 7 days in a row"),
    Item("champion_sash", "Champion's sash", "neck", "epic", "Beat a friend's challenge link"),
    Item("veteran_jacket", "Veteran jacket", "outfit", "epic", "Reach level 10"),
    # legendary: goals only, very hard
    Item("gem_crown", "Gem crown", "hat", "legendary", "Hold #1 on a game with 30 or more players"),
    Item("tournament_cape", "Tournament cape", "back", "legendary", "Win a daily tournament"),
    Item("flame_aura", "Flame aura", "aura", "legendary", "Play 30 days in a row"),
    # limited: an event, never comes back
    Item("pioneer_pin", "Pioneer pin", "neck", "limited", "Given to start.ng's first players"),
    Item("detty_december_hat", "Detty December hat", "hat", "limited", "In the shop during December 2026 only", price=1500,
         available_from=date(2026, 12, 1), available_until=date(2026, 12, 31)),
]
BY_KEY: dict[str, Item] = {i.key: i for i in CATALOG}


def on_sale(item: Item, today: date) -> bool:
    """Whether the shop sells this item today."""
    if item.price is None:
        return False
    if item.rarity == "limited":
        return item.available_from is not None and item.available_until is not None and item.available_from <= today <= item.available_until
    return item.rarity in SHOP_RARITIES


# The free choices in the character creator. An item can't reuse one, or earning it would mean nothing.
FREE_VALUES = {"none", "cap", "beanie", "tophat", "crown", "glasses", "earrings", "beard", "scarf", "backpack"}

# Catalogue mistakes fail when the server starts, not when a player hits them.
for _i in CATALOG:
    assert _i.slot in SLOTS and _i.rarity in RARITIES, _i.key
    assert _i.key not in FREE_VALUES, f"{_i.key} is already a free choice in the character creator"
    assert _i.price is None or _i.rarity in SHOP_RARITIES or _i.rarity == "limited", f"{_i.key}: {_i.rarity} items can't be bought"
assert len(BY_KEY) == len(CATALOG), "two items share a key"
