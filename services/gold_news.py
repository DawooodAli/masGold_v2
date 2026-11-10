"""Arabic market news for the home page: Investing.com Arabic RSS (commodities + economic indicators), free and public.

Display only: nothing is stored, posted or used by any accounting rule. Fetched with the standard library, cached for 10 minutes
(a failure is retried after 5 minutes, never on every dashboard load); a failing source never breaks the home page. Items whose
title mentions gold come first (the owner reads the gold news before the rest)."""
import time
import urllib.request
import xml.etree.ElementTree as ET

FEEDS = ("https://sa.investing.com/rss/news_11.rss", "https://sa.investing.com/rss/news_95.rss")
MAX_ITEMS = 6
SIMILAR = 0.5                                      # two headlines sharing at least this share of their words are the same story
CACHE_SECONDS, FAILURE_RETRY_SECONDS = 600, 300
MAX_BYTES = 600_000
GOLD_WORDS = ("الذهب", "ذهب", "الفيدرالي", "الفائدة", "التضخم", "الدولار")        # the headlines that move gold first


def parse(xml_bytes):
    """[{title, link, time}] of one RSS document; a malformed document gives an empty list."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    items = []
    for node in root.iter("item"):
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        if title and link.startswith("https://"):
            items.append({"title": title, "link": link, "time": (node.findtext("pubDate") or "").strip()[:16]})
    return items


def merge(lists):
    """Deduplicated by title, newest first, gold-related first inside the cap."""
    pool = [item for one in lists for item in one]
    pool.sort(key=lambda i: i["time"], reverse=True)
    pool.sort(key=lambda i: 0 if "ذهب" in i["title"] else 1)
    items, kept = [], []
    for item in pool:
        words = _words(item["title"])
        if any(_same_story(words, other) for other in kept):
            continue
        kept.append(words)
        items.append(item)
    return items[:MAX_ITEMS]


def _words(title):
    return {w for w in title.replace("عاجل:", " ").replace(":", " ").replace("..", " ").split() if len(w) > 2}


def _same_story(a, b):
    return bool(a and b) and len(a & b) / min(len(a), len(b)) >= SIMILAR


def _fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=8) as response:
        return response.read(MAX_BYTES)


class GoldNewsService:
    def __init__(self, fetch=None, monotonic=None, feeds=FEEDS):
        self._fetch, self._monotonic, self._feeds = fetch or _fetch, monotonic or time.monotonic, feeds
        self._items, self._stamp, self._failed, self._updated = [], None, False, ""

    def snapshot(self, force=False):
        wait = FAILURE_RETRY_SECONDS if self._failed else CACHE_SECONDS
        if force or self._stamp is None or self._monotonic() - self._stamp >= wait:
            self._refresh()
        return {"available": bool(self._items), "items": list(self._items), "updated": self._updated,
                "error": "تعذر جلب الأخبار الآن." if self._failed and not self._items else ""}

    def _refresh(self):
        self._stamp = self._monotonic()
        lists = []
        for url in self._feeds:
            try:
                lists.append(parse(self._fetch(url)))
            except Exception:                                      # one source down must not hide the other
                continue
        items = merge(lists)
        self._failed = not items
        if items:
            self._items, self._updated = items, time.strftime("%H:%M")


_SHARED = None


def shared():
    global _SHARED
    if _SHARED is None:
        _SHARED = GoldNewsService()
    return _SHARED
