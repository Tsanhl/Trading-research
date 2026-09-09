import tempfile,unittest
from pathlib import Path
from trading_hub.storage import HubStore
from trading_hub.web_server import research_docs
class ResearchPresentation(unittest.TestCase):
 def test_internal_material_hidden_before_limit_but_preserved_and_retrievable(self):
  with tempfile.TemporaryDirectory() as t:
   with HubStore(Path(t)/'data/hub.sqlite3') as s:
    s.ingest_documents('structure',[{'source':source,'source_id':'one','text':'test source','coverage':'TEST','published_at':None,'known_at':None} for source in ['kevinx','al_brooks_local_inventory','hub_source_rule_audit']])
   self.assertEqual([x['source'] for x in research_docs(t,limit=1)],['kevinx'])
   self.assertEqual(len(research_docs(t,include_internal=True)),3)
   self.assertEqual(research_docs(t,search='al_brooks_local_inventory'),[])
   self.assertEqual(len(research_docs(t,search='al_brooks_local_inventory',include_internal=True)),1)
   with HubStore(Path(t)/'data/hub.sqlite3') as s:self.assertEqual(s.stats()['documents'],3)
