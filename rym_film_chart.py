"""Live RYM film chart. No bundled catalogue or fabricated release URLs."""
import random
import re
import threading
import time
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

CHART_URL = (
    "https://rateyourmusic.com/charts/esoteric/film/all-time/"
    "separate:live,archival,soundtrack/"
)
CACHE_SECONDS = 3600


class ChartUnavailable(Exception):
    pass


def film_url(href):
    url = urljoin(CHART_URL, href or "")
    parts = urlsplit(url)
    if (parts.scheme == "https" and parts.netloc == "rateyourmusic.com"
            and re.fullmatch(r"/film/[^/]+/", parts.path)
            and not parts.query and not parts.fragment):
        return url
    return None


def parse_chart(html, page=1):
    soup = BeautifulSoup(html, "html.parser")
    films, seen = [], set()
    cards = soup.select(".page_charts_section_charts_item.object_film")
    for index, card in enumerate(cards):
        link = card.select_one("a.page_charts_section_charts_item_link.film")
        if link is None:
            continue
        url = film_url(link.get("href"))
        if not url or url in seen:
            continue
        localized = link.select_one(".ui_name_locale_language")
        original = link.select_one(".ui_name_locale_original")
        title = (localized or original or link).get_text(" ", strip=True)
        if original is not None and localized is not None:
            other = original.get_text(" ", strip=True)
            if other != title:
                title += f" [{other}]"
        if not title:
            continue
        film = {"title": title, "url": url, "rank": (page - 1) * 40 + index + 1}
        date = card.select_one(".page_charts_section_charts_item_date")
        if date is not None:
            year = re.search(r"\b(?:18|19|20)\d{2}\b", date.get_text())
            if year:
                film["year"] = year.group()
        poster = card.select_one(".page_charts_section_charts_item_image img")
        if poster is not None:
            cover = urljoin(CHART_URL, poster.get("src") or poster.get("data-src") or "")
            parts = urlsplit(cover)
            if parts.scheme == "https" and parts.netloc == "e.snmc.io":
                film["cover"] = cover
        films.append(film)
        seen.add(url)
    pages = [page]
    for link in soup.select("a[href]"):
        url = urljoin(CHART_URL, link["href"])
        match = re.fullmatch(re.escape(CHART_URL) + r"(\d+)/", url)
        if match:
            pages.append(int(match.group(1)))
    return films, max(pages)


class FilmChart:
    def __init__(self, get=requests.get, clock=time.monotonic, rng=random):
        self.get, self.clock, self.rng = get, clock, rng
        self._cache = {}
        self._lock = threading.Lock()
        self._retry_at = 0
        self.health = {"status": "not_checked", "source": CHART_URL, "cached_pages": 0}

    def _page(self, page):
        now = self.clock()
        cached = self._cache.get(page)
        if cached and now - cached[0] < CACHE_SECONDS:
            return cached[1:]
        if now < self._retry_at:
            raise ChartUnavailable("RYM временно не отдаёт чарт. Попробуй через минуту.")
        try:
            response = self.get(
                CHART_URL if page == 1 else f"{CHART_URL}{page}/",
                headers={"User-Agent": "chaitom_bot/1.0", "Accept": "text/html"},
                timeout=(5, 20),
            )
            if response.status_code in (403, 429):
                self.health["status"] = "blocked"
                raise ChartUnavailable("RYM временно ограничил доступ к чарту с сервера бота. Попробуй позже.")
            response.raise_for_status()
            films, pages = parse_chart(response.text, page)
            if not films:
                raise ChartUnavailable("Не удалось прочитать фильмы из чарта RYM. Попробуй позже.")
        except (requests.RequestException, ChartUnavailable) as error:
            self._retry_at = self.clock() + 60
            if self.health["status"] != "blocked":
                self.health["status"] = "unavailable"
            if isinstance(error, ChartUnavailable):
                raise
            raise ChartUnavailable("Чарт RYM сейчас недоступен. Попробуй позже.") from None
        self._cache[page] = (self.clock(), films, pages)
        self.health.update(status="ok", cached_pages=len(self._cache))
        return films, pages

    def pick(self):
        # Serialize fetches so simultaneous commands do not hammer the chart.
        with self._lock:
            first, pages = self._page(1)
            page = self.rng.randint(1, pages)
            films = first if page == 1 else self._page(page)[0]
            return dict(self.rng.choice(films))

    def check(self):
        """One read-only startup fetch; no Telegram messages."""
        try:
            with self._lock:
                self._page(1)
        except ChartUnavailable:
            pass
        print("RYM FILM CHART:", self.health["status"], flush=True)
