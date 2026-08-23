# Photo Intake

Photograph books — on a shelf, in a stack, or laid face-up. A vision model
reads the spines it can read and **recognizes** the covers it can't; you
review the list and import. Rows the model recognized rather than read are
marked so you can give them a second look. Confirmed rows are looked up by
the printed ISBN when one was in frame, otherwise by title and author, and
the Done panel tells you which rows found no metadata at all.

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
sets the size Shelf uploads when you send a photo as-is, and drives the
tiling decision below; the defaults match common models.

The **Photo Intake** nav tab appears once a provider is saved.

## Using it

1. Open **Photo Intake**. **Take photo** opens the camera — on a phone that's
   the native camera app, handing back a full-resolution still; on a desktop
   with a webcam it's an in-page viewfinder with **Capture** and **Cancel**
   (this path needs the HTTPS certificate trusted — see
   [HTTPS & reverse proxy](../https-and-reverse-proxy.md)). Where no camera
   is available the button doesn't appear, or on a desktop with no camera
   attached, clicking it says "No camera found. Use Choose photo instead."
   **Choose photo** opens the phone's photo library or a file on disk. Both
   paths feed the same pipeline below.
2. If the photo is much larger than the model will ingest, Shelf shows a
   **"what the model will see"** preview and offers to split it into
   overlapping tiles, with a cost estimate for each choice. More tiles = more
   legible spines = more tokens. Pick one, or choose **Send as-is**. Send
   as-is — and a plain **Read Photo**, when the photo is over the model's
   ingest size but not by enough to trigger the tiling offer — sends a copy
   resized to that same preview size: the preview canvas is drawn from the
   identical resample, and the upload is that image JPEG-encoded (the model
   may still resize it slightly further on ingest). Once the plan is known,
   nothing larger than the model's ingest size is uploaded, so the upload is
   smaller and faster too.
3. Wait for analysis. Detected books appear as an editable list. Each row
   carries a title, an author, an **ISBN** (pre-filled when the model read
   one off a back cover, and editable), and a **media type** picker — it
   defaults to Book, so set Comic, DVD or the rest per row before you
   confirm. A **recognized** badge marks rows the model identified from the
   cover art rather than read; check those. Fix typos, delete false
   positives, add anything the model missed.
4. **Confirm.** A row with a valid ISBN goes through the full ISBN lookup —
   the same cascade as scanning a barcode, so publisher, language, series
   and description come with it. A row without one falls back to the title +
   author search (an author-match guard rejects wrong editions). Either way
   covers are fetched in the background, and the Done panel flags any row
   that was added with no metadata match.

Nothing is imported until you confirm, and the photo itself is never stored.

## Getting good results

- **Light and angle.** Straight-on, even light, spines filling the frame.
  Glare and a 30° angle are the two biggest accuracy killers.
- **Resolution matters more than megapixels suggest.** Models downscale;
  a 12 MP phone photo of a 1.5 m shelf leaves each spine a few pixels wide
  after downscaling. That is exactly when the tiling offer appears — accept
  it. For Ollama and OpenAI-compatible providers, the **Image size**
  setting now sets the size Shelf uploads as-is as well as when tiling is
  offered — raise it for a model that reads larger images natively.
- **Low-resolution advisory.** When a photo is small rather than oversized —
  a desktop webcam frame, or a library photo a messaging app re-compressed —
  Shelf shows "This photo may be too small to read" with **Take another
  photo** / **Choose another photo** buttons. It's advisory, not a gate:
  Read Photo stays enabled and analysis still runs either way. A native
  phone photo is the best input and essentially never trips this.
- **One shelf per photo** beats one bookcase per photo.
- **Face-up works, front cover showing.** Lay thin or barcode-less books —
  kids' picture books, vintage manuals — cover up. The model recognizes
  cover art as well as reading it, so these get a row where a spine photo
  would give you nothing. If the back cover with the barcode happens to be
  the side in frame, the printed ISBN gives you exact-edition metadata — but
  never turn a book barcode-side up on purpose; the cover is what the model
  is best at.
- Local models are hit-and-miss on thin spines; a cloud model is worth the
  cents for a big backlog, then switch back.

## What it costs

The preview step estimates tokens and dollars from the tile count and
expected book density before anything is sent. Output tokens scale with the
number of books detected, not with tiles, so a dense shelf costs more than a
sparse one at the same resolution. Ollama is free regardless.

## Limitations

- A **recognized** row is the model's identification, not a reading. It is
  usually right and occasionally confidently wrong — that badge is there so
  you check before confirming.
- An ISBN the model misreads is checksum-validated and dropped to blank
  rather than guessed at, so a bad row costs you the enrichment, not a wrong
  book. The model is never asked to recall an ISBN from memory.
- A cover carrying **no text at all** is the hardest case, and often produces
  no row rather than a recognized one. Recognition leans on a printed title or
  byline to anchor itself; wholly textless art may be skipped.
- Local models recognize noticeably fewer covers than cloud ones, and
  mis-recognize more.
- Discs and games get classified, not looked up — setting a row to DVD keeps
  it out of the book catalogue, but there is no TMDb/IGDB lookup from intake
  rows yet.
- Handwriting, foreign scripts and heavily stylised spines are where models
  still fail; expect to fix a few rows.
