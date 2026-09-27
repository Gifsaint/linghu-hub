#!/usr/bin/env python3
"""Sync linghu-hub index.html YouTube video wall from @令狐投资 channel."""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

CHANNEL_ID = "UC9YCrcL7kklr6c431YOG3gA"
MAX_VIDEOS = 20
RSS_URL = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
UPLOADS_PLAYLIST = f"https://www.youtube.com/playlist?list=UU{CHANNEL_ID[2:]}"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = REPO_ROOT / "index.html"

FEATURED_START = "<!-- YT_FEATURED_START -->"
FEATURED_END = "<!-- YT_FEATURED_END -->"
MORE_START = "<!-- YT_MORE_START -->"
MORE_END = "<!-- YT_MORE_END -->"


def format_duration(seconds: int | float | None) -> str:
    if seconds is None:
        return ""
    try:
        total = int(round(float(seconds)))
    except (TypeError, ValueError):
        return ""
    if total < 0:
        return ""
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def find_yt_dlp() -> str | None:
    path = shutil.which("yt-dlp")
    if path:
        return path
    local = Path.home() / ".local" / "bin" / "yt-dlp"
    return str(local) if local.is_file() else None


def fetch_duration_yt_dlp(video_id: str, yt_dlp: str) -> str:
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        out = subprocess.check_output(
            [yt_dlp, "--print", "duration", "--skip-download", url],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=60,
        ).strip()
        last = out.splitlines()[-1].strip() if out else ""
        if last.isdigit():
            return format_duration(int(last))
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, IndexError):
        pass
    return ""


def fetch_via_rss() -> list[dict]:
    req = urllib.request.Request(
        RSS_URL,
        headers={"User-Agent": "Mozilla/5.0 (compatible; linghu-hub-sync/1.0)"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    # YouTube sometimes returns an HTML 404 page with 200; reject non-XML.
    if b"<feed" not in data[:500] and b"<?xml" not in data[:200]:
        raise RuntimeError("RSS response is not Atom XML")
    root = ET.fromstring(data)
    videos: list[dict] = []
    for entry in root.findall("atom:entry", ATOM_NS):
        vid = entry.findtext("yt:videoId", default="", namespaces=ATOM_NS)
        title = entry.findtext("atom:title", default="", namespaces=ATOM_NS)
        if not vid:
            continue
        videos.append({"id": vid, "title": title or "", "duration": ""})
        if len(videos) >= MAX_VIDEOS:
            break
    if not videos:
        raise RuntimeError("RSS returned no entries")
    return videos


def fetch_via_yt_dlp() -> list[dict]:
    yt_dlp = find_yt_dlp()
    if not yt_dlp:
        raise RuntimeError("yt-dlp not found")
    cmd = [
        yt_dlp,
        "-J",
        "--flat-playlist",
        "--playlist-end",
        str(MAX_VIDEOS),
        UPLOADS_PLAYLIST,
    ]
    raw = subprocess.check_output(cmd, stderr=subprocess.PIPE, text=True, timeout=180)
    payload = json.loads(raw)
    entries = payload.get("entries") or []
    videos: list[dict] = []
    for entry in entries:
        if not entry:
            continue
        vid = entry.get("id") or ""
        if "watch?v=" in str(vid):
            vid = str(vid).split("watch?v=", 1)[1].split("&", 1)[0]
        title = entry.get("title") or ""
        duration = format_duration(entry.get("duration"))
        if not vid:
            continue
        videos.append({"id": vid, "title": title, "duration": duration})
        if len(videos) >= MAX_VIDEOS:
            break
    if not videos:
        raise RuntimeError("yt-dlp playlist returned no entries")
    return videos


def enrich_durations(videos: list[dict]) -> None:
    missing = [v for v in videos if not v.get("duration")]
    if not missing:
        return
    yt_dlp = find_yt_dlp()
    if not yt_dlp:
        return
    for v in missing:
        v["duration"] = fetch_duration_yt_dlp(v["id"], yt_dlp)


def fetch_latest() -> list[dict]:
    errors: list[str] = []
    try:
        videos = fetch_via_rss()
        enrich_durations(videos)
        print("source=rss", file=sys.stderr)
        return videos
    except Exception as exc:  # noqa: BLE001
        errors.append(f"RSS: {exc}")
    try:
        videos = fetch_via_yt_dlp()
        print("source=yt-dlp", file=sys.stderr)
        return videos
    except Exception as exc:  # noqa: BLE001
        errors.append(f"yt-dlp: {exc}")
    raise RuntimeError("; ".join(errors))


def render_card(video: dict, featured: bool) -> str:
    vid = video["id"]
    title = html.escape(video.get("title") or "", quote=False)
    duration = video.get("duration") or ""
    cls = "vcard featured" if featured else "vcard"
    duration_html = (
        f'\n            <span class="duration">{html.escape(duration)}</span>' if duration else ""
    )
    return (
        f'        <a class="{cls}" href="https://www.youtube.com/watch?v={vid}" '
        f'target="_blank" rel="noopener noreferrer">\n'
        f'          <div class="thumb-wrap">\n'
        f'            <img class="thumb" src="https://i.ytimg.com/vi/{vid}/hqdefault.jpg" '
        f'alt="" loading="lazy" width="480" height="360" />'
        f"{duration_html}\n"
        f'            <span class="play" aria-hidden="true">▶</span>\n'
        f"          </div>\n"
        f'          <h3 class="vtitle">{title}</h3>\n'
        f"        </a>"
    )


def extract_ids(chunk: str) -> list[str]:
    return re.findall(r"youtube\.com/watch\?v=([A-Za-z0-9_-]{11})", chunk)


def ensure_markers(text: str) -> str:
    if FEATURED_START not in text:
        m = re.search(
            r'(<div class="featured-grid">)\s*(.*?)\s*(</div>\s*\n\s*<p class="subhead" id="more-videos">)',
            text,
            flags=re.S,
        )
        if not m:
            raise RuntimeError("Could not locate .featured-grid for markers")
        inner = m.group(2).strip()
        text = (
            text[: m.start()]
            + f"{m.group(1)}\n        {FEATURED_START}\n{inner}\n        {FEATURED_END}\n      "
            + m.group(3).lstrip()
            + text[m.end() :]
        )
        # Fix accidental double </div> — rebuild cleanly:
        m2 = re.search(
            r'<div class="featured-grid">.*?<p class="subhead" id="more-videos">',
            text,
            flags=re.S,
        )
        if m2:
            # Re-extract inner cards between grid open and more-videos
            block = m2.group(0)
            cards = re.findall(r'<a class="vcard featured".*?</a>', block, flags=re.S)
            if cards:
                rebuilt = (
                    '<div class="featured-grid">\n'
                    f"        {FEATURED_START}\n"
                    + "\n".join(cards)
                    + f"\n        {FEATURED_END}\n"
                    "      </div>\n\n"
                    '      <p class="subhead" id="more-videos">'
                )
                text = text[: m2.start()] + rebuilt + text[m2.end() :]

    if MORE_START not in text:
        m = re.search(
            r'(<div class="video-grid">)\s*(.*?)\s*(</div>\s*\n\s*</section>)',
            text,
            flags=re.S,
        )
        if not m:
            raise RuntimeError("Could not locate .video-grid for markers")
        inner = m.group(2).strip()
        # strip accidental prior markers
        inner = (
            inner.replace(MORE_START, "")
            .replace(MORE_END, "")
            .strip()
        )
        text = (
            text[: m.start()]
            + f'{m.group(1)}\n        {MORE_START}\n{inner}\n        {MORE_END}\n      </div>\n    </section>'
            + text[m.end() :]
        )
    return text


def section_between(text: str, start: str, end: str) -> str:
    m = re.search(re.escape(start) + r"(.*?)" + re.escape(end), text, flags=re.S)
    if not m:
        raise RuntimeError(f"Markers missing: {start} ... {end}")
    return m.group(1)


def replace_section(text: str, start: str, end: str, body: str) -> str:
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), flags=re.S)
    if not pattern.search(text):
        raise RuntimeError(f"Markers not found: {start} ... {end}")
    return pattern.sub(f"{start}\n{body}\n        {end}", text, count=1)


def rewrite_index(videos: list[dict]) -> bool:
    original = INDEX_PATH.read_text(encoding="utf-8")
    text = ensure_markers(original)

    old_ids = extract_ids(section_between(text, FEATURED_START, FEATURED_END)) + extract_ids(
        section_between(text, MORE_START, MORE_END)
    )
    new_ids = [v["id"] for v in videos]
    id_changed = old_ids != new_ids

    featured_html = "\n".join(render_card(v, True) for v in videos[:3])
    more_html = "\n".join(render_card(v, False) for v in videos[3:])
    text = replace_section(text, FEATURED_START, FEATURED_END, featured_html)
    text = replace_section(text, MORE_START, MORE_END, more_html)

    if text != original:
        INDEX_PATH.write_text(text, encoding="utf-8")

    return id_changed


def main() -> int:
    videos = fetch_latest()[:MAX_VIDEOS]
    if not videos:
        print("ERROR: no videos fetched", file=sys.stderr)
        return 1

    changed = rewrite_index(videos)
    newest = videos[0]
    print(f"count={len(videos)}")
    print(f"newest_id={newest['id']}")
    print(f"newest_title={newest['title']}")
    print(f"CHANGED={'1' if changed else '0'}")
    print("top3=")
    for v in videos[:3]:
        print(f"  {v['id']} | {v['title']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
