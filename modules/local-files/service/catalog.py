"""Local catalog: explicit scope, content-addressed passages and independent retrieval.

No source file is modified. SQLite is authoritative; the vector cache is disposable.
"""
from __future__ import annotations
import hashlib, json, os, re, sqlite3, stat, threading, time, unicodedata
from pathlib import Path

TEXT_EXT = set('txt md markdown rst csv tsv json jsonl yaml yml toml xml html htm css scss js jsx ts tsx py rs go java c h cpp sql sh zsh fish swift kt rb r tex log'.split())
DOC_EXT = TEXT_EXT | {'pdf','docx'}
GROUPS = [('strateji','strategy','strategic'),('belge','dokuman','dokume','document','documentation'),('rapor','report'),('plan','roadmap','yolharita'),('mimari','architecture'),('butce','budget'),('sozlesme','contract','agreement'),('toplanti','meeting'),('fatura','invoice'),('arastirma','research'),('finans','finance','financial'),('pazarlama','marketing'),('guvenlik','security')]
STOP = set('ben benim bana bir ve veya ile icin nasil hangi nerede nerde nereden bul bulur bulabilir dosya dosyam dosyalar file files my where is the a of do you can find please dokuman document belgeler'.split())

def normalized(text):
    text=text.lower().replace('ı','i').replace('İ','i')
    return ''.join(c for c in unicodedata.normalize('NFKD',text) if not unicodedata.combining(c))

def query_terms(query):
    words=re.findall(r'[a-z0-9]+',normalized(query))[:40]
    formats=[w for w in words if w in DOC_EXT|{'pptx','xlsx','png','jpg','mp4','mp3','zip'}]
    terms=[]
    for word in words:
        if word in STOP or word in formats:continue
        group=next((g for g in GROUPS if any(word.startswith(x) for x in g)),None)
        if group and group[0]=='belge':continue
        terms.extend(group or [word])
    return list(dict.fromkeys(terms)),list(dict.fromkeys(formats))

class Catalog:
    def __init__(self, state:Path, config:dict):
        self.state=state;state.mkdir(parents=True,exist_ok=True,mode=0o700);state.chmod(0o700)
        self.root=Path(config['root']).resolve();self.excludes=[Path(p).absolute() for p in config['exclude']]+[state.resolve()]
        self.active=lambda:True
        self.max_bytes=config.get('max_content_bytes',20*1024*1024)
        self.config=config;self.lock=threading.RLock();self.scan_lock=threading.Lock();self.progress={'phase':'starting','visited':0,'unreadable_directories':0,'last_scan':None,'error':None}
        with self.db() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,path TEXT UNIQUE,name TEXT,extension TEXT,size INTEGER,mtime INTEGER,seen INTEGER,digest TEXT,status TEXT,error TEXT);
            CREATE INDEX IF NOT EXISTS file_digest ON files(digest);
            CREATE INDEX IF NOT EXISTS file_pending ON files(status);
            CREATE INDEX IF NOT EXISTS file_priority ON files((CASE WHEN extension IN ('pdf','docx','md','markdown') THEN 0 ELSE 1 END),id) WHERE status='pending';
            CREATE VIRTUAL TABLE IF NOT EXISTS names USING fts5(name,path,tokenize='unicode61 remove_diacritics 2',prefix='2 3 4');
            CREATE TABLE IF NOT EXISTS documents(digest TEXT PRIMARY KEY,characters INTEGER,passages INTEGER);
            CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY AUTOINCREMENT,digest TEXT,position INTEGER,body TEXT,vector BLOB,UNIQUE(digest,position));
            CREATE INDEX IF NOT EXISTS chunk_digest ON chunks(digest);
            CREATE VIRTUAL TABLE IF NOT EXISTS contents USING fts5(body,tokenize='unicode61 remove_diacritics 2',prefix='2 3 4');
            ''')
        with self.db() as db:
            if 'priority' not in [r[1] for r in db.execute('PRAGMA table_info(files)')]:db.execute('ALTER TABLE files ADD COLUMN priority INTEGER NOT NULL DEFAULT 1')
            db.execute("CREATE INDEX IF NOT EXISTS file_priority_v2 ON files(priority,(CASE WHEN extension IN ('pdf','docx','md','markdown') THEN 0 ELSE 1 END),id) WHERE status='pending'")
        with self.db() as db:
            db.execute("CREATE INDEX IF NOT EXISTS file_priority_v3 ON files(priority,mtime DESC,id) WHERE status='pending'")
        os.chmod(state/'catalog.sqlite3',0o600)
        self.extract_turn=0
        self.vector=None;self.vector_lock=threading.Lock();self.vector_error=None

    def db(self):
        db=sqlite3.connect(self.state/'catalog.sqlite3',timeout=30);db.row_factory=sqlite3.Row;return db

    def allowed(self,path):
        try:
            lexical=Path(os.path.abspath(path));real=lexical.resolve(strict=False)
            return lexical.is_relative_to(self.root) and real.is_relative_to(self.root) and not any(lexical.is_relative_to(p) or real.is_relative_to(p.resolve()) for p in self.excludes)
        except (OSError,ValueError,RuntimeError):return False

    def content_status(self,path,size):
        ext=path.suffix.lower().lstrip('.')
        if size>self.max_bytes:return 'too_large'
        if ext not in DOC_EXT:return 'metadata_only'
        # Authentication stores are catalogued by name, never sent to extraction/models.
        if path.name.startswith('.env') or path.name.lower() in {'credentials.json','auth.json','id_rsa','id_ed25519','cookies','login data'}:return 'metadata_only'
        return 'pending'

    def upsert(self,path,db,seen):
        if not self.allowed(path):return
        try:
            st=path.stat()
            if not stat.S_ISREG(st.st_mode):return
            old=db.execute('SELECT * FROM files WHERE path=?',(str(path),)).fetchone()
            if old and old['size']==st.st_size and old['mtime']==st.st_mtime_ns:
                db.execute('UPDATE files SET seen=? WHERE id=?',(seen,old['id']));return
            state=self.content_status(path,st.st_size)
            if old:
                fid=old['id'];db.execute('DELETE FROM names WHERE rowid=?',(fid,))
                db.execute('UPDATE files SET size=?,mtime=?,seen=?,digest=NULL,status=?,error=NULL WHERE id=?',(st.st_size,st.st_mtime_ns,seen,state,fid))
            else:
                fid=db.execute('INSERT INTO files(path,name,extension,size,mtime,seen,status) VALUES(?,?,?,?,?,?,?)',(str(path),path.name,path.suffix.lower().lstrip('.'),st.st_size,st.st_mtime_ns,seen,state)).lastrowid
            db.execute('INSERT INTO names(rowid,name,path) VALUES(?,?,?)',(fid,normalized(path.name),normalized(str(path.relative_to(self.root)))))
        except (OSError,ValueError):pass

    def scan(self):
        if not self.scan_lock.acquire(blocking=False):return
        self.progress.update(phase='scanning',visited=0,unreadable_directories=0,error=None);seen=time.time_ns();blocked=[]
        try:
            def error(exc):
                self.progress['unreadable_directories']+=1
                if exc.filename:blocked.append(str(exc.filename))
            # Walk does not follow directory symlinks; their canonical in-scope targets
            # are visited under the root. No red-excluded target can be reached via alias.
            batch=[]
            def flush():
                with self.lock,self.db() as db:
                    for path in batch:self.upsert(path,db,seen)
                batch.clear()
                time.sleep(.005)
            for folder,dirs,files in os.walk(self.root,followlinks=False,onerror=error):
                if not self.active():
                    if batch:flush()
                    self.progress['phase']='paused';return
                dirs[:]=[d for d in dirs if self.allowed(Path(folder)/d) and not (Path(folder)/d).is_symlink()]
                dirs.sort(key=lambda d:(d not in ('Documents','obsidian','Developer','conductor'),d))
                for name in files:
                    batch.append(Path(folder)/name);self.progress['visited']+=1
                    if len(batch)>=500:flush()
            if batch:flush()
            with self.lock,self.db() as db:
                # Inaccessible directories are reported, not misclassified as deletion.
                for row in db.execute('SELECT id,path FROM files WHERE seen<?',(seen,)).fetchall():
                    if any(row['path']==p or row['path'].startswith(p+os.sep) for p in blocked):continue
                    db.execute('DELETE FROM names WHERE rowid=?',(row['id'],));db.execute('DELETE FROM files WHERE id=?',(row['id'],))
                self.progress.update(phase='watching',last_scan=time.time())
        except Exception as exc:self.progress.update(phase='error',error=type(exc).__name__)
        finally:self.scan_lock.release()

    def change(self,path):
        path=Path(path)
        with self.lock,self.db() as db:
            if path.is_file():
                self.upsert(path,db,time.time_ns())
                db.execute("UPDATE files SET priority=0 WHERE path=? AND status='pending'",(str(path),))
            elif not path.exists():
                # Escaped LIKE patterns would be unsafe for literal filenames; use range.
                prefix=str(path)+os.sep
                rows=db.execute('SELECT id FROM files WHERE path=? OR (path>=? AND path<?)',(str(path),prefix,prefix+'\U0010ffff')).fetchall()
                for row in rows:
                    db.execute('DELETE FROM names WHERE rowid=?',(row['id'],));db.execute('DELETE FROM files WHERE id=?',(row['id'],))

    def read_bytes(self,path):
        if not self.allowed(path):raise ValueError('out_of_scope')
        real=Path(path).resolve(strict=True)
        # O_NOFOLLOW protects the final component; re-check canonical scope before read.
        fd=os.open(real,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            st=os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_size>self.max_bytes:raise ValueError('too_large')
            with os.fdopen(fd,'rb',closefd=False) as f:data=f.read(self.max_bytes+1)
            if len(data)>self.max_bytes:raise ValueError('too_large')
            return data
        finally:os.close(fd)

    def extract(self,data,ext):
        if ext in ('pdf','docx'):
            import subprocess,sys
            try:
                result=subprocess.run([sys.executable,str(Path(__file__).with_name('extract_document.py')),ext],input=data,capture_output=True,timeout=30)
                if result.returncode:raise ValueError('document_parse_failed')
                return result.stdout.decode('utf-8')
            except subprocess.TimeoutExpired:raise ValueError('document_parse_timeout')
        if b'\0' in data[:4096]:raise ValueError('binary_content')
        for enc in ('utf-8-sig','utf-16','cp1254'):
            try:return data.decode(enc)
            except UnicodeError:pass
        raise ValueError('unsupported_encoding')

    def process_one(self):
        self.extract_turn+=1
        # New/edited documents get a fast lane; one in eight slots still drains
        # initial coverage, so a busy workspace cannot starve the original backlog.
        order="id" if self.extract_turn%8==0 else "priority,mtime DESC,id"
        with self.db() as db:
            row=db.execute("SELECT * FROM files WHERE status='pending' ORDER BY "+order+" LIMIT 1").fetchone()
        if not row:return False
        try:
            data=self.read_bytes(row['path']);digest=hashlib.sha256(data).hexdigest()
            with self.db() as db:known=db.execute('SELECT 1 FROM documents WHERE digest=?',(digest,)).fetchone()
            if not known:
                text=self.extract(data,row['extension'])
                if not text.strip():raise ValueError('empty_or_ocr_required')
                # Paragraph-like bounded windows with overlap, without truncating document.
                parts=[text[i:i+1800] for i in range(0,len(text),1600)]
            with self.lock,self.db() as db:
                current=Path(row['path']).stat()
                if current.st_mtime_ns!=row['mtime'] or current.st_size!=row['size']:
                    self.upsert(Path(row['path']),db,time.time_ns());return True
                if not self.allowed(row['path']):raise ValueError('out_of_scope')
                if not known:
                    db.execute('INSERT OR IGNORE INTO documents VALUES(?,?,?)',(digest,len(text),len(parts)))
                    for i,body in enumerate(parts):
                        cursor=db.execute('INSERT OR IGNORE INTO chunks(digest,position,body) VALUES(?,?,?)',(digest,i,body))
                        if cursor.rowcount:db.execute('INSERT INTO contents(rowid,body) VALUES(?,?)',(cursor.lastrowid,normalized(body)))
                db.execute("UPDATE files SET digest=?,status='indexed',error=NULL WHERE id=? AND mtime=?",(digest,row['id'],row['mtime']))
        except Exception as exc:
            with self.db() as db:db.execute("UPDATE files SET status='unreadable',error=? WHERE id=? AND mtime=?",(str(exc)[:160] if isinstance(exc,ValueError) else type(exc).__name__,row['id'],row['mtime']))
        return True

    def load_vectors(self):
        from usearch.index import Index
        import numpy as np
        idx=Index(ndim=768,metric='cos',dtype='f16')
        with self.db() as db:
            cursor=db.execute('SELECT id,vector FROM chunks WHERE vector IS NOT NULL')
            while batch:=cursor.fetchmany(1000):idx.add(np.array([r['id'] for r in batch],dtype=np.uint64),np.stack([np.frombuffer(r['vector'],dtype=np.float32) for r in batch]))
        with self.vector_lock:self.vector=idx

    def embed_batch(self):
        import httpx,numpy as np
        with self.db() as db:rows=db.execute('SELECT id,body FROM chunks WHERE vector IS NULL AND digest IN (SELECT digest FROM files WHERE digest IS NOT NULL) LIMIT 8').fetchall()
        if not rows:return False
        with httpx.Client(timeout=60) as client:
            response=client.post(self.config.get('ollama_url','http://127.0.0.1:11434')+'/api/embed',json={'model':self.config['embedding_model'],'input':[r['body'] for r in rows],'truncate':False,'keep_alive':'10m'})
            response.raise_for_status();vectors=np.asarray(response.json()['embeddings'],dtype=np.float32)
        if vectors.shape!=(len(rows),768) or not np.isfinite(vectors).all():raise ValueError('invalid_embeddings')
        with self.lock,self.db() as db,self.vector_lock:
            for row,vec in zip(rows,vectors):db.execute('UPDATE chunks SET vector=? WHERE id=?',(vec.tobytes(),row['id']))
            self.vector.add(np.array([r['id'] for r in rows],dtype=np.uint64),vectors)
        self.vector_error=None;return True

    def status(self):
        with self.db() as db:
            counts={r[0]:r[1] for r in db.execute('SELECT status,count(*) FROM files GROUP BY status')}
            vectors=db.execute('SELECT count(*) FROM chunks WHERE vector IS NOT NULL').fetchone()[0]
            passages=db.execute('SELECT count(*) FROM chunks').fetchone()[0]
        return dict(self.progress,root=str(self.root),excluded=[str(p) for p in self.excludes],files=sum(counts.values()),counts=counts,passages=passages,vectors=vectors,semantic_error=self.vector_error,embedding_model=self.config['embedding_model'],directory_symlinks='canonical_targets_only',max_content_bytes=self.max_bytes)

    def search(self,query,limit=20):
        start=time.monotonic();terms,formats=query_terms(query);warnings=[];rankings=[];bodies={}
        expression=' OR '.join('"'+w+'"*' for w in terms)
        with self.db() as db:
            if expression:
                rankings.append([(r['rowid'],None) for r in db.execute('SELECT rowid FROM names WHERE names MATCH ? ORDER BY bm25(names,8,1) LIMIT 150',(expression,))])
                chunkrows=db.execute('SELECT rowid FROM contents WHERE contents MATCH ? ORDER BY bm25(contents) LIMIT 100',(expression,)).fetchall()
            else:chunkrows=[]
            lexical=[]
            for r in chunkrows:
                chunk=db.execute('SELECT digest,body FROM chunks WHERE id=?',(r[0],)).fetchone()
                for file in db.execute('SELECT id FROM files WHERE digest=? LIMIT 20',(chunk['digest'],)):
                    lexical.append((file['id'],chunk['body']))
            rankings.append(lexical)
            if not terms:
                sql='SELECT id FROM files';params=[]
                if formats:sql+=' WHERE extension IN ('+','.join('?' for _ in formats)+')';params=formats
                rankings.append([(r[0],None) for r in db.execute(sql+' ORDER BY mtime DESC LIMIT 150',params)])
            try:
                if self.vector is not None and len(self.vector) and terms:
                    import httpx,numpy as np
                    response=httpx.post(self.config.get('ollama_url','http://127.0.0.1:11434')+'/api/embed',json={'model':self.config['embedding_model'],'input':query,'truncate':False},timeout=12);response.raise_for_status()
                    q=np.asarray(response.json()['embeddings'][0],dtype=np.float32)
                    with self.vector_lock:matches=list(self.vector.search(q,count=80))
                    semantic=[]
                    for match in matches:
                        chunk=db.execute('SELECT digest,body FROM chunks WHERE id=?',(int(match.key),)).fetchone()
                        if chunk:
                            for file in db.execute('SELECT id FROM files WHERE digest=? LIMIT 20',(chunk['digest'],)):semantic.append((file['id'],chunk['body']))
                    rankings.append(semantic)
            except Exception:warnings.append('semantic_unavailable')
            scores={}
            for li,ranking in enumerate(rankings):
                seen=set()
                for i,(fid,body) in enumerate(ranking):
                    if fid in seen:continue
                    seen.add(fid);scores[fid]=scores.get(fid,0)+(1.5 if li==0 else 1)/(40+i+1)
                    if body:bodies.setdefault(fid,body)
            results=[];groups={}
            for fid in sorted(scores,key=scores.get,reverse=True):
                row=db.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
                if not row or (formats and row['extension'] not in formats) or not self.allowed(row['path']):continue
                try:
                    st=Path(row['path']).stat()
                    if st.st_mtime_ns!=row['mtime'] or st.st_size!=row['size']:continue
                except OSError:continue
                key=row['digest'] or row['path']
                if key in groups:
                    groups[key]['copies'].append(row['path']);continue
                item={k:row[k] for k in ('id','path','name','extension','size','mtime','status','error')}
                item.update(score=round(scores[fid],6),snippet=bodies.get(fid,'')[:1000],copies=[],sha256=row['digest'])
                groups[key]=item;results.append(item)
            if results and self.config.get('reranker_url'):
                try:
                    import httpx
                    shortlist=results[:min(32,len(results))]
                    key=Path(self.config['reranker_key_file']).read_text().strip()
                    response=httpx.post(self.config['reranker_url']+'/rerank',json={'query':query,'documents':[r['name']+'\n'+r['snippet'] for r in shortlist]},headers={'Authorization':'Bearer '+key},timeout=20)
                    response.raise_for_status();scores=response.json()['scores']
                    if len(scores)!=len(shortlist):raise ValueError('invalid_reranker_response')
                    if max(scores)<.001:warnings.append('weak_relevance')
                    else:
                        ordered=sorted(zip(shortlist,scores),key=lambda pair:pair[1],reverse=True)
                        results=[dict(item,relevance=score) for item,score in ordered]+results[len(shortlist):]
                except Exception:warnings.append('reranker_unavailable')
            return {'results':results[:limit],'query_terms':terms,'extensions':formats,'warnings':warnings,'timing_ms':round((time.monotonic()-start)*1000),'partial_index':self.progress['phase'] in ('starting','scanning') or self.status()['counts'].get('pending',0)>0,'retrieval':'filename BM25 + passage BM25 + multilingual HNSW + reciprocal rank fusion'}
