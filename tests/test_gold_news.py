import unittest

from services.gold_news import GoldNewsService, merge, parse

RSS = """<?xml version="1.0" encoding="utf-8"?><rss><channel><item><title>الذهب يتراجع</title><pubDate>2026-11-10 13:45:45</pubDate><link>https://sa.investing.com/a1</link></item>
<item><title>خبر بلا رابط آمن</title><pubDate>2026-11-10 13:00:00</pubDate><link>javascript:alert(1)</link></item>
<item><title>النفط يرتفع</title><pubDate>2026-11-10 14:00:00</pubDate><link>https://sa.investing.com/a2</link></item></channel></rss>""".encode()


class GoldNewsTests(unittest.TestCase):
    def test_parse_keeps_only_https_links(self):
        self.assertEqual([i["title"] for i in parse(RSS)], ["الذهب يتراجع", "النفط يرتفع"])
        self.assertEqual(parse(b"not xml"), [])

    def test_gold_headlines_come_first_and_duplicates_go(self):
        items = merge([parse(RSS), parse(RSS)])
        self.assertEqual([i["title"] for i in items], ["الذهب يتراجع", "النفط يرتفع"])

    def test_cache_failure_and_one_source_down(self):
        clock, calls = [0], []
        def fetch(url):
            calls.append(url)
            if url == "bad":
                raise OSError("down")
            return RSS
        service = GoldNewsService(fetch=fetch, monotonic=lambda: clock[0], feeds=("bad", "good"))
        self.assertTrue(service.snapshot()["available"])
        service.snapshot()
        self.assertEqual(len(calls), 2)                       # served from the cache
        clock[0] = 601
        service.snapshot()
        self.assertEqual(len(calls), 4)
        dead = GoldNewsService(fetch=lambda u: (_ for _ in ()).throw(OSError()), monotonic=lambda: 0, feeds=("bad",))
        answer = dead.snapshot()
        self.assertFalse(answer["available"])
        self.assertTrue(answer["error"])


if __name__ == "__main__":
    unittest.main()
