#!/usr/bin/env python3
"""Meme Radar — denny build meme galerie pre Nie Som Idealista.

Stiahne popularne memes (Reddit cez meme-api.com, Know Your Meme trending,
Imgflip templaty), ku kazdemu Reddit meme vygeneruje kratky AI komentar
(Claude, vision) a vyrenderuje staticku HTML galeriu do docs/ pre GitHub Pages.
"""

import json
import os
import sys
import html
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
ARCHIVE = DOCS / "archive"
DATA = ROOT / "data"
SEEN_FILE = DATA / "seen.json"

TZ = ZoneInfo("Europe/Bratislava")
NOW = datetime.now(TZ)
DATE_ISO = NOW.strftime("%Y-%m-%d")
DATE_HUMAN = NOW.strftime("%d.%m.%Y")

SUBREDDITS = ["memes", "dankmemes", "me_irl", "wholesomememes", "comedyheaven"]
PER_SUB = 8          # kolko kandidatov na subreddit
MAX_MEMES = 18       # strop galerie
MIN_UPS = 100        # filter slabych postov
SEEN_DAYS = 30       # ako dlho drzat dedup historiu
KYM_LIMIT = 5
IMGFLIP_LIMIT = 10

VIDEO_SUBS = ["TikTokCringe", "Unexpected", "ContagiousLaughter",
              "funnyvideos", "PerfectlyCutScreams", "AbruptChaos"]
VIDEO_PER_SUB = 10
MAX_VIDEOS = 10
MIN_VIDEO_UPS = 200
YT_LIMIT = 6
VIDEO_DOMAINS = ("v.redd.it", "youtube.com", "youtu.be", "streamable.com", "tiktok.com")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 meme-radar/1.0"}


# ---------------------------------------------------------------- fetchery

def fetch_reddit(client: httpx.Client) -> list[dict]:
    """Popularne memes cez meme-api.com (wrapper nad Redditom, bez auth)."""
    memes = []
    for sub in SUBREDDITS:
        try:
            r = client.get(f"https://meme-api.com/gimme/{sub}/{PER_SUB}", headers=UA)
            r.raise_for_status()
            for m in r.json().get("memes", []):
                if m.get("nsfw") or m.get("spoiler"):
                    continue
                if not m.get("url", "").lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
                    continue
                memes.append({
                    "id": m["postLink"].rstrip("/").split("/")[-1],
                    "title": m.get("title", ""),
                    "url": m["url"],
                    "post": m["postLink"],
                    "sub": m.get("subreddit", sub),
                    "ups": m.get("ups", 0),
                })
        except Exception as e:
            print(f"[warn] meme-api r/{sub} zlyhal: {e}", file=sys.stderr)
    return memes


def fetch_kym(client: httpx.Client) -> list[dict]:
    """Trendujuce meme formaty z Know Your Meme (scrape, fail-soft)."""
    try:
        r = client.get("https://knowyourmeme.com/memes/popular", headers=UA, follow_redirects=True)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        seen, trends = set(), []
        # aktualne popularne formaty su v kartach .wide-card / .overlayed-card
        for a in soup.select("a.wide-card, a.overlayed-card"):
            href = a.get("href", "")
            slug = href.removeprefix("/memes/").strip("/")
            if not href.startswith("/memes/") or not slug or "/" in slug or slug in seen:
                continue
            img = a.find("img")
            title = (img.get("alt", "") if img else "") or slug.replace("-", " ").title()
            for suffix in ("meme example image", "meme and image example", "meme image example",
                           "meme and viral video", "meme example", "image example"):
                title = title.replace(suffix, "")
            title = title.strip(" .")
            img_url = None
            if img:
                img_url = img.get("src-medium") or img.get("src-large") or img.get("data-src") or img.get("src")
                if img_url and img_url.startswith("data:"):
                    img_url = None
            seen.add(slug)
            trends.append({
                "title": title or slug,
                "link": f"https://knowyourmeme.com{href}",
                "img": img_url,
            })
            if len(trends) >= KYM_LIMIT:
                break
        return trends
    except Exception as e:
        print(f"[warn] KnowYourMeme zlyhal: {e}", file=sys.stderr)
        return []


def fetch_reddit_videos(client: httpx.Client) -> list[dict]:
    """Top video memes dna z Reddit video subov (app-only OAuth, fail-soft)."""
    cid = os.environ.get("REDDIT_CLIENT_ID")
    secret = os.environ.get("REDDIT_CLIENT_SECRET")
    if not cid or not secret:
        print("[warn] REDDIT_CLIENT_ID/SECRET nie su nastavene — video sekcia (Reddit) vypadne", file=sys.stderr)
        return []
    try:
        tok = client.post(
            "https://www.reddit.com/api/v1/access_token",
            auth=(cid, secret),
            data={"grant_type": "client_credentials"},
            headers=UA,
        )
        tok.raise_for_status()
        token = tok.json()["access_token"]
    except Exception as e:
        print(f"[warn] Reddit OAuth token zlyhal: {e}", file=sys.stderr)
        return []

    headers = {**UA, "Authorization": f"Bearer {token}"}
    videos = []
    for sub in VIDEO_SUBS:
        try:
            r = client.get(
                f"https://oauth.reddit.com/r/{sub}/top",
                params={"t": "day", "limit": VIDEO_PER_SUB, "raw_json": 1},
                headers=headers,
            )
            r.raise_for_status()
            for child in r.json()["data"]["children"]:
                d = child["data"]
                if d.get("over_18") or d.get("stickied"):
                    continue
                domain = d.get("domain", "")
                if not (d.get("is_video") or any(v in domain for v in VIDEO_DOMAINS)):
                    continue
                thumb = None
                try:
                    thumb = html.unescape(d["preview"]["images"][0]["source"]["url"])
                except (KeyError, IndexError):
                    t = d.get("thumbnail", "")
                    if t.startswith("http"):
                        thumb = t
                if not thumb:
                    continue
                videos.append({
                    "id": d["id"],
                    "title": d.get("title", ""),
                    "thumb": thumb,
                    "link": f"https://www.reddit.com{d.get('permalink', '')}",
                    "source": f"r/{d.get('subreddit', sub)}",
                    "ups": d.get("ups", 0),
                })
        except Exception as e:
            print(f"[warn] Reddit video r/{sub} zlyhal: {e}", file=sys.stderr)
    return videos


def _iso_duration_seconds(dur: str) -> int:
    """Parse ISO 8601 trvanie typu PT1M23S na sekundy."""
    import re
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", dur or "")
    if not m:
        return 10**6
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


def fetch_youtube(client: httpx.Client) -> list[dict]:
    """Trending comedy videa/Shorts z YouTube Data API (fail-soft)."""
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        print("[warn] YOUTUBE_API_KEY nie je nastaveny — video sekcia (YouTube) vypadne", file=sys.stderr)
        return []
    try:
        r = client.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={
                "part": "snippet,contentDetails,statistics",
                "chart": "mostPopular",
                "regionCode": "US",
                "videoCategoryId": "23",  # Comedy
                "maxResults": 15,
                "key": key,
            },
        )
        r.raise_for_status()
        items = []
        for v in r.json().get("items", []):
            secs = _iso_duration_seconds(v.get("contentDetails", {}).get("duration", ""))
            sn = v.get("snippet", {})
            thumbs = sn.get("thumbnails", {})
            thumb = (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get("url")
            if not thumb:
                continue
            items.append({
                "id": f"yt_{v['id']}",
                "title": sn.get("title", ""),
                "thumb": thumb,
                "link": f"https://www.youtube.com/watch?v={v['id']}",
                "source": f"YouTube · {sn.get('channelTitle', '')}",
                "ups": int(v.get("statistics", {}).get("viewCount", 0)),
                "secs": secs,
            })
        shorts = [i for i in items if i["secs"] <= 60]
        if len(shorts) < 4:
            shorts = [i for i in items if i["secs"] <= 180]
        return shorts[:YT_LIMIT]
    except Exception as e:
        print(f"[warn] YouTube API zlyhal: {e}", file=sys.stderr)
        return []


def fetch_imgflip(client: httpx.Client) -> list[dict]:
    """Najpouzivanejsie meme templaty z Imgflip (bez auth)."""
    try:
        r = client.get("https://api.imgflip.com/get_memes", headers=UA)
        r.raise_for_status()
        return [
            {"name": t["name"], "url": t["url"]}
            for t in r.json()["data"]["memes"][:IMGFLIP_LIMIT]
        ]
    except Exception as e:
        print(f"[warn] Imgflip zlyhal: {e}", file=sys.stderr)
        return []


# ---------------------------------------------------------------- dedup

def load_seen() -> dict:
    if SEEN_FILE.exists():
        try:
            return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def prune_seen(seen: dict) -> dict:
    cutoff = (NOW - timedelta(days=SEEN_DAYS)).strftime("%Y-%m-%d")
    return {k: v for k, v in seen.items() if v >= cutoff}


def select_memes(candidates: list[dict], seen: dict) -> list[dict]:
    fresh, ids = [], set()
    for m in sorted(candidates, key=lambda x: x["ups"], reverse=True):
        if m["id"] in seen or m["id"] in ids or m["ups"] < MIN_UPS:
            continue
        ids.add(m["id"])
        fresh.append(m)
        if len(fresh) >= MAX_MEMES:
            break
    # ak je po dedupe malo memes, pusti dnu aj slabsie posty
    if len(fresh) < 8:
        for m in sorted(candidates, key=lambda x: x["ups"], reverse=True):
            if m["id"] in seen or m["id"] in ids:
                continue
            ids.add(m["id"])
            fresh.append(m)
            if len(fresh) >= MAX_MEMES:
                break
    return fresh


def select_videos(reddit_videos: list[dict], yt_videos: list[dict], seen: dict) -> list[dict]:
    """Reddit videa podla upvotes + YouTube trending, dedup cez seen.json."""
    out, ids = [], set()
    for v in sorted(reddit_videos, key=lambda x: x["ups"], reverse=True):
        if v["id"] in seen or v["id"] in ids or v["ups"] < MIN_VIDEO_UPS:
            continue
        ids.add(v["id"])
        out.append(v)
        if len(out) >= MAX_VIDEOS:
            break
    for v in yt_videos:
        if v["id"] in seen or v["id"] in ids:
            continue
        ids.add(v["id"])
        out.append(v)
    return out


# ---------------------------------------------------------------- AI komentare

AI_SYSTEM = (
    "Si expert na internetovu meme kulturu a pomahas adminovi slovenskej meme stranky "
    "Nie Som Idealista (Instagram ~87k, Facebook ~55k followerov; zacala ako parodia "
    "uctu Som Idealista, dnes tvori originalne memes pre slovenske publikum). "
    "Dostanes obrazok popularneho meme z Redditu. Odpovedaj po slovensky, strucne a vecne."
)

AI_SCHEMA = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "komentar": {
                "type": "string",
                "description": "1-2 vety: aky je to format/o com meme je a preco prave teraz leti",
            },
            "napad": {
                "type": "string",
                "description": "1-2 vety: konkretny napad, ako format adaptovat pre Nie Som Idealista a slovensky kontext",
            },
        },
        "required": ["komentar", "napad"],
        "additionalProperties": False,
    },
}


def ai_comments(memes: list[dict]) -> None:
    """Doplni ku kazdemu meme AI komentar. Fail-soft — meme bez komentara ostava."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("[warn] ANTHROPIC_API_KEY nie je nastaveny — galeria bude bez AI komentarov", file=sys.stderr)
        return
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    for m in memes:
        try:
            resp = client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=500,
                system=AI_SYSTEM,
                output_config={"format": AI_SCHEMA},
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "url", "url": m["url"]}},
                        {"type": "text", "text": (
                            f"Titulok postu: {m['title']}\n"
                            f"Subreddit: r/{m['sub']}, upvotes: {m['ups']}\n"
                            "Analyzuj toto meme."
                        )},
                    ],
                }],
            )
            text = next(b.text for b in resp.content if b.type == "text")
            data = json.loads(text)
            m["komentar"] = data.get("komentar", "")
            m["napad"] = data.get("napad", "")
        except Exception as e:
            print(f"[warn] AI komentar pre {m['id']} zlyhal: {e}", file=sys.stderr)


# ---------------------------------------------------------------- render

CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #101014; color: #e8e8ec; font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
       max-width: 640px; margin: 0 auto; padding: 16px 12px 48px; }
h1 { font-size: 1.5rem; margin: 8px 0 2px; }
.sub { color: #9a9aa4; font-size: .85rem; margin-bottom: 20px; }
.sub a { color: #7fb0ff; text-decoration: none; }
h2 { font-size: 1.1rem; margin: 28px 0 12px; padding-bottom: 6px; border-bottom: 1px solid #2a2a32; }
.card { background: #18181f; border: 1px solid #26262e; border-radius: 14px; overflow: hidden; margin-bottom: 18px; }
.card img { width: 100%; height: auto; display: block; background: #0c0c10; }
.card .body { padding: 12px 14px 14px; }
.card .title { font-weight: 600; line-height: 1.35; }
.card .meta { color: #9a9aa4; font-size: .8rem; margin-top: 4px; }
.card .meta a { color: #7fb0ff; text-decoration: none; }
.ai { margin-top: 10px; padding: 10px 12px; background: #1f1f29; border-left: 3px solid #f5a623;
      border-radius: 0 8px 8px 0; font-size: .9rem; line-height: 1.45; }
.ai .idea { margin-top: 6px; color: #ffd27f; }
.vid { position: relative; display: block; }
.vid .play { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
             width: 56px; height: 56px; border-radius: 50%; background: rgba(0,0,0,.55);
             display: flex; align-items: center; justify-content: center; font-size: 24px;
             pointer-events: none; }
.trend { display: flex; gap: 12px; align-items: center; background: #18181f; border: 1px solid #26262e;
         border-radius: 12px; padding: 10px; margin-bottom: 10px; }
.trend img { width: 72px; height: 72px; object-fit: cover; border-radius: 8px; flex-shrink: 0; }
.trend a { color: #e8e8ec; text-decoration: none; font-weight: 600; }
.trend .kym { color: #9a9aa4; font-size: .8rem; display: block; margin-top: 3px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 10px; }
.tpl { background: #18181f; border: 1px solid #26262e; border-radius: 10px; overflow: hidden; }
.tpl img { width: 100%; aspect-ratio: 1; object-fit: cover; display: block; }
.tpl div { padding: 6px 8px; font-size: .75rem; color: #c8c8d0; }
footer { margin-top: 36px; color: #6a6a74; font-size: .75rem; text-align: center; }
footer a { color: #7fb0ff; }
"""


def esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def render_page(memes: list[dict], videos: list[dict], trends: list[dict],
                templates: list[dict], archive_links: list[str]) -> str:
    parts = [
        "<!doctype html><html lang='sk'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>Meme Radar — {DATE_HUMAN}</title>",
        "<link rel='icon' href='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>🔥</text></svg>'>",
        f"<style>{CSS}</style></head><body>",
        "<h1>🔥 Meme Radar</h1>",
        f"<p class='sub'>{DATE_HUMAN} · denná inšpirácia pre <strong>Nie Som Idealista</strong> · <a href='archive/index.html'>archív</a></p>",
    ]

    parts.append(f"<h2>Top memes dňa ({len(memes)})</h2>")
    if not memes:
        parts.append("<p class='sub'>Dnes sa nepodarilo stiahnuť žiadne memes 😔</p>")
    for m in memes:
        parts.append("<div class='card'>")
        parts.append(f"<img src='{esc(m['url'])}' alt='{esc(m['title'])}' loading='lazy'>")
        parts.append("<div class='body'>")
        parts.append(f"<div class='title'>{esc(m['title'])}</div>")
        parts.append(f"<div class='meta'>r/{esc(m['sub'])} · ⬆️ {m['ups']:,} · <a href='{esc(m['post'])}'>originál</a></div>")
        if m.get("komentar") or m.get("napad"):
            parts.append("<div class='ai'>")
            if m.get("komentar"):
                parts.append(f"<div>{esc(m['komentar'])}</div>")
            if m.get("napad"):
                parts.append(f"<div class='idea'>💡 {esc(m['napad'])}</div>")
            parts.append("</div>")
        parts.append("</div></div>")

    if videos:
        parts.append(f"<h2>🎬 Video memes dňa ({len(videos)})</h2>")
        for v in videos:
            meta = f"{esc(v['source'])}"
            if v["source"].startswith("r/"):
                meta += f" · ⬆️ {v['ups']:,}"
            parts.append("<div class='card'>")
            parts.append(f"<a class='vid' href='{esc(v['link'])}'>"
                         f"<img src='{esc(v['thumb'])}' alt='{esc(v['title'])}' loading='lazy'>"
                         f"<span class='play'>▶️</span></a>")
            parts.append("<div class='body'>")
            parts.append(f"<div class='title'>{esc(v['title'])}</div>")
            parts.append(f"<div class='meta'>{meta} · <a href='{esc(v['link'])}'>pozrieť video</a></div>")
            parts.append("</div></div>")

    if trends:
        parts.append("<h2>Trendujúce formáty (Know Your Meme)</h2>")
        for t in trends:
            parts.append("<div class='trend'>")
            if t.get("img"):
                parts.append(f"<img src='{esc(t['img'])}' alt='' loading='lazy'>")
            parts.append(f"<div><a href='{esc(t['link'])}'>{esc(t['title'])}</a>"
                         f"<span class='kym'>kontext a vysvetlenie na KYM →</span></div></div>")

    if templates:
        parts.append("<h2>Populárne templaty (Imgflip)</h2><div class='grid'>")
        for t in templates:
            parts.append(f"<div class='tpl'><img src='{esc(t['url'])}' alt='{esc(t['name'])}' loading='lazy'><div>{esc(t['name'])}</div></div>")
        parts.append("</div>")

    parts.append(f"<footer>Meme Radar · generované {NOW.strftime('%d.%m.%Y %H:%M')} · zdroje: Reddit (meme-api.com), Know Your Meme, Imgflip</footer>")
    parts.append("</body></html>")
    return "".join(parts)


def render_archive_index(dates: list[str]) -> str:
    items = "".join(
        f"<li><a href='{d}.html'>{datetime.strptime(d, '%Y-%m-%d').strftime('%d.%m.%Y')}</a></li>"
        for d in sorted(dates, reverse=True)
    )
    return (
        "<!doctype html><html lang='sk'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>Meme Radar — archív</title><style>{CSS} li{{margin:6px 0}} a{{color:#7fb0ff}}</style></head><body>"
        "<h1>🔥 Meme Radar — archív</h1><p class='sub'><a href='../index.html'>← dnešné vydanie</a></p>"
        f"<ul>{items}</ul></body></html>"
    )


# ---------------------------------------------------------------- main

def main() -> int:
    DOCS.mkdir(exist_ok=True)
    ARCHIVE.mkdir(exist_ok=True)
    DATA.mkdir(exist_ok=True)

    with httpx.Client(timeout=30) as client:
        candidates = fetch_reddit(client)
        reddit_videos = fetch_reddit_videos(client)
        yt_videos = fetch_youtube(client)
        trends = fetch_kym(client)
        templates = fetch_imgflip(client)

    print(f"[info] kandidatov z Redditu: {len(candidates)}, video kandidatov: {len(reddit_videos)}+{len(yt_videos)}, "
          f"KYM trendov: {len(trends)}, Imgflip templatov: {len(templates)}")

    seen = prune_seen(load_seen())
    memes = select_memes(candidates, seen)
    videos = select_videos(reddit_videos, yt_videos, seen)
    print(f"[info] vybranych po dedupe: {len(memes)} memes, {len(videos)} videi")

    ai_comments(memes)

    page = render_page(memes, videos, trends, templates, [])
    (DOCS / "index.html").write_text(page, encoding="utf-8")
    (ARCHIVE / f"{DATE_ISO}.html").write_text(page, encoding="utf-8")

    dates = [p.stem for p in ARCHIVE.glob("????-??-??.html")]
    (ARCHIVE / "index.html").write_text(render_archive_index(dates), encoding="utf-8")

    for item in memes + videos:
        seen[item["id"]] = DATE_ISO
    SEEN_FILE.write_text(json.dumps(seen, indent=1), encoding="utf-8")
    (DATA / "last_count.txt").write_text(f"{len(memes)} memes + {len(videos)} videí", encoding="utf-8")

    print(f"[ok] galeria vygenerovana: {len(memes)} memes, {len(videos)} videi -> docs/index.html")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
