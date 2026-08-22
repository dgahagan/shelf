# Photo Intake

Photograph a shelf; a vision model reads the spines; you review the list and
import. Everything confirmed goes through the normal metadata and cover
pipeline, so the results look exactly like scanned books.

## Setup

Settings → Integrations → **Photo Intake (Vision)**. Choose a provider:

| Provider | Accuracy | Cost | Privacy |
|---|---|---|---|
| **Anthropic API** | Best | Pay per photo, typically a few cents | Photo sent to Anthropic |
| **OpenAI-compatible** | Depends on model | Depends on host — OpenAI / OpenRouter bill per use; vLLM, LM Studio, LocalAI are free and local | Depends on where the endpoint runs |
| **Ollama** | Depends on model (`qwen2.5vl`, `gemma3`, `llama3.2-vision` …) | Free | Fully local |

For Anthropic enter an API key and pick a model. For OpenAI-compatible enter
the base URL, an optional key, and a vision-capable model name. For Ollama
enter the server URL and model. Both OpenAI-compatible and Ollama have an
"ingest long edge" field — the resolution the model actually sees — which
drives the tiling decision below; the defaults match common models.

The **Photo Intake** nav tab appears once a provider is saved.

## Using it

1. Open **Photo Intake**, choose a photo (on phones the camera opens
   directly; see [#28](https://github.com/dgahagan/shelf/issues/28) for the
   planned camera/library choice).
2. If the photo is much larger than the model will ingest, Shelf shows a
   **"what the model will see"** preview and offers to split it into
   overlapping tiles, with a cost estimate for each choice. More tiles = more
   legible spines = more tokens. Pick one.
3. Wait for analysis. Detected books appear as an editable list — title and
   author per row. Fix typos, delete false positives, add anything the model
   missed.
4. **Confirm.** Each row is looked up by title + author (an author-match
   guard rejects wrong editions), inserted with metadata, and covers are
   fetched in the background.

Nothing is imported until you confirm, and the photo itself is never stored.

## Getting good results

- **Light and angle.** Straight-on, even light, spines filling the frame.
  Glare and a 30° angle are the two biggest accuracy killers.
- **Resolution matters more than megapixels suggest.** Models downscale;
  a 12 MP phone photo of a 1.5 m shelf leaves each spine a few pixels wide
  after downscaling. That is exactly when the tiling offer appears — accept
  it.
- **One shelf per photo** beats one bookcase per photo.
- **Face-up stacks work too** (kids' books, manuals): the model reads covers
  as well as spines.
- Local models are hit-and-miss on thin spines; a cloud model is worth the
  cents for a big backlog, then switch back.

## What it costs

The preview step estimates tokens and dollars from the tile count and
expected book density before anything is sent. Output tokens scale with the
number of books detected, not with tiles, so a dense shelf costs more than a
sparse one at the same resolution. Ollama is free regardless.

## Limitations

- Media type defaults to **book** for every row; change comics or discs on
  the item page afterwards. (A per-row type picker is on the
  [roadmap](https://github.com/dgahagan/shelf/issues).)
- No barcode means lookup is by title/author, so obscure editions may land as
  a different printing. The ISBN is filled in when the lookup finds one.
- Handwriting, foreign scripts and heavily stylised spines are where models
  still fail; expect to fix a few rows.
