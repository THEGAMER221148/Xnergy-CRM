import sys,unittest,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from demo_projection import project
class Projection(unittest.TestCase):
 def test_private_clients_and_related_content_never_reach_browser(self):
  p=[{'id':'p-secret','name':'Confidential Widgets','website_url':'https://secretwidgets.example','status':'client','sector':'Ultra secret sector','notes':'Secret terms'}, {'id':'alias','name':'Confidential Widgets LLC','website_url':'https://secretwidgets.example','status':'contacted'}, {'id':'p-ok','name':'Public Prospect','notes':'A meeting with Confidential Widgets last week','status':'contacted'}]
  d=[{'id':'d-secret','name':'Confidential Widgets','prospect_id':'p-secret','stage':'engaged_client','mandate_type':'Secret mandate'}]
  investors=[{'id':'i-ok','name':'Public Fund','sector_focus':'Technology','notes':'Secret Widgets deal: Confidential Widgets','description':'A public investment firm','website_url':'https://public.example/?token=secret'}, {'id':'i-private','name':'Confidential Widgets','domain':'secretwidgets.example'}]
  contacts=[{'organization_id':'i-ok','name':'Public Contact','email':'public@public.example'},{'organization_id':'i-ok','name':'Secret Person','email':'hidden@private.example'}]
  pc=[{'prospect_id':'p-secret','name':'Secret Person','email':'hidden@private.example','phone':'5555551111'}]
  out=project(investors,contacts,[],p,d,pc)
  text=json.dumps(out).lower()
  for secret in ['confidential widgets','secretwidgets','secret person','hidden@private.example','5555551111','ultra secret sector','secret terms','secret mandate','p-secret','d-secret','token=']:self.assertNotIn(secret,text)
  self.assertEqual(out['investors'][0]['name'],'Public Fund')
  self.assertEqual(out['investors'][0]['contacts'][0]['email'],'public@public.example')
  self.assertEqual(out['deals'][0]['name'],'Private Client B')
  self.assertTrue(out['deals'][0]['private'])
  self.assertIsNone(out['investors'][0]['notes'])
 def test_signed_unlinked_and_completed_clients_are_private(self):
  d=[{'id':'1','name':'Secret A','stage':'engaged_client'},{'id':'2','name':'Secret B','stage':'completed'}]
  out=project([],[],[],[],d,[])
  self.assertEqual([r['name'] for r in out['deals']],['Private Client A','Private Client B'])
  for r in out['deals']:self.assertEqual(set(r),{'id','name','private','stage'})
 def test_real_bankers_and_criteria_preserved_no_raw_blob(self):
  out=project([{'id':'i','name':'Real Fund','notes':'Valid fund notes'}],[],[{'organization_id':'i','criteria':{'check_min':100,'stages':['Growth'],'secret_payload':'excluded'},'evidence_status':'source_stated'}],[],[],[],[{'id':'b','display_name':'Real Banker'}],[{'entity_kind':'investor','entity_id':'i','banker_ids':['b']}])
  self.assertEqual(out['investors'][0]['bankers'][0]['name'],'Real Banker')
  self.assertEqual(out['investors'][0]['mandates'][0]['criteria'],{'check_min':100,'stages':['Growth']})
  self.assertNotIn('secret_payload',json.dumps(out))
if __name__=='__main__':unittest.main()
