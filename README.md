# Gelbooru Downloader

A polite, rate-limited desktop tool for archiving your Gelbooru favorites or
the results of an arbitrary tag search. Every downloaded file is enriched
with embedded metadata — categorized tags, the originating post URL, and the
original posted date — so it stays browseable in Windows Explorer, Lightroom,
digiKam, and the companion [Gelbooru Reader](https://github.com/) app.

## What it does

- **Authenticated search** against `gelbooru.com/index.php?page=dapi`. User ID
  and API key are required (Gelbooru returns HTTP 401 for anonymous JSON
  requests since 2024).
- **Two modes**, selectable per run:
  - *Favorites only* — prepends `fav:<user_id>` to your search.
  - *General search* — any tag combination you type.
- **ID-based pagination** with a forced `sort:id` metatag so the walker keeps
  advancing past Gelbooru's 20,000-result `pid` ceiling.
- **File filter** — only `.jpg`, `.jpeg`, and `.png`. GIF, MP4, and WebM
  posts are reported as `[SKIP]` and never written to disk.
- **Optional PNG → JPEG conversion** (white-flattened, quality 95,
  4:4:4 chroma). PNG has no IPTC standard, so converting is the only way
  Windows Explorer's "Tags" column ever populates for those posts.
- **Rich embedded metadata** per saved JPEG via [pyexiv2](https://pypi.org/project/pyexiv2/):
  - `Xmp.dc.subject` + `Iptc.Application2.Keywords` — flat list of every tag.
  - `Xmp.dc.creator` + `Iptc.Application2.Byline` — artists.
  - `Xmp.dc.rights` + `Iptc.Application2.CopyrightNotice` — copyrights.
  - `Xmp.lr.hierarchicalSubject` — `Artist|x`, `Character|x`, `Copyright|x`,
    `Meta|x` entries for Lightroom/digiKam category browsing.
  - `Xmp.dc.source` — link back to the original Gelbooru post.
  - `Xmp.xmp.CreateDate`, `Exif.Photo.DateTimeOriginal`,
    `Iptc.Application2.DateCreated` + `TimeCreated` — the post's
    `created_at`, so Explorer's "Date taken" column populates.
- **Tag-type lookup cache** — categories are resolved via a separate
  `dapi&s=tag` query (batched 80 names per request) and cached at
  `~/.gelbooru_tag_types.json` so repeat runs hit the cache.
- **Post-count limit** — optionally cap a run at the top *N* posts in the
  result set (e.g. "20 latest favorites").
- **Anti-ban**:
  - Hard-coded `User-Agent: GelbooruFavDownloader/1.0`.
  - 1.5 s between API requests, 2.5 s between image downloads.
  - 60-second backoff on HTTP 401 / 429.
  - 5-retry cap on network/DNS failures with a clear log message.
- **Network resilience**:
  - `timeout=10` on every request.
  - `stream=True` + `iter_content(64 KiB)` so large files never balloon RAM.
  - Atomic write via `*.part → os.replace` so a crash mid-download can't
    corrupt an existing file.
  - **DNS-over-HTTPS fallback** — if the system resolver returns NXDOMAIN
    for `*.gelbooru.com` (common on some ISPs), the app falls back to
    Google's DoH endpoint at `https://8.8.8.8/resolve`. TLS SNI stays
    on the original hostname so certificate verification works normally.
- **File-system safety**:
  - Filenames are sanitized against `< > : " / \ | ? *` plus control chars.
  - Destination directory is checked for existence and writability before
    the first request.
  - Already-downloaded files are detected and skipped (including the
    converted-JPEG sibling of a previously-saved PNG).
- **Persisted preferences** at `~/.gelbooru_downloader.json` — user ID,
  API key, download folder, favorites toggle, metadata toggle, PNG→JPEG
  toggle, max-posts cap.
- **Thread-safe GUI** — every log/status update from the worker thread
  is dispatched via `Tk.after(0, ...)` to avoid Tkinter cross-thread
  crashes.

## Tech stack

| Layer | Library |
| --- | --- |
| GUI | [customtkinter](https://pypi.org/project/customtkinter/) |
| HTTP | [requests](https://pypi.org/project/requests/) |
| Concurrency | `threading` + `threading.Event` for cooperative cancellation |
| Image re-encode | [Pillow](https://pypi.org/project/Pillow/) |
| EXIF / IPTC / XMP write | [pyexiv2](https://pypi.org/project/pyexiv2/) |
| Persistence | `json` files under the user home directory |

Python 3.10 or newer.

## Getting started

### Windows (one-click)

Double-click `run.bat`. On first launch it creates a virtual environment
under `venv/`, installs the dependencies in `requirements.txt`, and starts
the GUI. Subsequent launches skip the install step if the venv already has
the required imports.

### Manual

```bash
python -m venv venv
venv\Scripts\activate           # PowerShell:  venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

### Getting Gelbooru credentials

1. Sign in to <https://gelbooru.com/>.
2. Go to **My Account → Options**, scroll to "API Access Credentials".
3. Click **Generate**. The page shows a URL-style snippet like
   `&api_key=…&user_id=…`. Paste either field into the matching input —
   the app auto-extracts the value from a pasted snippet.

## Using it

1. Fill in **User ID** and **API Key**.
2. Choose a **Save To** folder.
3. Toggle the options you want:
   - *Favorites only* — search inside `fav:<your_id>`.
   - *Embed tags into file metadata (XMP/IPTC)* — recommended.
   - *Convert PNG → JPG (for Windows Explorer tag support)* — recommended.
4. Optionally type **Search Tags** (space-separated, Gelbooru syntax).
5. Optionally set **Max posts** to cap the run.
6. Click **Start Download**. Watch the log; click **Stop** any time.

## Files

```
main.py            Single-file app (GUI + worker + metadata writer + DoH)
requirements.txt   Pinned-minimum dependencies
run.bat            Windows launcher: provisions venv, runs main.py
```

User-level config is written to `~/.gelbooru_downloader.json` and the tag
category cache to `~/.gelbooru_tag_types.json`. Neither file is tracked by
this repository.

## License & disclaimer

This tool is for personal archival of content you have legitimate access to.
Respect Gelbooru's terms of service and rate limits. The defaults are
deliberately conservative (~0.4 image requests per second, well under the
documented 10 req/s ceiling); please don't lower them.
