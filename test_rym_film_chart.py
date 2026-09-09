import json
import tempfile
import unittest
from pathlib import Path

from rym_film_chart import FilmChart, ChartUnavailable, load_snapshot


class FirstChoice:
    @staticmethod
    def choice(items):
        return items[0]


class FilmChartTests(unittest.TestCase):
    def make_snapshot(self, films):
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / "rym_films.json"
        path.write_text(
            json.dumps({"films": films}, ensure_ascii=False),
            encoding="utf-8",
        )
        return tmp, path

    def test_local_snapshot(self):
        tmp, path = self.make_snapshot([
            {
                "rank": 1,
                "title": "Example Film",
                "url": "https://rateyourmusic.com/search?searchterm=Example+Film&searchtype=F",
            }
        ])
        try:
            chart = FilmChart(path=str(path), rng=FirstChoice)
            self.assertEqual(chart.health["status"], "ok")
            self.assertEqual(chart.health["mode"], "local_snapshot")
            self.assertEqual(chart.pick()["title"], "Example Film")
        finally:
            tmp.cleanup()

    def test_missing_snapshot(self):
        chart = FilmChart(path="/definitely/missing/rym_films.json")
        self.assertEqual(chart.health["status"], "unavailable")
        with self.assertRaises(ChartUnavailable):
            chart.pick()

    def test_bundled_snapshot(self):
        chart = FilmChart()
        self.assertEqual(chart.health["status"], "ok")
        self.assertGreaterEqual(chart.health["films"], 200)
        film = chart.pick()
        self.assertTrue(film["url"].startswith("https://rateyourmusic.com/"))


if __name__ == "__main__":
    unittest.main()
