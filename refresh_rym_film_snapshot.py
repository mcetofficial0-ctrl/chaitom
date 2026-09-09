"""Refresh rym_films.json from RYM chart HTML saved in your browser.

Usage:
  1) Open the RYM film chart in your normal browser.
  2) Save pages as HTML (page1.html, page2.html, ...).
  3) Run:
       python refresh_rym_film_snapshot.py page1.html page2.html ...

This utility is intentionally offline, so a deployed Render bot never hits RYM.
"""
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

CHART_URL = (
    "https://rateyourmusic.com/charts/esoteric/film/all-time/"
    "separate:live,archival,soundtrack/"
)


def parse_file(path, start_rank):
    soup = BeautifulSoup(Path(path).read_text(encoding="utf-8", errors="ignore"), "html.parser")
    cards = soup.select(".page_charts_section_charts_item.object_film")
    results = []
    for card in cards:
        link = card.select_one("a.page_charts_section_charts_item_link.film")
        if not link:
            continue
        localized = link.select_one(".ui_name_locale_language")
        original = link.select_one(".ui_name_locale_original")
        title = (localized or original or link).get_text(" ", strip=True)
        if not title:
            continue
        item = {
            "rank": start_rank + len(results),
            "title": title,
            # Search URLs are more robust than guessing RYM /film/<slug>/ paths.
            "url": f"https://rateyourmusic.com/search?searchterm={quote_plus(title)}&searchtype=F",
        }
        date = card.select_one(".page_charts_section_charts_item_date")
        if date:
            year = re.search(r"\b(?:18|19|20)\d{2}\b", date.get_text(" ", strip=True))
            if year:
                item["year"] = year.group()
        img = card.select_one(".page_charts_section_charts_item_image img")
        if img:
            cover = img.get("src") or img.get("data-src")
            if cover and cover.startswith("https://e.snmc.io/"):
                item["cover"] = cover
        results.append(item)
    return results


def main(paths):
    if not paths:
        raise SystemExit("Передай один или несколько сохранённых HTML-файлов чарта RYM.")
    films = []
    rank = 1
    for path in paths:
        chunk = parse_file(path, rank)
        films.extend(chunk)
        rank += len(chunk)
    if not films:
        raise SystemExit("Не удалось найти карточки фильмов в HTML.")
    payload = {
        "chart_url": CHART_URL,
        "snapshot_note": "Offline snapshot generated from browser-saved RYM chart HTML.",
        "films": films,
    }
    out = Path(__file__).with_name("rym_films.json")
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {len(films)} films -> {out}")


if __name__ == "__main__":
    main(sys.argv[1:])
