"""Bounded offline PA source inventory. Does not execute or OCR source material.

Usage: python3 scripts/index_local_pa.py '/path/to/AL brooks'
Indexes file names and SHA-256 provenance only; reading a file is not strategy qualification.
"""
from pathlib import Path
import argparse,hashlib,json,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from trading_hub.common import now_iso
from trading_hub.storage import HubStore,DEFAULT_DB


def inventory(root):
    root=Path(root).resolve()
    if not root.is_dir(): raise ValueError('Source folder missing')
    paths=sorted(p for p in root.rglob('*') if p.suffix.lower() in {'.pdf','.png'} and not p.is_symlink() and p.is_file() and p.resolve().is_relative_to(root))
    if len(paths)>200: raise ValueError('Bounded intake allows at most 200 files')
    if sum(p.stat().st_size for p in paths)>2_000_000_000: raise ValueError('Bounded intake allows at most 2 GB')
    rows=[]
    for p in paths:
        h=hashlib.sha256()
        with p.open('rb') as f:
            for chunk in iter(lambda:f.read(1048576),b''):h.update(chunk)
        rows.append({'relative_path':str(p.relative_to(root)),'sha256':h.hexdigest(),'bytes':p.stat().st_size,'kind':p.suffix.lower()[1:]})
    return {'schema':'trading-hub.local-pa-inventory.v1','source_root':str(root),'indexed_at':now_iso(),'coverage':'FILE_METADATA_ONLY_NOT_FULL_CONTENT_REVIEW','files':rows}


def ingest(manifest,db_path=DEFAULT_DB):
    root=Path(manifest['source_root'])
    docs=[]
    for row in manifest['files']:
        docs.append({'source':'al_brooks_local_inventory','source_id':row['relative_path'],
          'title':'PA source inventory: '+row['relative_path'],'text':row['relative_path'],
          'published_at':None,'known_at':manifest['indexed_at'],'coverage':manifest['coverage'],
          'metadata':{'authorship':'User-labelled collection; verify each source title','bytes':row['bytes'],'file_sha256':row['sha256'],'full_content_reviewed':False,'strategy_qualified':False},
          'artifact_refs':[{'path':str(root/row['relative_path']),'sha256':row['sha256']}]})
    with HubStore(db_path) as store:return store.ingest_documents('structure',docs)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('source');parser.add_argument('--db',type=Path,default=DEFAULT_DB)
    args=parser.parse_args();manifest=inventory(args.source)
    target=args.db.parent/'raw/research/local-pa/inventory.json';target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n');target.chmod(0o600)
    print(json.dumps({'files':len(manifest['files']),'counts':ingest(manifest,args.db),'full_content_reviewed':False}))
