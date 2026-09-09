import unittest
from unittest.mock import Mock

from rym_film_chart import FilmChart, ChartUnavailable, CHART_URL, parse_chart


# RYM card structure, including duplicate poster/title links and localized names.
HTML = '''<a href="/film/not-a-chart-entry/">Advertisement</a>
<div class="page_charts_section_charts_item object_film">
 <a class="page_charts_section_charts_item_image_link" href="/film/die-zweite-heimat-–-chronik-einer-jugend/">
  <picture class="page_charts_section_charts_item_image"><img src="//e.snmc.io/i/300/s/poster.jpeg"></picture>
 </a>
 <a class="page_charts_section_charts_item_link film" href="/film/die-zweite-heimat-–-chronik-einer-jugend/">
  <span class="ui_name_locale_language">Heimat II</span><span class="ui_name_locale_original">Die Zweite Heimat</span>
 </a>
 <div class="page_charts_section_charts_item_date"><span>31 August 1992</span></div>
</div>'''


def response(body=HTML, code=200):
    result = Mock(status_code=code, text=body)
    result.raise_for_status.return_value = None
    return result


class FilmChartTests(unittest.TestCase):
    def test_actual_film_urls_and_posters(self):
        films, pages = parse_chart(HTML)
        self.assertEqual(len(films), 1)
        self.assertEqual(films[0], {
            "title": "Heimat II [Die Zweite Heimat]",
            "url": "https://rateyourmusic.com/film/die-zweite-heimat-–-chronik-einer-jugend/",
            "cover": "https://e.snmc.io/i/300/s/poster.jpeg", "year": "1992", "rank": 1,
        })

    def test_external_and_old_fabricated_links_rejected(self):
        for bad in ["https://example.com/film/test/", "/release/film/director/test/"]:
            films, _ = parse_chart(HTML.replace('/film/die-zweite-heimat-–-chronik-einer-jugend/', bad))
            self.assertEqual(films, [])

    def test_pagination_and_cache(self):
        first = HTML + f'<a href="{CHART_URL}125/">125</a>'
        get = Mock(side_effect=[response(first), response(HTML)])
        rng = Mock()
        rng.randint.return_value = 125
        rng.choice.side_effect = lambda items: items[0]
        chart = FilmChart(get=get, rng=rng)
        self.assertEqual(chart.pick()["rank"], 4961)
        chart.pick()
        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args.args[0], f"{CHART_URL}125/")

    def test_blocked_response_has_no_local_fallback_and_cools_down(self):
        get = Mock(return_value=response(code=403))
        chart = FilmChart(get=get)
        for _ in range(2):
            with self.assertRaises(ChartUnavailable):
                chart.pick()
        self.assertEqual(get.call_count, 1)
        self.assertEqual(chart.health["status"], "blocked")

    def test_expired_cache_does_not_hide_source_failure(self):
        now = [0]
        get = Mock(side_effect=[response(), response(code=403)])
        rng = Mock()
        rng.randint.return_value = 1
        rng.choice.side_effect = lambda items: items[0]
        chart = FilmChart(get=get, clock=lambda: now[0], rng=rng)
        chart.pick()
        now[0] = 3601
        with self.assertRaises(ChartUnavailable):
            chart.pick()

    def test_empty_or_challenge_page_is_not_a_chart(self):
        chart = FilmChart(get=Mock(return_value=response('<h1>Verify you are human</h1>')))
        with self.assertRaises(ChartUnavailable):
            chart.pick()


if __name__ == '__main__':
    unittest.main()
