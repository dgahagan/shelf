import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DATABASE_PATH = DATA_DIR / "shelf.db"
COVERS_DIR = DATA_DIR / "covers"

MEDIA_TYPES = {
    "book": "Book",
    "kids_book": "Kids Book",
    "audiobook": "Audiobook",
    "ebook": "eBook",
    "dvd": "DVD / Blu-ray",
    "cd": "CD",
    "comic": "Comic / Graphic Novel",
}

OPENLIBRARY_RATE_LIMIT = 0.34  # seconds between requests (~3/sec)
HARDCOVER_RATE_LIMIT = 1.0  # seconds between requests (60/min API limit)
