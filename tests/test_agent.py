import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import agent
from datetime import datetime,timezone,timedelta
class CoreTests(unittest.TestCase):
 def test_candidate_requires_two_distinct_sources_and_safe_topic(self):
  base={'topic':'New AI software model update','date':'2025-01-01T00:00:00+00:00','articles':[{'title':'Headline','source':'One','url':'https://a'}]}
  rows=[dict(base,region='US'),dict(base,region='GB',articles=[{'title':'Another headline','source':'Two','url':'https://b'}])]
  self.assertEqual(len(agent.candidates(rows)),1)
  rows[1]['articles'][0]['source']='One'; self.assertEqual(agent.candidates(rows),[])
  self.assertFalse(agent.safe_topic('AI election announcement'))
 def test_cadence(self):
  at=datetime(2025,1,3,tzinfo=timezone.utc); s={'last_published':'2025-01-02T12:00:00+00:00'}
  self.assertFalse(agent.cadence_ok(s,1,at)); self.assertFalse(agent.cadence_ok(s,1,at+timedelta(hours=11, minutes=59)))
  self.assertTrue(agent.cadence_ok(s,1,at+timedelta(hours=12)))
  self.assertFalse(agent.cadence_ok(s,2,at+timedelta(hours=23, minutes=59)))
  self.assertTrue(agent.cadence_ok(s,2,at+timedelta(days=1, hours=12)))
  self.assertIsNotNone(agent.parse_date(s['last_published']))
  self.assertTrue(agent.cadence_ok({},2,at))
 def test_text_fitting(self):
  from PIL import Image,ImageDraw
  d=ImageDraw.Draw(Image.new('RGB',(300,300))); f=agent.font(18)
  lines=agent.fit_lines(d,'A long descriptive sentence fits across the canvas',f,100)
  self.assertGreater(len(lines),1)
  self.assertTrue(all(d.textbbox((0,0),x,font=f)[2]<=100 for x in lines))
if __name__=='__main__': unittest.main()
