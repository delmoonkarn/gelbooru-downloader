# Gelbooru Downloader 

A polite, rate-limited desktop tool to archive Gelbooru posts (favorites or any tag search). Each saved image can include embedded metadata (tags, artists, post URL, post date) so it remains searchable in Windows Explorer, Lightroom, and similar apps.

## Main features
- Authenticated search (requires Gelbooru user_id + api_key).
- Two modes: Favorites-only or general tag search.
- Saves only JPG/JPEG/PNG (skips GIF/MP4/WebM).
- Optional PNG → JPEG conversion for Windows tag support.
- Embeds tags, artists, copyright, source URL, and post date into saved images.
- Conservative rate-limiting, retries, and basic backoff on errors.
- Simple persisted preferences: `~/.gelbooru_downloader.json` (user id, api key, folder, toggles).
- Tag-type cache stored at `~/.gelbooru_tag_types.json`.

## Requirements
- Python 3.10+
- Install deps: pip install -r requirements.txt

## Quick start (Windows)
- Double-click `run.bat` (creates venv and runs the app), or:
```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## Get Gelbooru credentials
1. Sign in at https://gelbooru.com/.
2. My Account → Options → API Access Credentials → Generate.
3. Paste the shown `user_id` and `api_key` into the app.

## How to use
1. Enter User ID and API Key.
2. Choose a save folder.
3. Toggle options (Favorites-only, Embed metadata, Convert PNG→JPG).
4. (Optional) Enter search tags and set Max posts.
5. Click Start Download. Click Stop to cancel.

## Files in repo
- main.py — app (GUI + worker)
- requirements.txt
- run.bat — Windows launcher

## License & disclaimer
For personal archival use only. Respect Gelbooru’s terms and rate limits.
