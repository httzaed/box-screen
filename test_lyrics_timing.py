import unittest
from unittest.mock import patch
import lyrics_source as source

class LyricsTimingTests(unittest.TestCase):
    def test_intro_has_no_line_until_first_timestamp(self):
        self.assertEqual(source._current_idx([(10,'one'),(20,'two')],9.9),-1)
        self.assertEqual(source._current_idx([(10,'one'),(20,'two')],10),0)
        self.assertEqual(source._current_idx([(10,'one'),(20,'two')],20),1)

    def test_manual_offset_applies_immediately_in_seconds(self):
        cache={source._cache_key('A','B'):{'offset':-18.8}}
        with patch.object(source,'_state',{'artist':'A','title':'B'}), patch.object(source,'_calibrated_offset',-18.8), patch.object(source,'_get_disk_cache',return_value=cache), patch.object(source,'_mark_cache_dirty'), patch.object(source,'_flush_cache_if_dirty'):
            source.set_song_offset('A','B',0.3)
            self.assertEqual(source._calibrated_offset,0.3)
            self.assertEqual(cache[source._cache_key('A','B')]['offset'],0.3)
            self.assertEqual(cache[source._cache_key('A','B')]['offset_source'],'manual')

    def test_other_song_does_not_shift_current_playback(self):
        with patch.object(source,'_state',{'artist':'A','title':'B'}), patch.object(source,'_calibrated_offset',0), patch.object(source,'_get_disk_cache',return_value={}):
            source.set_song_offset('Other','Track',2)
            self.assertEqual(source._calibrated_offset,0)

    def test_uncached_song_saves_and_restores_manual_half_second(self):
        cache = {}
        with patch.object(source, '_get_disk_cache', return_value=cache), patch.object(source, '_mark_cache_dirty'), patch.object(source, '_flush_cache_if_dirty') as flush:
            source.set_song_offset('New', 'Song', 0.5)
            flush.assert_called_once()
            self.assertEqual(source._load_offset('New', 'Song'), (0.5, True))
            self.assertEqual(source._load_offset('Next', 'Song'), (None, False))
            self.assertIsNone(source._get_cached_lyrics('New', 'Song'))
            source._set_cached_lyrics('New', 'Song', [(1, 'line')], '', 2)
            self.assertEqual(source._load_offset('New', 'Song'), (0.5, True))

    def test_invalid_offsets_rejected(self):
        for value in (float('nan'),float('inf'),61,-61):
            with self.assertRaises(ValueError): source.set_song_offset('A','B',value)

if __name__=='__main__': unittest.main()
