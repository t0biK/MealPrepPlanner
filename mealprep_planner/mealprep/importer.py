import hashlib
import html
import http.client
import ipaddress
import json
import os
import re
import socket
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

from . import VERSION, ai, db, recipes
from .ingredients import parse_line

USER_AGENT = f"MealPrepPlanner/{VERSION} (+https://github.com/t0biK/MealPrepPlanner)"
ALLOWED_PORTS = (80, 443)
TIMEOUT = 15
MAX_REDIRECTS = 5
MAX_HTML = 3 * 1024 * 1024
MAX_IMAGE = 2 * 1024 * 1024
MAX_OEMBED = 256 * 1024
MAX_PAGE_TEXT = 20000
MAX_VIDEO_TEXT = 5000


class FetchError(Exception):
    """`code` is an API error code: fetch_blocked, fetch_failed or fetch_too_large."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


# ---- fetching (SSRF-guarded) ----

def _is_global(ip):
    addr = ipaddress.ip_address(ip)
    return (getattr(addr, "ipv4_mapped", None) or addr).is_global


def check_url(url):
    """Raise FetchError unless url is a plain http(s) URL on port 80/443 whose host resolves only to global IPs."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        raise FetchError("fetch_blocked")
    if parts.scheme not in ("http", "https") or not host or "@" in parts.netloc or port not in ALLOWED_PORTS:
        raise FetchError("fetch_blocked")
    try:
        addrs = {info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
        blocked = not addrs or not all(_is_global(a) for a in addrs)
    except (socket.gaierror, UnicodeError):
        raise FetchError("fetch_failed")
    except ValueError:
        blocked = True
    if blocked:
        raise FetchError("fetch_blocked")


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            check_url(newurl)
        except FetchError:
            fp.close()
            raise
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# no cookie processor and no proxies
_opener = urllib.request.build_opener(_CheckedRedirects, urllib.request.ProxyHandler({}))


def fetch(url, max_bytes, accept):
    """GET url -> (final_url, content_type, body). Raises FetchError."""
    check_url(url)
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": accept, "Accept-Language": "de-DE,de;q=0.9"})
    try:
        # ponytail: the timeout applies per socket read, not to the whole download; add a deadline if a slow server hangs the worker
        with _opener.open(req, timeout=TIMEOUT) as resp:
            length = resp.headers.get("Content-Length", "")
            if length.isdigit() and int(length) > max_bytes:
                raise FetchError("fetch_too_large")
            body = resp.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise FetchError("fetch_too_large")
            return resp.geturl(), resp.headers.get("Content-Type", ""), body
    except urllib.error.HTTPError as e:
        e.close()
        raise FetchError("fetch_failed")
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError):
        raise FetchError("fetch_failed")


def decode(body, content_type):
    m = re.search(r"charset=([\w-]+)", content_type, re.I)
    try:
        return body.decode(m.group(1) if m else "utf-8", errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


# ---- images ----

def image_ext(data):
    """File extension by magic bytes (JPEG, PNG, WebP), else None."""
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:4] == b"\x89PNG":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def download_image(url, data_dir):
    """Store the image as images/<sha256>.<ext>; returns the file name or None."""
    try:
        data = fetch(url, MAX_IMAGE, "image/jpeg,image/png,image/webp")[2]
        ext = image_ext(data)
        if ext is None:
            return None
        name = f"{hashlib.sha256(data).hexdigest()}.{ext}"
        folder = Path(data_dir) / "images"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / name
        if not target.exists():
            tmp = folder / (name + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, target)
        return name
    except (FetchError, OSError):
        return None


# ---- HTML extraction ----

class _Extractor(HTMLParser):
    META = ("og:title", "og:image", "og:description")

    def __init__(self):
        super().__init__()
        self.jsonld = []
        self.meta = {}
        self.title = ""
        self._script = None
        self._in_title = False
        self._title_done = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script" and (a.get("type") or "").strip().lower() == "application/ld+json":
            self._script = []
        elif tag == "meta":
            key = a.get("property") or a.get("name")
            if key in self.META and a.get("content"):
                self.meta.setdefault(key, a["content"])
        elif tag == "title" and not self._title_done:
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "script" and self._script is not None:
            try:
                self.jsonld.append(json.loads("".join(self._script), strict=False))
            except (ValueError, RecursionError):
                pass
            self._script = None
        elif tag == "title" and self._in_title:
            self._in_title, self._title_done = False, True

    def handle_data(self, data):
        if self._script is not None:
            self._script.append(data)
        elif self._in_title:
            self.title += data


def extract(page_html, base_url):
    """JSON-LD objects plus OpenGraph/title fallbacks of an HTML page."""
    p = _Extractor()
    p.feed(page_html)
    p.close()
    image = p.meta.get("og:image", "").strip()
    image = urljoin(base_url, image) if image else None
    return {
        "jsonld": p.jsonld,
        "og_title": _line(p.meta.get("og:title", "")),
        "og_image": image if image and recipes.http_url(image) else None,
        "og_description": _line(p.meta.get("og:description", "")),
        "title": _line(p.title),
    }


# ---- schema.org Recipe -> draft ----

def _line(s):
    return " ".join(s.split())


def _clean(v):
    """Plain text of an untrusted string: tags dropped, entities decoded, whitespace collapsed."""
    return _line(html.unescape(re.sub(r"<[^>]*>", "", v))) if isinstance(v, str) else ""


def _as_list(v):
    return v if isinstance(v, list) else [] if v is None else [v]


def _find_recipe(node):
    if isinstance(node, list):
        return next((r for r in map(_find_recipe, node) if r), None)
    if not isinstance(node, dict):
        return None
    types = [str(t).rsplit("/", 1)[-1].rsplit(":", 1)[-1] for t in _as_list(node.get("@type"))]
    return node if "Recipe" in types else _find_recipe(node.get("@graph"))


def _ingredient(line):
    """parse_line, fitted to the §6 limits."""
    i = parse_line(line)
    if i["amount"] is not None and not 0 < i["amount"] <= 100000:
        i["amount"] = None
    return {**i, "name": i["name"][:100], "note": i["note"][:200] if i["note"] else None}


def _first_int(v):
    for item in _as_list(v):
        m = re.search(r"\d+", str(item))
        if m:
            return int(m.group())
    return None


_DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:\d+(?:\.\d+)?S)?)?$")


def _minutes(v):
    m = _DURATION.match(v.strip()) if isinstance(v, str) else None
    return int(m.group(1) or 0) * 1440 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0) if m else 0


def _steps(v):
    if isinstance(v, str):
        lines = re.split(r"\r?\n|<br\s*/?>|</p>|</li>", v, flags=re.I)
        return [s for s in map(_clean, lines) if s]
    if isinstance(v, list):
        return [s for item in v for s in _steps(item)]
    if isinstance(v, dict):
        if "itemListElement" in v:  # HowToSection
            return _steps(v["itemListElement"])
        return _steps(v.get("text") or v.get("name"))
    return []


def _image_url(v, base_url):
    first = _as_list(v)[:1]
    v = first[0] if first else None
    if isinstance(v, dict):
        v = v.get("url") or v.get("contentUrl")
    if not isinstance(v, str) or not v.strip():
        return None
    url = urljoin(base_url, v.strip())
    return url if recipes.http_url(url) else None


def _nutrition(v):
    if not isinstance(v, dict):
        return None
    out = {}
    for key, field in (("kcal", "calories"), ("protein_g", "proteinContent"),
                       ("fat_g", "fatContent"), ("carbs_g", "carbohydrateContent")):
        raw = v.get(field)
        m = re.search(r"\d+(?:[.,]\d+)?", raw) if isinstance(raw, str) else None
        num = float(m.group().replace(",", ".")) if m else raw if isinstance(raw, (int, float)) and not isinstance(raw, bool) else None
        out[key] = num if num is not None and 0 <= num <= recipes.NUTRITION_MAX[key] else None
    return {**out, "source": "page"} if any(x is not None for x in out.values()) else None


def recipe_from_jsonld(objs, default_portions, tag_names, base_url=""):
    """First schema.org Recipe in the JSON-LD objects -> draft fields (not yet validated), or None."""
    node = _find_recipe(objs)
    if node is None:
        return None
    servings = _first_int(node.get("recipeYield"))
    minutes = _minutes(node.get("totalTime")) or _minutes(node.get("prepTime")) + _minutes(node.get("cookTime"))
    ingredients = []
    for line in _as_list(node.get("recipeIngredient")):
        line = _clean(line)
        if line:
            ingredients.append(_ingredient(line))
    known = {n.casefold(): n for n in tag_names}
    words = []
    for field in ("keywords", "recipeCategory", "recipeCuisine"):
        for item in _as_list(node.get(field)):
            words += str(item).split(",") if isinstance(item, str) else []
    tags = list(dict.fromkeys(known[w.strip().casefold()] for w in words if w.strip().casefold() in known))
    return {
        "title": _clean(node.get("name"))[:200],
        "servings": servings if servings and 1 <= servings <= 50 else default_portions,
        "total_minutes": minutes if 1 <= minutes <= 1440 else None,
        "tags": tags[:15],
        "ingredients": ingredients[:100],
        "steps": [s[:2000] for s in _steps(node.get("recipeInstructions"))][:50],
        "image_url": _image_url(node.get("image"), base_url),
        "nutrition": _nutrition(node.get("nutrition")),
    }


# ---- video / social links and page text (M4) ----

SOURCE_HOSTS = {"tiktok.com": "tiktok", "youtube.com": "youtube", "youtu.be": "youtube", "instagram.com": "instagram"}
SHORT_HOSTS = ("vm.tiktok.com", "vt.tiktok.com", "youtu.be")  # resolved through redirects before oEmbed


def source_kind(url):
    host = urlsplit(url).hostname or ""
    for domain, kind in SOURCE_HOSTS.items():
        if host == domain or host.endswith("." + domain):
            return kind
    return "web"


def _lines(v):
    """Text keeping its line structure (ingredient lists), blank lines dropped."""
    return "\n".join(line for line in map(_line, v.splitlines()) if line)


def oembed(url, kind):
    """TikTok/YouTube oEmbed -> {caption, author, thumbnail}. Raises FetchError."""
    quoted = quote(url, safe="")
    endpoint = (f"https://www.tiktok.com/oembed?url={quoted}" if kind == "tiktok"
                else f"https://www.youtube.com/oembed?url={quoted}&format=json")
    _, content_type, body = fetch(endpoint, MAX_OEMBED, "application/json")
    try:
        data = json.loads(decode(body, content_type))
    except (ValueError, RecursionError):
        data = None
    if not isinstance(data, dict):
        raise FetchError("fetch_failed")
    caption, author, thumb = data.get("title"), data.get("author_name"), data.get("thumbnail_url")
    return {
        "caption": _lines(caption)[:5000] if isinstance(caption, str) else "",
        "author": _line(author)[:200] if isinstance(author, str) else "",
        "thumbnail": thumb if recipes.http_url(thumb) else None,
    }


def _json_after(page_html, key):
    """The JSON value after the first `"key":` in a page, or None."""
    i = page_html.find(f'"{key}":')
    if i < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(page_html, i + len(key) + 3)[0]
    except (ValueError, RecursionError):
        return None


def tiktok_transcript(page_html):
    """Spoken text of a TikTok video page (WebVTT subtitle track, the original rather than a machine translation); "" on any problem."""
    tracks = _json_after(page_html, "subtitleInfos")
    tracks = [t for t in tracks if isinstance(t, dict) and t.get("Format") == "webvtt" and recipes.http_url(t.get("Url"))] \
        if isinstance(tracks, list) else []
    if not tracks:
        return ""
    track = next((t for t in tracks if t.get("Source") != "MT"), tracks[0])
    try:
        _, content_type, body = fetch(track["Url"], MAX_OEMBED, "text/vtt,text/plain")
    except FetchError:
        return ""
    cues = [_clean(line) for line in decode(body, content_type).splitlines()
            if "-->" not in line and not line.startswith("WEBVTT") and not line.strip().isdigit()]
    return " ".join(c for c in cues if c)[:MAX_VIDEO_TEXT]


def youtube_description(page_html):
    """Full description of a YouTube watch page, line structure kept; "" on any problem."""
    v = _json_after(page_html, "shortDescription")
    return _lines(v)[:MAX_VIDEO_TEXT] if isinstance(v, str) else ""


# kind -> (heading the AI sees, fn(page_html) -> extra text); a further source is one function + one entry (M17)
VIDEO_PAGE_TEXT = {"tiktok": ("Transkript:", tiktok_transcript), "youtube": ("Videobeschreibung:", youtube_description)}


class _TextExtractor(HTMLParser):
    SKIP = ("script", "style", "noscript")

    def __init__(self):
        super().__init__()
        self.parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def page_text(page_html):
    """Visible text of a page (no script/style/noscript), whitespace collapsed, <= 20000 chars."""
    p = _TextExtractor()
    p.feed(page_html)
    p.close()
    return _line(" ".join(p.parts))[:MAX_PAGE_TEXT]


def _caption_fields(text):
    """Rule-based result for free text (AI off or failed): a title line, and every line starting with an amount as an ingredient."""
    lines = [line for line in map(_line, text.splitlines()) if line]
    parsed = [(line, _ingredient(line)) for line in lines]
    title = next((line for line, i in parsed if i["amount"] is None), lines[0])
    return {"title": title[:200], "ingredients": [i for _, i in parsed if i["amount"] is not None][:100]}


# ---- import job -> draft ----

_STALE_WARNINGS = ("paste_caption", "ai_failed", "ai_disabled", "no_recipe_data")  # no longer true after pasted text


def _field(job, key):
    """Job as sqlite Row or dict; a missing column is None."""
    return job[key] if key in job.keys() else None


def build_draft(job, conn, data_dir):
    """Build a validated recipe draft (§6) for the job: page, video link, Instagram link or pasted text. Raises FetchError."""
    url, text = _field(job, "url"), _field(job, "text")
    prior = json.loads(job["draft"]) if _field(job, "draft") else None
    settings = db.get_settings(conn)
    default_portions = settings["default_portions"]
    entity = settings["ai_entity"] if settings["ai_enabled"] else None
    tag_names = [t["name"] for t in recipes.list_tags(conn)]
    known = recipes.top_ingredient_names(conn, ai.MAX_KNOWN_NAMES)

    def from_text(text, base, fallback_fields):
        """AI draft from text; AI off/failed -> rule-based fields, plus the reason as a warning."""
        draft, why = ai.from_text(text, known, tag_names, entity, base)
        if draft is None:
            draft, _ = recipes.validate_draft(
                {**base, **fallback_fields, "warnings": base["warnings"] + why}, tag_names)
        return draft

    if text:  # pasted text, or a caption pasted onto an existing draft (keeps link and image)
        if prior:
            base = {k: prior.get(k) for k in ("source_url", "source_kind", "image", "image_url")}
            warnings = [w for w in prior.get("warnings", []) if w not in _STALE_WARNINGS]
        else:
            base = {"source_url": url, "source_kind": source_kind(url) if url else "text"}
            warnings = []
        base = {**base, "format_version": 1, "servings": default_portions, "warnings": warnings}
        draft = from_text(text, base, _caption_fields(text))
    else:
        kind, fetched = source_kind(url), None
        if kind == "web" or urlsplit(url).hostname in SHORT_HOSTS:  # a short link is resolved through its redirects
            fetched = fetch(url, MAX_HTML, "text/html,application/xhtml+xml")
        final_url = fetched[0] if fetched else url
        kind = source_kind(final_url)
        warnings = []
        if conn.execute("SELECT 1 FROM recipes WHERE source_url IN (?, ?)", (url, final_url)).fetchone():
            warnings.append("already_imported")
        base = {"format_version": 1, "source_kind": kind, "source_url": final_url if len(final_url) <= 2048 else url,
                "servings": default_portions, "warnings": warnings}
        host = urlsplit(final_url).hostname or url

        if kind == "instagram":  # no fetch: the caption is pasted by hand
            draft, _ = recipes.validate_draft({**base, "title": host[:200], "warnings": warnings + ["paste_caption"]}, tag_names)
        elif kind in ("tiktok", "youtube"):
            info = oembed(final_url, kind)
            base["image_url"] = info["thumbnail"]
            ai_text = info["caption"]
            if entity and kind in VIDEO_PAGE_TEXT:  # extra text from the public video page; any failure means caption only
                heading, extract_text = VIDEO_PAGE_TEXT[kind]
                try:
                    page = fetched or fetch(final_url, MAX_HTML, "text/html,application/xhtml+xml")  # a short link's page is reused
                    extra = extract_text(decode(page[2], page[1]))
                except FetchError:
                    extra = ""
                if extra:
                    ai_text = f"{ai_text}\n\n{heading}\n{extra}".strip()
            if ai_text:
                draft = from_text(ai_text, base, _caption_fields(info["caption"]) if info["caption"] else {"title": host[:200]})
            else:
                draft, _ = recipes.validate_draft({**base, "title": host[:200], "warnings": warnings + ["paste_caption"]}, tag_names)
        else:
            html_text = decode(fetched[2], fetched[1])
            page = extract(html_text, final_url)
            draft = None
            fields = recipe_from_jsonld(page["jsonld"], default_portions, tag_names, final_url)
            if fields:
                fields["image_url"] = fields["image_url"] or page["og_image"]  # JSON-LD often only references the image by @id
                draft, _ = recipes.validate_draft({**base, **fields}, tag_names)
                if draft:
                    draft, why = ai.enrich(draft, known, tag_names, entity)
                    draft["warnings"] += why
            if draft is None:
                base["image_url"] = page["og_image"]
                text = page_text(html_text)
                draft, why = ai.from_text(text, known, tag_names, entity, base) if text else (None, [])
                if draft is None:
                    draft, _ = recipes.validate_draft({
                        **base, "title": (page["og_title"] or page["title"] or host)[:200],
                        "warnings": warnings + ["no_recipe_data"] + why}, tag_names)

    draft["tags"] += [n for n in recipes.suggested_tags(draft, tag_names) if n not in draft["tags"]][:15 - len(draft["tags"])]
    if draft.get("image_url") and not draft.get("image"):
        name = download_image(draft["image_url"], data_dir)
        if name:
            draft["image"] = name
        else:
            draft["warnings"].append("image_failed")
    return draft


