import unittest
import numpy as np
from rhythm_dsp import RhythmDetector, event_level

class RhythmTests(unittest.TestCase):
    def run_signal(self, hits=(), sustained=False, amplitude=0.2):
        d=RhythmDetector(); events=[]
        for i in range(180):
            t=np.arange(512)/44100+i*512/44100
            envelope=np.zeros(512)
            for hit in hits:
                envelope+=np.where(t>=hit,np.exp(-np.maximum(0,t-hit)/0.025),0)
            if sustained: envelope=(t>=0.2).astype(float)
            x=amplitude*np.sin(2*np.pi*86*t)*envelope
            old=d.events['kick'][1]; result=d.process(x,float(t[-1]))
            if result['kick'][1]>old: events.append(result['kick'])
        return events

    def test_silence(self): self.assertEqual(self.run_signal(),[])
    def test_isolated_and_double(self):
        for hits in ((0.2,), (0.2,0.32), (0.2,0.8)):
            events=self.run_signal(hits)
            self.assertEqual(len(events),len(hits))
            for event,hit in zip(events,hits): self.assertLess(event[0]-hit,0.024)
    def test_sustained(self): self.assertEqual(len(self.run_signal(sustained=True)),1)
    def test_dynamics(self):
        weak=self.run_signal((0.2,),amplitude=0.03)[0][2]
        strong=self.run_signal((0.2,),amplitude=0.2)[0][2]
        self.assertLess(weak,strong)
    def test_release(self):
        self.assertEqual(event_level((1,1,0.8),2,0.09),0)

if __name__=='__main__': unittest.main()
