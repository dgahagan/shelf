"""Tags for a board game, from its BoardGameGeek categories and mechanics.

BGG files every game under a fixed vocabulary of categories ("Puzzle",
"Party Game", "Deduction", …) and mechanics. Shelf turns them into ordinary
item tags when a game is added, so the Browse tag filter can answer "show me
the puzzle games" without a new filter or column.

- Every BGG category becomes a tag. The list is closed and short, so each one
  is declared here and translated like any other UI word.
- Of the ~190 mechanics only a handful mean something to a player choosing
  a game for tonight ("Cooperative Game", "Trick-taking", …). Those few are
  mapped; the rest would bury the tag list and are dropped.
- An expansion gets an "Expansion" tag, so base games and add-ons can be told
  apart in Browse.

Tag names are stored data shared by every user, so they are translated into
the library's language (`i18n.library_pgettext`), never the language of the
person who scanned. The `bgg` context keeps "Book", "Music" and the like
apart from the media type words.

A category BGG adds later, and not declared here, is skipped rather than
filed in English beside translated tags; the test pins the known list.
"""

from app import i18n
from app.i18n import NC_

CONTEXT = "bgg"

#: BGG category name -> tag (English msgid, translated per library language).
CATEGORY_TAGS: dict[str, str] = {
    "Abstract Strategy": NC_("bgg", "Abstract Strategy"),
    "Action / Dexterity": NC_("bgg", "Dexterity"),
    "Adventure": NC_("bgg", "Adventure"),
    "Age of Reason": NC_("bgg", "Age of Reason"),
    "American Civil War": NC_("bgg", "American Civil War"),
    "American Indian Wars": NC_("bgg", "American Indian Wars"),
    "American Revolutionary War": NC_("bgg", "American Revolutionary War"),
    "American West": NC_("bgg", "Wild West"),
    "Ancient": NC_("bgg", "Ancient"),
    "Animals": NC_("bgg", "Animals"),
    "Arabian": NC_("bgg", "Arabian"),
    "Aviation / Flight": NC_("bgg", "Aviation"),
    "Bluffing": NC_("bgg", "Bluffing"),
    "Book": NC_("bgg", "Gamebook"),
    "Card Game": NC_("bgg", "Card Game"),
    "Children's Game": NC_("bgg", "Children's Game"),
    "City Building": NC_("bgg", "City Building"),
    "Civil War": NC_("bgg", "Civil War"),
    "Civilization": NC_("bgg", "Civilization"),
    "Collectible Components": NC_("bgg", "Collectible"),
    "Comic Book / Strip": NC_("bgg", "Comics"),
    "Deduction": NC_("bgg", "Deduction"),
    "Dice": NC_("bgg", "Dice Game"),
    "Economic": NC_("bgg", "Economic"),
    "Educational": NC_("bgg", "Educational"),
    "Electronic": NC_("bgg", "Electronic"),
    "Environmental": NC_("bgg", "Environmental"),
    "Expansion for Base-game": NC_("bgg", "Expansion"),
    "Exploration": NC_("bgg", "Exploration"),
    "Fan Expansion": NC_("bgg", "Expansion"),
    "Fantasy": NC_("bgg", "Fantasy"),
    "Farming": NC_("bgg", "Farming"),
    "Fighting": NC_("bgg", "Fighting"),
    "Game System": NC_("bgg", "Game System"),
    "Horror": NC_("bgg", "Horror"),
    "Humor": NC_("bgg", "Humor"),
    "Industry / Manufacturing": NC_("bgg", "Industry"),
    "Korean War": NC_("bgg", "Korean War"),
    "Mafia": NC_("bgg", "Mafia"),
    "Math": NC_("bgg", "Math"),
    "Mature / Adult": NC_("bgg", "Adults Only"),
    "Maze": NC_("bgg", "Maze"),
    "Medical": NC_("bgg", "Medical"),
    "Medieval": NC_("bgg", "Medieval"),
    "Memory": NC_("bgg", "Memory"),
    "Miniatures": NC_("bgg", "Miniatures"),
    "Modern Warfare": NC_("bgg", "Modern Warfare"),
    "Movies / TV / Radio theme": NC_("bgg", "Movies & TV"),
    "Murder/Mystery": NC_("bgg", "Mystery"),
    "Music": NC_("bgg", "Music"),
    "Mythology": NC_("bgg", "Mythology"),
    "Napoleonic": NC_("bgg", "Napoleonic"),
    "Nautical": NC_("bgg", "Nautical"),
    "Negotiation": NC_("bgg", "Negotiation"),
    "Novel-based": NC_("bgg", "Based on a Novel"),
    "Number": NC_("bgg", "Numbers"),
    "Party Game": NC_("bgg", "Party Game"),
    "Pike and Shot": NC_("bgg", "Pike and Shot"),
    "Pirates": NC_("bgg", "Pirates"),
    "Political": NC_("bgg", "Political"),
    "Post-Napoleonic": NC_("bgg", "Post-Napoleonic"),
    "Prehistoric": NC_("bgg", "Prehistoric"),
    "Print & Play": NC_("bgg", "Print & Play"),
    "Puzzle": NC_("bgg", "Puzzle"),
    "Racing": NC_("bgg", "Racing"),
    "Real-time": NC_("bgg", "Real-time"),
    "Religious": NC_("bgg", "Religious"),
    "Renaissance": NC_("bgg", "Renaissance"),
    "Science Fiction": NC_("bgg", "Science Fiction"),
    "Space Exploration": NC_("bgg", "Space"),
    "Spies/Secret Agents": NC_("bgg", "Spies"),
    "Sports": NC_("bgg", "Sports"),
    "Territory Building": NC_("bgg", "Territory Building"),
    "Trains": NC_("bgg", "Trains"),
    "Transportation": NC_("bgg", "Transportation"),
    "Travel": NC_("bgg", "Travel"),
    "Trivia": NC_("bgg", "Trivia"),
    "Video Game Theme": NC_("bgg", "Video Game Theme"),
    "Vietnam War": NC_("bgg", "Vietnam War"),
    "Wargame": NC_("bgg", "Wargame"),
    "Word Game": NC_("bgg", "Word Game"),
    "World War I": NC_("bgg", "World War I"),
    "World War II": NC_("bgg", "World War II"),
    "Zombies": NC_("bgg", "Zombies"),
}

#: The few BGG mechanics a player picks a game by.
MECHANIC_TAGS: dict[str, str] = {
    "Cooperative Game": NC_("bgg", "Cooperative"),
    "Solo / Solitaire Game": NC_("bgg", "Solo"),
    "Team-Based Game": NC_("bgg", "Teams"),
    "Deck, Bag, and Pool Building": NC_("bgg", "Deck Building"),
    "Worker Placement": NC_("bgg", "Worker Placement"),
    "Tile Placement": NC_("bgg", "Tile Placement"),
    "Roll / Spin and Move": NC_("bgg", "Roll and Move"),
    "Trick-taking": NC_("bgg", "Trick-taking"),
    "Hidden Roles": NC_("bgg", "Hidden Roles"),
    "Legacy Game": NC_("bgg", "Legacy"),
    "Push Your Luck": NC_("bgg", "Push Your Luck"),
    "Memory": NC_("bgg", "Memory"),
    "Open Drafting": NC_("bgg", "Drafting"),
    "Closed Drafting": NC_("bgg", "Drafting"),
}

EXPANSION_TAG = NC_("bgg", "Expansion")


def tag_names(meta: dict) -> list[str]:
    """The tags a BGG metadata dict files under, translated, de-duplicated,
    in BGG's order (categories, then mechanics)."""
    english: list[str] = []
    for name in meta.get("categories") or []:
        if name in CATEGORY_TAGS:
            english.append(CATEGORY_TAGS[name])
    for name in meta.get("mechanics") or []:
        if name in MECHANIC_TAGS:
            english.append(MECHANIC_TAGS[name])
    if meta.get("kind") == "boardgameexpansion":
        english.append(EXPANSION_TAG)
    out: list[str] = []
    seen: set[str] = set()
    for tag in english:
        translated = i18n.library_pgettext(CONTEXT, tag)
        if translated.casefold() not in seen:  # tags are unique case-insensitively
            seen.add(translated.casefold())
            out.append(translated)
    return out
