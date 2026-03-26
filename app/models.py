from pydantic import BaseModel


class ScanRequest(BaseModel):
    isbn: str
    media_type: str = "book"
    location_id: int | None = None


class ItemCreate(BaseModel):
    title: str
    subtitle: str | None = None
    authors: str | None = None
    isbn: str | None = None
    isbn10: str | None = None
    media_type: str = "book"
    publisher: str | None = None
    publish_year: int | None = None
    page_count: int | None = None
    description: str | None = None
    series_name: str | None = None
    series_position: float | None = None
    narrator: str | None = None
    duration_mins: int | None = None
    location_id: int | None = None
    notes: str | None = None


class ItemUpdate(ItemCreate):
    title: str | None = None
    media_type: str | None = None


class LocationCreate(BaseModel):
    name: str
    sort_order: int = 0


class SettingsUpdate(BaseModel):
    key: str
    value: str
