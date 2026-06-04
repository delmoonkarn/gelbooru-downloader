import json
import os
import re
import socket
import time
import threading
import urllib.parse
from datetime import datetime
from tkinter import filedialog

import customtkinter as ctk
import requests

try:
    import pyexiv2  # type: ignore
    HAS_PYEXIV2 = True
    _PYEXIV2_ERR = ""
except Exception as _e:  # ImportError or DLL load error on Windows
    pyexiv2 = None
    HAS_PYEXIV2 = False
    _PYEXIV2_ERR = str(_e)

try:
    from PIL import Image  # type: ignore
    HAS_PIL = True
    _PIL_ERR = ""
except Exception as _e:
    Image = None  # type: ignore
    HAS_PIL = False
    _PIL_ERR = str(_e)


CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".gelbooru_downloader.json")
TAG_TYPE_CACHE_PATH = os.path.join(
    os.path.expanduser("~"), ".gelbooru_tag_types.json"
)

# Gelbooru tag type numeric codes (from the dapi tag endpoint).
TAG_TYPE_GENERAL = 0
TAG_TYPE_ARTIST = 1
TAG_TYPE_COPYRIGHT = 3
TAG_TYPE_CHARACTER = 4
TAG_TYPE_METADATA = 5
TAG_TYPE_DEPRECATED = 6


def load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(data: dict) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


def load_tag_type_cache() -> dict:
    try:
        with open(TAG_TYPE_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_tag_type_cache(data: dict) -> None:
    try:
        with open(TAG_TYPE_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        pass


def parse_gelbooru_date(s):
    """Parse Gelbooru's 'Sun Oct 25 06:30:28 -0500 2020' created_at format."""
    if not s or not isinstance(s, str):
        return None
    try:
        return datetime.strptime(s.strip(), "%a %b %d %H:%M:%S %z %Y")
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


API_BASE = "https://gelbooru.com/index.php"
USER_AGENT = "GelbooruFavDownloader/1.0"
ALLOWED_EXT = (".jpg", ".jpeg", ".png")
SKIP_EXT = (".gif", ".mp4", ".webm")
ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
MAX_NET_RETRIES = 5

# DNS-over-HTTPS fallback for ISPs that block gelbooru.com via DNS.
# Google's DoH endpoint is reachable directly by IP (cert covers 8.8.8.8).
DOH_URL = "https://8.8.8.8/resolve"
_DNS_CACHE: dict[str, str] = {}
_orig_getaddrinfo = socket.getaddrinfo


def _doh_lookup(host: str) -> str | None:
    try:
        r = requests.get(
            DOH_URL,
            params={"name": host, "type": "A"},
            headers={"Accept": "application/dns-json"},
            timeout=8,
        )
        if r.status_code != 200:
            return None
        for ans in r.json().get("Answer", []):
            if ans.get("type") == 1 and ans.get("data"):
                return ans["data"]
    except Exception:
        return None
    return None


def _patched_getaddrinfo(host, *args, **kwargs):
    if isinstance(host, str) and (
        host == "gelbooru.com" or host.endswith(".gelbooru.com")
    ):
        try:
            return _orig_getaddrinfo(host, *args, **kwargs)
        except socket.gaierror:
            ip = _DNS_CACHE.get(host) or _doh_lookup(host)
            if ip:
                _DNS_CACHE[host] = ip
                return _orig_getaddrinfo(ip, *args, **kwargs)
            raise
    return _orig_getaddrinfo(host, *args, **kwargs)


socket.getaddrinfo = _patched_getaddrinfo


def extract_param(text: str, key: str) -> str:
    """Pull a query-param value out of a pasted snippet, else return text as-is."""
    text = text.strip().lstrip("&?")
    if f"{key}=" in text:
        try:
            parsed = urllib.parse.parse_qs(text, keep_blank_values=False)
            if key in parsed and parsed[key]:
                return parsed[key][0].strip()
        except Exception:
            pass
    return text


def sanitize_filename(name: str) -> str:
    name = urllib.parse.unquote(name)
    name = ILLEGAL_CHARS.sub("_", name)
    name = name.strip(" .")
    if not name:
        name = "untitled"
    if len(name) > 200:
        root, ext = os.path.splitext(name)
        name = root[: 200 - len(ext)] + ext
    return name


class GelbooruDownloader(ctk.CTk):
    def __init__(self):
        super().__init__()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title("Gelbooru Downloader")
        self.geometry("780x680")
        self.minsize(680, 600)

        self._config = load_config()
        self.download_dir = ctk.StringVar(value=self._config.get("download_dir", ""))
        self.favorites_only = ctk.BooleanVar(
            value=self._config.get("favorites_only", True)
        )
        self.write_metadata = ctk.BooleanVar(
            value=self._config.get("write_metadata", True)
        )
        self.convert_png_to_jpg = ctk.BooleanVar(
            value=self._config.get("convert_png_to_jpg", True)
        )
        self.is_running = False
        self.stop_flag = threading.Event()
        self._warned_no_pyexiv2 = False
        self._warned_no_pil = False
        self._tag_types: dict = load_tag_type_cache()

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(7, weight=1)

        header = ctk.CTkLabel(
            self,
            text="Gelbooru Downloader",
            font=ctk.CTkFont(size=20, weight="bold"),
        )
        header.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="w")

        creds_frame = ctk.CTkFrame(self)
        creds_frame.grid(row=1, column=0, padx=20, pady=8, sticky="ew")
        creds_frame.grid_columnconfigure(1, weight=1)
        creds_frame.grid_columnconfigure(3, weight=1)

        ctk.CTkLabel(creds_frame, text="User ID:").grid(
            row=0, column=0, padx=(12, 6), pady=10, sticky="w"
        )
        self.user_id_entry = ctk.CTkEntry(
            creds_frame, placeholder_text="required, e.g. 123456"
        )
        self.user_id_entry.grid(row=0, column=1, padx=(0, 12), pady=10, sticky="ew")
        if self._config.get("user_id"):
            self.user_id_entry.insert(0, self._config["user_id"])

        ctk.CTkLabel(creds_frame, text="API Key:").grid(
            row=0, column=2, padx=(12, 6), pady=10, sticky="w"
        )
        self.api_key_entry = ctk.CTkEntry(
            creds_frame, placeholder_text="required", show="*"
        )
        self.api_key_entry.grid(row=0, column=3, padx=(0, 12), pady=10, sticky="ew")
        if self._config.get("api_key"):
            self.api_key_entry.insert(0, self._config["api_key"])

        opts_frame = ctk.CTkFrame(self, fg_color="transparent")
        opts_frame.grid(row=2, column=0, padx=22, pady=(2, 4), sticky="ew")
        self.fav_checkbox = ctk.CTkCheckBox(
            opts_frame,
            text="Favorites only (prepend fav:user_id)",
            variable=self.favorites_only,
        )
        self.fav_checkbox.grid(row=0, column=0, sticky="w")
        self.meta_checkbox = ctk.CTkCheckBox(
            opts_frame,
            text="Embed tags into file metadata (XMP/IPTC)",
            variable=self.write_metadata,
        )
        self.meta_checkbox.grid(row=0, column=1, padx=(24, 0), sticky="w")
        self.convert_checkbox = ctk.CTkCheckBox(
            opts_frame,
            text="Convert PNG → JPG (for Windows Explorer tag support)",
            variable=self.convert_png_to_jpg,
        )
        self.convert_checkbox.grid(row=0, column=2, padx=(24, 0), sticky="w")

        tags_frame = ctk.CTkFrame(self)
        tags_frame.grid(row=3, column=0, padx=20, pady=8, sticky="ew")
        tags_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(tags_frame, text="Search Tags:").grid(
            row=0, column=0, padx=(12, 6), pady=10, sticky="w"
        )
        self.tags_entry = ctk.CTkEntry(
            tags_frame, placeholder_text="e.g. solo blue_eyes  (space-separated)"
        )
        self.tags_entry.grid(row=0, column=1, padx=(0, 12), pady=10, sticky="ew")

        ctk.CTkLabel(tags_frame, text="Max posts:").grid(
            row=0, column=2, padx=(0, 6), pady=10, sticky="w"
        )
        self.max_posts_entry = ctk.CTkEntry(
            tags_frame, placeholder_text="0 = all", width=90
        )
        self.max_posts_entry.grid(row=0, column=3, padx=(0, 12), pady=10, sticky="w")
        saved_max = self._config.get("max_posts")
        if saved_max not in (None, "", 0, "0"):
            self.max_posts_entry.insert(0, str(saved_max))

        dir_frame = ctk.CTkFrame(self)
        dir_frame.grid(row=4, column=0, padx=20, pady=8, sticky="ew")
        dir_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(dir_frame, text="Save To:").grid(
            row=0, column=0, padx=(12, 6), pady=10, sticky="w"
        )
        self.dir_entry = ctk.CTkEntry(
            dir_frame, textvariable=self.download_dir, placeholder_text="Choose a folder..."
        )
        self.dir_entry.grid(row=0, column=1, padx=(0, 6), pady=10, sticky="ew")
        self.browse_btn = ctk.CTkButton(
            dir_frame, text="Browse", width=100, command=self._browse_dir
        )
        self.browse_btn.grid(row=0, column=2, padx=(0, 12), pady=10)

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=5, column=0, padx=20, pady=(4, 8), sticky="ew")
        btn_frame.grid_columnconfigure(0, weight=1)
        btn_frame.grid_columnconfigure(1, weight=1)

        self.start_btn = ctk.CTkButton(
            btn_frame, text="Start Download", height=40, command=self._start_download
        )
        self.start_btn.grid(row=0, column=0, padx=(0, 6), sticky="ew")

        self.stop_btn = ctk.CTkButton(
            btn_frame,
            text="Stop",
            height=40,
            fg_color="#a13030",
            hover_color="#7a2424",
            command=self._stop_download,
            state="disabled",
        )
        self.stop_btn.grid(row=0, column=1, padx=(6, 0), sticky="ew")

        self.status_label = ctk.CTkLabel(self, text="Idle.", anchor="w")
        self.status_label.grid(row=6, column=0, padx=22, pady=(2, 4), sticky="ew")

        self.log_box = ctk.CTkTextbox(self, wrap="word", font=ctk.CTkFont(family="Consolas", size=12))
        self.log_box.grid(row=7, column=0, padx=20, pady=(4, 20), sticky="nsew")
        self.log_box.configure(state="disabled")

    def _browse_dir(self):
        path = filedialog.askdirectory(title="Select download folder")
        if path:
            self.download_dir.set(path)

    def _log(self, message: str):
        def append():
            self.log_box.configure(state="normal")
            self.log_box.insert("end", message + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")

        self.after(0, append)

    def _set_status(self, text: str):
        self.after(0, lambda: self.status_label.configure(text=text))

    def _set_running(self, running: bool):
        def apply():
            self.is_running = running
            state_input = "disabled" if running else "normal"
            self.user_id_entry.configure(state=state_input)
            self.api_key_entry.configure(state=state_input)
            self.tags_entry.configure(state=state_input)
            self.dir_entry.configure(state=state_input)
            self.browse_btn.configure(state=state_input)
            self.start_btn.configure(state="disabled" if running else "normal")
            self.stop_btn.configure(state="normal" if running else "disabled")

        self.after(0, apply)

    def _start_download(self):
        if self.is_running:
            return

        user_id = extract_param(self.user_id_entry.get(), "user_id")
        api_key = extract_param(self.api_key_entry.get(), "api_key")
        extra_tags = self.tags_entry.get().strip()
        out_dir = self.download_dir.get().strip()
        favorites_only = bool(self.favorites_only.get())

        if not user_id or not api_key:
            self._log(
                "[ERROR] User ID and API Key are required - Gelbooru's API "
                "rejects anonymous requests (HTTP 401)."
            )
            return
        if not user_id.isdigit():
            self._log("[ERROR] User ID must be numeric.")
            return
        if not favorites_only and not extra_tags:
            self._log(
                "[ERROR] No search tags. Enter at least one tag "
                "(or enable 'Favorites only')."
            )
            return
        if not out_dir:
            self._log("[ERROR] Please choose a download directory.")
            return
        if not os.path.isdir(out_dir):
            self._log(f"[ERROR] Directory does not exist: {out_dir}")
            return
        if not os.access(out_dir, os.W_OK):
            self._log(f"[ERROR] No write permission for: {out_dir}")
            return

        write_meta = bool(self.write_metadata.get())
        convert_png = bool(self.convert_png_to_jpg.get())

        max_posts_raw = self.max_posts_entry.get().strip()
        if max_posts_raw:
            try:
                max_posts = int(max_posts_raw)
            except ValueError:
                self._log("[ERROR] Max posts must be a whole number.")
                return
            if max_posts < 0:
                self._log("[ERROR] Max posts must be 0 or greater.")
                return
        else:
            max_posts = 0

        save_config({
            "user_id": user_id,
            "api_key": api_key,
            "download_dir": out_dir,
            "favorites_only": favorites_only,
            "write_metadata": write_meta,
            "convert_png_to_jpg": convert_png,
            "max_posts": max_posts,
        })

        self.stop_flag.clear()
        self._set_running(True)
        self._log("=" * 60)
        self._log(f"Starting download into: {out_dir}")

        thread = threading.Thread(
            target=self._download_worker,
            args=(
                user_id,
                api_key,
                extra_tags,
                out_dir,
                favorites_only,
                write_meta,
                convert_png,
                max_posts,
            ),
            daemon=True,
        )
        thread.start()

    def _on_close(self):
        max_posts_raw = self.max_posts_entry.get().strip()
        try:
            max_posts = int(max_posts_raw) if max_posts_raw else 0
        except ValueError:
            max_posts = 0
        save_config({
            "user_id": extract_param(self.user_id_entry.get(), "user_id"),
            "api_key": extract_param(self.api_key_entry.get(), "api_key"),
            "download_dir": self.download_dir.get().strip(),
            "favorites_only": bool(self.favorites_only.get()),
            "write_metadata": bool(self.write_metadata.get()),
            "convert_png_to_jpg": bool(self.convert_png_to_jpg.get()),
            "max_posts": max(0, max_posts),
        })
        self.stop_flag.set()
        self.destroy()

    def _stop_download(self):
        if self.is_running:
            self.stop_flag.set()
            self._log("[*] Stop requested - finishing current request...")
            self._set_status("Stopping...")

    def _convert_png_to_jpg(self, png_path: str) -> str:
        """Re-encode a PNG to JPG (white-flattened) in place. Returns the new path,
        or the original path if Pillow is unavailable or conversion fails."""
        if not HAS_PIL:
            if not self._warned_no_pil:
                self._log(
                    f"[WARN] PNG→JPG conversion requested but Pillow isn't "
                    f"available ({_PIL_ERR}). Keeping PNG files as-is."
                )
                self._warned_no_pil = True
            return png_path

        jpg_path = os.path.splitext(png_path)[0] + ".jpg"
        try:
            with Image.open(png_path) as img:
                img.load()
                if img.mode in ("RGBA", "LA") or (
                    img.mode == "P" and "transparency" in img.info
                ):
                    rgba = img.convert("RGBA")
                    flat = Image.new("RGB", rgba.size, (255, 255, 255))
                    flat.paste(rgba, mask=rgba.split()[-1])
                    out = flat
                else:
                    out = img.convert("RGB")
                out.save(
                    jpg_path,
                    "JPEG",
                    quality=95,
                    subsampling=0,
                    optimize=True,
                )
        except Exception as e:
            self._log(
                f"[WARN] PNG→JPG failed for {os.path.basename(png_path)}: {e}"
            )
            try:
                if os.path.exists(jpg_path):
                    os.remove(jpg_path)
            except OSError:
                pass
            return png_path

        try:
            os.remove(png_path)
        except OSError as e:
            self._log(
                f"[WARN] Could not remove original PNG "
                f"{os.path.basename(png_path)}: {e}"
            )
        return jpg_path

    def _write_metadata(self, path: str, post: dict) -> None:
        if not HAS_PYEXIV2:
            return
        try:
            tags_str = (post.get("tags") or "").strip()
            tags = [t for t in tags_str.split() if t]
            if not tags:
                return

            post_id = post.get("id")
            rating = (post.get("rating") or "").strip()
            source = (post.get("source") or "").strip()
            post_url = (
                f"https://gelbooru.com/index.php?page=post&s=view&id={post_id}"
                if post_id
                else ""
            )
            posted_dt = parse_gelbooru_date(post.get("created_at"))

            artists, characters, copyrights, metas, _gen = self._categorize_tags(tags)

            # Lightroom-style hierarchical subjects (digiKam, Bridge, LR all read this).
            hier = []
            hier.extend(f"Artist|{t}" for t in artists)
            hier.extend(f"Character|{t}" for t in characters)
            hier.extend(f"Copyright|{t}" for t in copyrights)
            hier.extend(f"Meta|{t}" for t in metas)

            xmp_data = {"Xmp.dc.subject": tags}
            if artists:
                xmp_data["Xmp.dc.creator"] = artists
            if copyrights:
                xmp_data["Xmp.dc.rights"] = {"x-default": ", ".join(copyrights)}
            if hier:
                xmp_data["Xmp.lr.hierarchicalSubject"] = hier
            if post_url:
                xmp_data["Xmp.dc.source"] = post_url
            desc_bits = []
            if rating:
                desc_bits.append(f"rating: {rating}")
            if source:
                desc_bits.append(f"artist source: {source}")
            if desc_bits:
                xmp_data["Xmp.dc.description"] = {"x-default": " | ".join(desc_bits)}
            if posted_dt:
                iso = posted_dt.isoformat()
                xmp_data["Xmp.xmp.CreateDate"] = iso
                xmp_data["Xmp.photoshop.DateCreated"] = iso

            iptc_data = {"Iptc.Application2.Keywords": tags}
            if artists:
                iptc_data["Iptc.Application2.Byline"] = artists
            if copyrights:
                iptc_data["Iptc.Application2.CopyrightNotice"] = ", ".join(copyrights)
            if posted_dt:
                iptc_data["Iptc.Application2.DateCreated"] = posted_dt.strftime(
                    "%Y%m%d"
                )
                iptc_data["Iptc.Application2.TimeCreated"] = posted_dt.strftime(
                    "%H%M%S%z"
                )

            exif_data = {}
            is_jpeg = path.lower().endswith((".jpg", ".jpeg"))
            if posted_dt and is_jpeg:
                stamp = posted_dt.strftime("%Y:%m:%d %H:%M:%S")
                exif_data["Exif.Photo.DateTimeOriginal"] = stamp
                exif_data["Exif.Photo.DateTimeDigitized"] = stamp
                exif_data["Exif.Image.DateTime"] = stamp
                offset = posted_dt.strftime("%z")
                if offset:
                    # EXIF 2.31+ wants "+HH:MM", strftime gives "+HHMM".
                    offset_fmt = offset[:3] + ":" + offset[3:]
                    exif_data["Exif.Photo.OffsetTimeOriginal"] = offset_fmt
                    exif_data["Exif.Photo.OffsetTimeDigitized"] = offset_fmt
                    exif_data["Exif.Photo.OffsetTime"] = offset_fmt

            img = pyexiv2.Image(path)
            try:
                try:
                    img.modify_xmp(xmp_data)
                except Exception as e:
                    self._log(
                        f"[WARN] XMP write failed for "
                        f"{os.path.basename(path)}: {e}"
                    )
                try:
                    img.modify_iptc(iptc_data)
                except Exception as e:
                    # PNG has no IPTC chunk — expected when convert_png is off.
                    if is_jpeg:
                        self._log(
                            f"[WARN] IPTC write failed for "
                            f"{os.path.basename(path)}: {e}"
                        )
                if exif_data:
                    try:
                        img.modify_exif(exif_data)
                    except Exception as e:
                        self._log(
                            f"[WARN] EXIF write failed for "
                            f"{os.path.basename(path)}: {e}"
                        )
            finally:
                img.close()
        except Exception as e:
            self._log(
                f"[WARN] Could not write metadata for "
                f"{os.path.basename(path)}: {e}"
            )

    def _build_tags(
        self, user_id: str, extra_tags: str, favorites_only: bool
    ) -> str:
        parts = []
        if favorites_only and user_id:
            parts.append(f"fav:{user_id}")
        if extra_tags:
            parts.append(extra_tags)
        # sort:id forces deterministic order so id-based pagination
        # is guaranteed to cover every matching post (esp. past the
        # 20,000-result pid cap).
        parts.append("sort:id")
        return " ".join(parts)

    def _fetch_tag_types(
        self,
        session: requests.Session,
        names,
        user_id: str,
        api_key: str,
    ) -> None:
        """Populate self._tag_types for any uncached tag names by querying the
        dapi tag endpoint in batches. Tags not returned by Gelbooru are cached
        as TAG_TYPE_GENERAL so we don't re-query them every run."""
        uncached = sorted({n for n in names if n and n not in self._tag_types})
        if not uncached:
            return
        CHUNK = 80
        for i in range(0, len(uncached), CHUNK):
            if self.stop_flag.is_set():
                break
            batch = uncached[i : i + CHUNK]
            params = {
                "page": "dapi",
                "s": "tag",
                "q": "index",
                "names": " ".join(batch),
                "json": "1",
                "user_id": user_id,
                "api_key": api_key,
            }
            url = (
                f"{API_BASE}?"
                f"{urllib.parse.urlencode(params, quote_via=urllib.parse.quote)}"
            )
            net_failures = 0
            done = False
            while not self.stop_flag.is_set() and not done:
                try:
                    r = session.get(url, timeout=10)
                except requests.exceptions.RequestException as e:
                    net_failures += 1
                    if net_failures >= MAX_NET_RETRIES:
                        self._log(
                            "[WARN] Tag-type lookup network failure; giving up "
                            "on this batch (tags will fall back to 'general')."
                        )
                        for n in batch:
                            self._tag_types[n] = TAG_TYPE_GENERAL
                        break
                    time.sleep(5)
                    continue

                if r.status_code in (401, 429):
                    self._log(
                        f"[WARN] HTTP {r.status_code} on tag lookup. Sleeping 60s."
                    )
                    time.sleep(60)
                    continue
                if r.status_code != 200:
                    self._log(
                        f"[WARN] HTTP {r.status_code} on tag lookup. "
                        f"Skipping batch."
                    )
                    for n in batch:
                        self._tag_types[n] = TAG_TYPE_GENERAL
                    break

                try:
                    data = r.json()
                except ValueError:
                    for n in batch:
                        self._tag_types[n] = TAG_TYPE_GENERAL
                    break

                if isinstance(data, dict):
                    tags = data.get("tag", [])
                elif isinstance(data, list):
                    tags = data
                else:
                    tags = []
                if isinstance(tags, dict):
                    tags = [tags]

                returned = set()
                for t in tags or []:
                    if not isinstance(t, dict):
                        continue
                    name = t.get("name")
                    if not name:
                        continue
                    try:
                        self._tag_types[name] = int(t.get("type", 0))
                    except (TypeError, ValueError):
                        self._tag_types[name] = TAG_TYPE_GENERAL
                    returned.add(name)
                # Anything Gelbooru didn't return → cache as general so we
                # don't re-query it on every run.
                for n in batch:
                    if n not in returned:
                        self._tag_types[n] = TAG_TYPE_GENERAL
                done = True

            time.sleep(1.5)
        save_tag_type_cache(self._tag_types)

    def _categorize_tags(self, tags):
        artists, characters, copyrights, metas, generals = [], [], [], [], []
        for t in tags:
            typ = self._tag_types.get(t, TAG_TYPE_GENERAL)
            if typ == TAG_TYPE_ARTIST:
                artists.append(t)
            elif typ == TAG_TYPE_CHARACTER:
                characters.append(t)
            elif typ == TAG_TYPE_COPYRIGHT:
                copyrights.append(t)
            elif typ == TAG_TYPE_METADATA:
                metas.append(t)
            else:
                generals.append(t)
        return artists, characters, copyrights, metas, generals

    def _fetch_page(self, session: requests.Session, params: dict) -> list | None:
        url = f"{API_BASE}?{urllib.parse.urlencode(params, quote_via=urllib.parse.quote)}"
        net_failures = 0
        while not self.stop_flag.is_set():
            try:
                resp = session.get(url, timeout=10)
            except requests.exceptions.RequestException as e:
                net_failures += 1
                short = str(e).split(":")[0]
                self._log(
                    f"[WARN] API request failed ({short}). "
                    f"Retry {net_failures}/{MAX_NET_RETRIES} in 5s."
                )
                if net_failures >= MAX_NET_RETRIES:
                    self._log(
                        "[ERROR] Network unreachable - check your internet/DNS "
                        "(could not resolve gelbooru.com)."
                    )
                    return None
                time.sleep(5)
                continue
            net_failures = 0

            if resp.status_code in (401, 429):
                self._log(
                    f"[WARN] HTTP {resp.status_code} from API. Sleeping 60s before retry."
                )
                time.sleep(60)
                continue
            if resp.status_code != 200:
                self._log(
                    f"[WARN] HTTP {resp.status_code} from API. Retrying in 5s."
                )
                time.sleep(5)
                continue

            try:
                data = resp.json()
            except ValueError:
                self._log("[ERROR] API did not return valid JSON.")
                return None

            if isinstance(data, dict):
                posts = data.get("post", [])
            elif isinstance(data, list):
                posts = data
            else:
                posts = []
            if isinstance(posts, dict):
                posts = [posts]
            return posts
        return None

    def _download_file(
        self, session: requests.Session, file_url: str, dest_path: str
    ) -> bool:
        headers = {
            "Referer": "https://gelbooru.com/",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        }
        attempts = 0
        while not self.stop_flag.is_set():
            attempts += 1
            if attempts > MAX_NET_RETRIES:
                self._log(f"[ERROR] Giving up on {file_url}")
                return False
            try:
                with session.get(
                    file_url, timeout=10, stream=True, headers=headers
                ) as r:
                    if r.status_code in (401, 429):
                        self._log(
                            f"[WARN] HTTP {r.status_code} on download. Sleeping 60s."
                        )
                        time.sleep(60)
                        continue
                    if r.status_code != 200:
                        self._log(
                            f"[WARN] HTTP {r.status_code} for {file_url}. Retry in 5s."
                        )
                        time.sleep(5)
                        continue

                    ctype = r.headers.get("Content-Type", "").lower()
                    if ctype and not ctype.startswith("image/"):
                        self._log(
                            f"[WARN] Wrong content-type '{ctype}' for "
                            f"{os.path.basename(dest_path)} - probably a "
                            f"block/error page. Skipping."
                        )
                        return False

                    tmp_path = dest_path + ".part"
                    written = 0
                    with open(tmp_path, "wb") as fh:
                        for chunk in r.iter_content(chunk_size=64 * 1024):
                            if self.stop_flag.is_set():
                                fh.close()
                                try:
                                    os.remove(tmp_path)
                                except OSError:
                                    pass
                                return False
                            if chunk:
                                fh.write(chunk)
                                written += len(chunk)

                    if written < 1024:
                        try:
                            with open(tmp_path, "rb") as fh:
                                head = fh.read(16)
                        except OSError:
                            head = b""
                        is_image = (
                            head.startswith(b"\xff\xd8\xff")  # JPEG
                            or head.startswith(b"\x89PNG\r\n\x1a\n")  # PNG
                        )
                        if not is_image:
                            try:
                                os.remove(tmp_path)
                            except OSError:
                                pass
                            self._log(
                                f"[WARN] {os.path.basename(dest_path)} was "
                                f"{written}B and not a real image - discarded."
                            )
                            return False

                    os.replace(tmp_path, dest_path)
                    size_kb = written / 1024
                    self._log(f"    ({size_kb:.1f} KB)")
                    return True
            except requests.exceptions.RequestException as e:
                short = str(e).split(":")[0]
                self._log(f"[WARN] Download failed ({short}). Retry in 5s.")
                time.sleep(5)
        return False

    def _download_worker(
        self,
        user_id: str,
        api_key: str,
        extra_tags: str,
        out_dir: str,
        favorites_only: bool,
        write_meta: bool,
        convert_png: bool,
        max_posts: int,
    ):
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})

        tags = self._build_tags(user_id, extra_tags, favorites_only)
        self._log(f"[*] Search tags: {tags or '(all)'}")
        self._log(
            f"[*] Mode: {'favorites' if favorites_only else 'general search'}"
        )
        if max_posts > 0:
            self._log(f"[*] Limit: top {max_posts} posts from the search.")
        if write_meta and not HAS_PYEXIV2 and not self._warned_no_pyexiv2:
            self._log(
                f"[WARN] Metadata embedding requested but pyexiv2 isn't "
                f"available ({_PYEXIV2_ERR}). Continuing without metadata."
            )
            self._warned_no_pyexiv2 = True

        page = 0
        last_id: int | None = None
        total_seen = 0
        total_downloaded = 0
        total_skipped = 0
        total_existing = 0
        total_failed = 0
        processed_posts = 0
        limit_reached = False
        LIMIT = 100

        try:
            while not self.stop_flag.is_set():
                page_tags = tags
                if last_id is not None:
                    page_tags = f"{tags} id:<{last_id}".strip()

                self._set_status(f"Fetching page {page}...")
                cursor = f" (id:<{last_id})" if last_id is not None else ""
                self._log(f"[*] Fetching page {page}{cursor}")

                params = {
                    "page": "dapi",
                    "s": "post",
                    "q": "index",
                    "json": "1",
                    "limit": str(LIMIT),
                    "pid": "0",
                    "tags": page_tags,
                    "user_id": user_id,
                    "api_key": api_key,
                }

                posts = self._fetch_page(session, params)
                if posts is None:
                    self._log("[ERROR] Aborting due to API failure.")
                    break
                if not posts:
                    self._log("[*] No more posts. Reached end of results.")
                    break

                total_seen += len(posts)
                self._log(
                    f"[*] Page {page}: {len(posts)} posts "
                    f"(total seen: {total_seen})"
                )

                if write_meta and HAS_PYEXIV2:
                    page_tag_names = []
                    for p in posts:
                        if isinstance(p, dict):
                            ts = (p.get("tags") or "").strip()
                            page_tag_names.extend(t for t in ts.split() if t)
                    if page_tag_names:
                        self._fetch_tag_types(
                            session, page_tag_names, user_id, api_key
                        )

                page_min_id: int | None = None
                for post in posts:
                    if isinstance(post, dict):
                        try:
                            pid_int = int(post.get("id", 0))
                        except (TypeError, ValueError):
                            pid_int = 0
                        if pid_int > 0 and (
                            page_min_id is None or pid_int < page_min_id
                        ):
                            page_min_id = pid_int

                for post in posts:
                    if self.stop_flag.is_set():
                        break
                    if max_posts > 0 and processed_posts >= max_posts:
                        limit_reached = True
                        break
                    processed_posts += 1

                    file_url = post.get("file_url") if isinstance(post, dict) else None
                    if not file_url:
                        total_skipped += 1
                        continue

                    lower = file_url.lower().split("?", 1)[0]
                    if lower.endswith(SKIP_EXT):
                        total_skipped += 1
                        self._log(f"[SKIP] Non-image: {os.path.basename(lower)}")
                        continue
                    if not lower.endswith(ALLOWED_EXT):
                        total_skipped += 1
                        self._log(f"[SKIP] Unsupported ext: {os.path.basename(lower)}")
                        continue

                    raw_name = os.path.basename(urllib.parse.urlparse(file_url).path)
                    filename = sanitize_filename(raw_name)
                    dest_path = os.path.join(out_dir, filename)

                    if os.path.exists(dest_path):
                        total_existing += 1
                        self._log(f"[=] Already exists: {filename}")
                        continue
                    if (
                        convert_png
                        and dest_path.lower().endswith(".png")
                        and os.path.exists(os.path.splitext(dest_path)[0] + ".jpg")
                    ):
                        total_existing += 1
                        self._log(
                            f"[=] Already exists (as .jpg): "
                            f"{os.path.basename(os.path.splitext(dest_path)[0] + '.jpg')}"
                        )
                        continue

                    progress_tag = (
                        f"Post {processed_posts}/{max_posts}"
                        if max_posts > 0
                        else f"Page {page}"
                    )
                    self._set_status(
                        f"{progress_tag} - downloading {filename}"
                    )
                    self._log(f"[>] Downloading: {filename}")
                    ok = self._download_file(session, file_url, dest_path)
                    if ok:
                        total_downloaded += 1
                        self._log(f"[OK] Saved: {filename}")
                        if convert_png and dest_path.lower().endswith(".png"):
                            new_path = self._convert_png_to_jpg(dest_path)
                            if new_path != dest_path:
                                self._log(
                                    f"[CONV] {filename} → "
                                    f"{os.path.basename(new_path)}"
                                )
                                dest_path = new_path
                        if write_meta and HAS_PYEXIV2:
                            self._write_metadata(dest_path, post)
                    else:
                        total_failed += 1

                    if self.stop_flag.is_set():
                        break
                    time.sleep(2.5)

                if self.stop_flag.is_set():
                    break
                if limit_reached:
                    self._log(
                        f"[*] Reached post limit ({max_posts}). Stopping."
                    )
                    break

                if len(posts) < LIMIT:
                    self._log(
                        f"[*] Got {len(posts)} < {LIMIT} posts - "
                        f"reached end of results."
                    )
                    break

                if page_min_id is None:
                    self._log("[WARN] No usable post IDs on page - stopping.")
                    break
                if last_id is not None and page_min_id >= last_id:
                    self._log("[*] Pagination stalled - finished.")
                    break
                last_id = page_min_id

                page += 1
                time.sleep(1.5)

        except Exception as e:
            self._log(f"[FATAL] Unexpected error: {e}")
        finally:
            self._log("-" * 60)
            self._log(
                f"Done. Processed: {processed_posts}"
                + (f"/{max_posts}" if max_posts > 0 else "")
                + f" | Seen: {total_seen} | "
                f"Downloaded: {total_downloaded} | "
                f"Existing: {total_existing} | "
                f"Skipped: {total_skipped} | "
                f"Failed: {total_failed}"
            )
            self._set_status("Idle.")
            self._set_running(False)
            session.close()


if __name__ == "__main__":
    app = GelbooruDownloader()
    app.mainloop()
