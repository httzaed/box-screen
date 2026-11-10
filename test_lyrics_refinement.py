import unittest
from unittest.mock import patch

import lyrics_source as source


class LyricsRefinementTests(unittest.TestCase):
    def test_background_sync_replaces_plain_at_current_position(self):
        plain = {"synced": [(None, "plain")], "source": "lrclib_fast"}
        synced = {"synced": [(10, "first"), (20, "second")], "source": "youtube"}
        jobs = []

        class DeferredThread:
            def __init__(self, target, **kwargs):
                self.target = target

            def start(self):
                jobs.append(self.target)

        real_load = source._load_lyrics_for

        def load(artist, title, video_id, fast_first=False):
            return real_load(artist, title, video_id, True) if fast_first else synced

        with patch.object(source, "_load_lyrics_fast", return_value=plain), \
             patch.object(source, "_load_lyrics_for", side_effect=load), \
             patch.object(source.threading, "Thread", DeferredThread), \
             patch.object(source, "score_lyrics_candidate", side_effect=lambda candidate, *args: (80 if source._has_sync(candidate) else 20, {})), \
             patch.object(source, "_set_cached"), \
             patch.object(source, "_flush_cache_if_dirty"):
            current = source._load_lyrics_for("Artist", "Song", "video", True)
            self.assertIs(source._apply_lyrics_refinement(current), current)
            jobs.pop()()
            upgraded = source._apply_lyrics_refinement(current)
            self.assertIs(upgraded, synced)
            self.assertTrue(source._has_sync(upgraded))
            self.assertEqual(source._current_idx(upgraded["synced"], 21), 1)
            self.assertNotIn("_refinement", plain)

    def test_late_result_cannot_replace_next_song(self):
        old = {"_refinement": {}}
        current = {"synced": [(None, "next song")], "_refinement": {}}
        old["_refinement"]["result"] = {"synced": [(10, "old song")]}
        self.assertIs(source._apply_lyrics_refinement(current), current)
        self.assertIsNone(source._apply_lyrics_refinement(None))


if __name__ == "__main__":
    unittest.main()
