"""Локальный снимок эзотерического film-чарта RateYourMusic.

ВАЖНО: этот модуль НИКОГДА не обращается к RYM по сети.
Он читает только bundled-файл rym_films.json.
"""

from __future__ import annotations

import json
import os
import random

CHART_URL = (
    "https://rateyourmusic.com/charts/esoteric/film/all-time/"
    "separate:live,archival,soundtrack/"
)

DEFAULT_SNAPSHOT = os.path.join(
    os.path.dirname(__file__),
    "rym_films.json",
)


class ChartUnavailable(Exception):
    pass


def load_snapshot(path=DEFAULT_SNAPSHOT):
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    films = raw.get("films", []) if isinstance(raw, dict) else raw
    if not isinstance(films, list):
        raise ChartUnavailable("Некорректный формат rym_films.json.")

    valid = []
    seen = set()

    for item in films:
        if not isinstance(item, dict):
            continue

        title = str(item.get("title", "")).strip()
        url = str(item.get("url", "")).strip()

        try:
            rank = int(item.get("rank"))
        except (TypeError, ValueError):
            continue

        if (
            not title
            or rank < 1
            or rank in seen
            or not url.startswith("https://rateyourmusic.com/")
        ):
            continue

        film = {
            "rank": rank,
            "title": title,
            "url": url,
        }

        year = str(item.get("year", "")).strip()
        cover = str(item.get("cover", "")).strip()

        if year:
            film["year"] = year
        if cover.startswith("https://"):
            film["cover"] = cover

        valid.append(film)
        seen.add(rank)

    valid.sort(key=lambda x: x["rank"])

    if not valid:
        raise ChartUnavailable(
            "Локальный снимок rym_films.json пуст или повреждён."
        )

    return valid


class FilmChart:
    """Совместимый wrapper для старого кода. Работает только локально."""

    def __init__(self, path=DEFAULT_SNAPSHOT, rng=random, **_ignored):
        self.path = path
        self.rng = rng

        try:
            self._films = load_snapshot(path)
            self.health = {
                "status": "ok",
                "source": CHART_URL,
                "mode": "local_snapshot",
                "films": len(self._films),
            }
        except Exception as exc:
            self._films = []
            self.health = {
                "status": "unavailable",
                "source": CHART_URL,
                "mode": "local_snapshot",
                "films": 0,
                "error": str(exc),
            }

    def pick(self):
        if not self._films:
            raise ChartUnavailable(
                "Локальный снимок rym_films.json не загрузился."
            )
        return dict(self.rng.choice(self._films))

    def check(self):
        # Никаких HTTP-запросов. Только вывод состояния локального JSON.
        print(
            "RYM FILM LOCAL SNAPSHOT:",
            self.health.get("status"),
            f"({self.health.get('films', 0)} films)",
            flush=True,
        )
