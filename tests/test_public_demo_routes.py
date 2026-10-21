import sys,unittest,asyncio,importlib.util
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
spec=importlib.util.spec_from_file_location('public_demo',Path(__file__).resolve().parents[1]/'app/public_demo.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Routes(unittest.TestCase):
 def setUp(self):
  m._cache={'as_of':'2026-10-05','private_clients':1,'bankers':[],'prospects':[], 'deals':[{'id':'private-1','name':'Private Client A','private':True,'stage':'engaged_client'}],'investors':[{'id':'i1','name':'Real Fund','sector':'Healthcare','holding_sectors':'Energy','bankers':[{'id':'b1','name':'Real Banker'}],'contacts':[{'name':'Real Contact','email':'real@example.invalid'}],'notes':'Private to detail view','mandates':[{'criteria':{'check_min':100,'check_max':500,'stages':['Growth']}}]}]}
  app=FastAPI();app.include_router(m.router);self.c=TestClient(app)
 def test_no_auth_required_and_redacted_shape(self):
  self.assertEqual(self.c.get('/api/demo/summary').status_code,200)
  r=self.c.get('/api/demo/record/deals/private-1').json()
  self.assertEqual(set(r),{'id','name','private','stage'})
 def test_bound_filters_and_search(self):
  self.assertEqual(self.c.get('/api/demo/records/investors?banker=b1&sector=Energy&minimum=200&stage=Growth').json()['total'],1)
  self.assertEqual(self.c.get('/api/demo/records/investors?banker=b2').json()['total'],0)
  self.assertEqual(self.c.get('/api/demo/records/investors?maximum=20').json()['total'],0)
  r=self.c.get('/api/demo/records/investors?q=real@example.invalid').json()
  self.assertEqual(r['total'],1);self.assertNotIn('notes',r['items'][0]);self.assertNotIn('contacts',r['items'][0])
 def test_no_write_or_raw_export_routes(self):
  for verb in ('post','put','patch','delete'):
   self.assertEqual(getattr(self.c,verb)('/api/demo/record/deals/private-1').status_code,405)
  self.assertEqual(self.c.get('/api/demo/records/users').status_code,404)
  self.assertEqual(self.c.get('/api/demo/records/investors?limit=10000').status_code,422)
if __name__=='__main__':unittest.main()
