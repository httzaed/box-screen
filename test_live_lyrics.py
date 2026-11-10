import unittest
from unittest.mock import patch
from types import SimpleNamespace
import control_server

class LiveLyricsTests(unittest.TestCase):
    def snapshot(self, state):
        with patch.dict('sys.modules', {'lyrics_source': SimpleNamespace(get_state=lambda: state)}):
            return control_server._lyrics_live_state()

    def test_native_tuple_lines_follow_current_index(self):
        result = self.snapshot({'artist':'Artist','title':'Song','lines':[(0,'before'),(2,'now'),(4,'after')], 'current_idx':1,'has_sync':True})
        self.assertEqual((result['previous_line'],result['current_line'],result['next_line']),('before','now','after'))
        self.assertTrue(result['synced'])

    def test_blank_line_clears_previous_text(self):
        result = self.snapshot({'lines':[[0,'old'],[2,'']], 'current_idx':1})
        self.assertEqual(result['current_line'],'')
        self.assertEqual(result['next_line'],'')

    def test_no_track_is_explicit_and_clear(self):
        result = self.snapshot(None)
        self.assertFalse(result['active'])
        self.assertEqual(result['current_line'],'')

    def test_loading_and_same_index_content_changes_are_visible(self):
        pending=self.snapshot({'artist':'A','title':'B','loading':True,'lines':[],'current_idx':0})
        ready=self.snapshot({'artist':'A','title':'B','loading':False,'lines':[(0,'ready')],'current_idx':0})
        self.assertNotEqual(pending,ready)
        self.assertEqual(ready['current_line'],'ready')

    def test_text_dict_unsynced_and_out_of_range(self):
        for line in ('plain', {'text':'plain'}):
            result=self.snapshot({'lines':[line],'current_idx':0})
            self.assertEqual(result['current_line'],'plain')
            self.assertFalse(result['synced'])
        self.assertEqual(self.snapshot({'lines':['text'],'current_idx':-1})['current_line'],'')

if __name__ == '__main__': unittest.main()
