import sys
sys.path.insert(0,'.phosphor-deps')
import unittest
from unittest.mock import patch
from PIL import Image
import render
import lyrics_source
import control_server

class DisplaySyncTests(unittest.TestCase):
    def frame(self, state, cached_offset):
        with patch.object(lyrics_source,'get_state',return_value=state), patch.object(lyrics_source,'get_song_offset',return_value=cached_offset), patch.object(render.time,'monotonic',return_value=100), patch.object(render,'background_override',Image.new('RGB',(1920,462))), patch.object(render,'_get_lyrics_bg',return_value=None):
            return render._draw_lyrics_hud(Image.new('RGB',(1920,462))).tobytes()

    def state(self,idx=1):
        return {'artist':'Test','title':'Clock','lines':[(0,'line zero'),(1,'line one'),(2,'line two')], 'position':1.1,'current_idx':idx,'has_sync':True,'loading':False}

    def test_panel_does_not_apply_offset_again(self):
        reference=self.frame(self.state(),0)
        for offset in (-18.8,-1,0.97,2):
            self.assertEqual(reference,self.frame(self.state(),offset))

    def test_panel_and_api_use_same_authoritative_line(self):
        with patch.object(lyrics_source,'get_state',return_value=self.state(2)):
            self.assertEqual(control_server._lyrics_live_state()['current_line'],'line two')
        # The panel follows current_idx even when the stored clock is from a prior sample.
        self.assertNotEqual(self.frame(self.state(1),0),self.frame(self.state(2),0))

    def test_unsynced_panel_does_not_invent_a_separate_timeline(self):
        early=self.state(1);early['has_sync']=False
        later=dict(early,position=200)
        self.assertEqual(self.frame(early,0),self.frame(later,0))

if __name__=='__main__': unittest.main()
