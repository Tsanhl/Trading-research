"""Offline intake checks on disposable files/databases; never crawl the network."""
import json,tempfile,unittest
from pathlib import Path
from scripts.index_local_pa import inventory,ingest
from trading_hub.storage import HubStore
from trading_hub.connection_health import _webull

class SourceRefresh(unittest.TestCase):
 def test_inventory_does_not_execute_and_ignores_external_links(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)/'PA with spaces';root.mkdir()
   (root/'notes.png').write_bytes(b'not executable')
   (root/'instructions.py').write_text('raise RuntimeError()')
   external=Path(t)/'private.pdf';external.write_bytes(b'outside')
   (root/'link.pdf').symlink_to(external)
   m=inventory(root)
   self.assertEqual(len(m['files']),1)
   self.assertEqual(m['coverage'],'FILE_METADATA_ONLY_NOT_FULL_CONTENT_REVIEW')
 def test_repeat_inventory_idempotent_and_changes_versioned(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)/'PA';root.mkdir();p=root/'test.pdf';p.write_bytes(b'first')
   db=Path(t)/'disposable.sqlite3';m=inventory(root)
   self.assertEqual(ingest(m,db)['new_documents'],1)
   m['indexed_at']='2026-09-09T12:00:00Z'
   self.assertEqual(ingest(m,db)['unchanged'],1)
   p.write_bytes(b'other')
   self.assertEqual(ingest(inventory(root),db)['new_revisions'],1)
   with HubStore(db) as s:
    self.assertEqual(s.stats()['documents'],1)
    self.assertEqual(s.stats()['document_revisions'],2)
    row=s.list_documents()[0]
    self.assertFalse(row['metadata']['strategy_qualified'])
    self.assertIsNone(row['published_at'])
 def test_bound_rejects_too_many_files(self):
  with tempfile.TemporaryDirectory() as t:
   for i in range(201):(Path(t)/f'{i}.pdf').touch()
   with self.assertRaises(ValueError):inventory(t)
 def test_app_only_is_not_production_entitlement(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'state';p.mkdir()
   (p/'webull-capability-declaration.json').write_text(json.dumps({'purchaseScope':'APP_ONLY','credential':'MUST_NOT_RETURN'}))
   r=_webull(t)
   self.assertEqual(r['purchaseScope'],'APP_ONLY_USER_REPORTED')
   self.assertEqual(r['productionAccount'],'UNVERIFIED')
   self.assertEqual(r['environment'],'sandbox')
   self.assertFalse(r['ordersEnabled'])
   self.assertNotIn('MUST_NOT_RETURN',json.dumps(r))
 def test_malformed_capability_is_unknown(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'state';p.mkdir();(p/'webull-capability-declaration.json').write_text('[]')
   self.assertEqual(_webull(t)['purchaseScope'],'UNVERIFIED')
