import io
import json
import unittest
import urllib.error
import urllib.parse
from unittest.mock import patch

import lyrics_source


class LyricsSearchTests(unittest.TestCase):
    def test_ovh_fallback_when_primary_sources_fail(self):
        expected = {"plain": "Test", "synced": [(None, "Test")]}
        with patch.object(lyrics_source, "_lrclib_get", return_value=None), \
                patch.object(lyrics_source, "_genius_fetch", return_value=None), \
                patch.object(lyrics_source, "_lyrics_ovh_get", side_effect=[None, {"plainLyrics": "Test"}]) as ovh, \
                patch.object(lyrics_source, "_result_to_lyrics", return_value=expected.copy()), \
                patch.object(lyrics_source, "_set_cached") as cache:
            result = lyrics_source._load_lyrics_for("Ayra Starr", "Ms. Paper ft. Theodora", None)
        self.assertEqual(result["source"], "lyrics_ovh_fallback")
        self.assertEqual(ovh.call_args.args[:2], ("Ayra Starr", "Ms. Paper"))
        cache.assert_called_once()

    def test_transient_503_retries_same_request(self):
        entry = {"syncedLyrics": "[00:01.00]Test"}
        unavailable = urllib.error.HTTPError("https://lrclib.net", 503, "Unavailable", {}, None)
        with patch("urllib.request.urlopen", side_effect=[
                unavailable, io.BytesIO(json.dumps(entry).encode())]) as request, \
                patch("lyrics_source.time.sleep") as sleep:
            self.assertEqual(lyrics_source._lrclib_get("Artist", "Song"), entry)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[0], request.call_args_list[1])
        sleep.assert_called_once_with(1.0)

    def test_featured_title_retries_base_title_and_rejects_wrong_song(self):
        queries = []
        correct = {"artistName": "Ayra Starr", "trackName": "Ms. Paper",
                   "syncedLyrics": "[00:01.00]Test"}

        def respond(request, timeout):
            url = request.full_url
            if "/api/get?" in url:
                raise urllib.error.HTTPError(url, 404, "Missing", {}, None)
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["q"][0]
            queries.append(query)
            entries = [correct] if query == "Ayra Starr Ms. Paper" else [
                {"artistName": "Someone else", "trackName": "Unrelated",
                 "syncedLyrics": "[00:01.00]Wrong"}]
            return io.BytesIO(json.dumps(entries).encode())

        with patch("urllib.request.urlopen", side_effect=respond):
            result = lyrics_source._lrclib_get("Ayra Starr", "Ms. Paper ft. Theodora")
        self.assertEqual(result, correct)
        self.assertIn("Ayra Starr Ms. Paper", queries)

    def test_plain_fallback_survives_empty_search(self):
        plain = {"plainLyrics": "Test"}
        with patch("urllib.request.urlopen", side_effect=[
                io.BytesIO(json.dumps(plain).encode()), io.BytesIO(b"[]")]):
            self.assertEqual(lyrics_source._lrclib_get("Artist", "Song"), plain)


if __name__ == "__main__":
    unittest.main()
