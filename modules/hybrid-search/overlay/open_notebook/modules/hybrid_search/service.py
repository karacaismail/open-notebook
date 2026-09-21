"""Hybrid retrieval over a disposable, versioned passage index.

Original sources/notes/insights and original vectors are never written here.
"""
from __future__ import annotations
import asyncio
from collections import OrderedDict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import time

import httpx
from loguru import logger
from open_notebook.ai.models import Model, model_manager
from open_notebook.database.repository import repo_query, ensure_record_id, db_connection, parse_record_ids
from open_notebook.exceptions import ConfigurationError, DatabaseOperationError, InvalidInputError
from open_notebook.modules.registry import Registry
from .ranking import folded, query_terms, passages, fuse, exact_match, group_results, diversify

VERSION = 'hybrid-v1'
DEFAULTS = {'candidate_count': 60, 'rerank_limit': 32, 'ask_results': 12, 'rerank_enabled': True, 'sync_interval': 60}


def settings():
    registry = Registry()
    registry.require_enabled('hybrid-search')
    return registry.settings('hybrid-search', effective=True)


def stamp():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


async def checked_query(sql, variables=None):
    # The SDK query() returns only statement 0. Inspect EVERY statement so a
    # failed index or transaction cannot be reported as successfully committed.
    async with db_connection() as connection:
        response = await connection.query_raw(sql, variables or {})
    if response.get('error'):
        raise DatabaseOperationError('Hybrid database request failed')
    statements = response.get('result', [])
    for item in statements:
        if item.get('status') != 'OK':
            logger.error('Hybrid query error: {}', item.get('result'))
            raise DatabaseOperationError('Hybrid database statement failed')
    return parse_record_ids(statements[-1]['result']) if statements else []


class HybridSearch:
    def __init__(self, query=checked_query):
        self.query = query
        self.task = None
        self.periodic = None
        self.lock = asyncio.Lock()
        self.state = {'status': 'pending', 'processed': 0, 'total': 0, 'error': None}
        self.cache = OrderedDict()
        self.cache_lock = asyncio.Lock()
        self.ready_tables = set()

    async def model(self):
        defaults = await model_manager.get_defaults()
        if not defaults.default_embedding_model:
            raise ConfigurationError('Configure an embedding model to build the hybrid index.')
        spec = await Model.get(defaults.default_embedding_model)
        # Credential identity, model configuration and task/chunk version isolate vector spaces.
        signature = digest(json.dumps([VERSION, str(spec.id), spec.name, spec.provider, str(spec.credential)], ensure_ascii=False))
        return signature, spec

    async def metadata(self):
        docs = []
        for table, body, title, parent, kind in (
            ('source', 'full_text', "title OR ''", 'id', 'source'),
            ('note', 'content', "title OR ''", 'id', 'note'),
            ('source_insight', 'content', "(insight_type OR '') + ' — ' + (source.title OR '')", 'source.id', 'source'),
        ):
            rows = await self.query(f"SELECT id, {title} AS title, {parent} AS parent_id, '{kind}' AS kind, crypto::sha256(({title}) + '\\n' + ({body} OR '')) AS doc_hash, string::len({body} OR '') AS chars FROM {table}")
            docs.extend({**r, 'body_field': body} for r in rows if r.get('chars') and r.get('parent_id'))
        return docs

    async def prepare(self, table, dimension):
        if table in self.ready_tables:
            return
        if not table.startswith('hs_p_') or not table[5:].isalnum() or not 1 <= dimension <= 8192:
            raise InvalidInputError('Invalid hybrid index identity')
        await self.query(f"""
            DEFINE ANALYZER IF NOT EXISTS hs_tr TOKENIZERS blank,class,punct FILTERS lowercase,snowball(turkish);
            DEFINE ANALYZER IF NOT EXISTS hs_en TOKENIZERS blank,class,punct FILTERS lowercase,snowball(english);
            DEFINE INDEX IF NOT EXISTS hs_tr_text ON {table} FIELDS search_tr SEARCH ANALYZER hs_tr BM25;
            DEFINE INDEX IF NOT EXISTS hs_en_text ON {table} FIELDS search_en SEARCH ANALYZER hs_en BM25;
            DEFINE INDEX IF NOT EXISTS hs_doc ON {table} FIELDS doc_id;
            DEFINE INDEX IF NOT EXISTS hs_parent ON {table} FIELDS parent_id;
            DEFINE INDEX IF NOT EXISTS hs_vector ON {table} FIELDS embedding HNSW DIMENSION {dimension} DIST COSINE TYPE F32 EFC 200 M 16;
        """)
        self.ready_tables.add(table)

    async def active(self):
        rows = await self.query('SELECT * FROM hs_state:active')
        return rows[0] if rows else None

    async def start(self):
        if self.task and not self.task.done():
            return
        self.task = asyncio.create_task(self.sync())

    async def run_periodic(self):
        while True:
            try:
                cfg = settings()
                await self.start()
                await asyncio.sleep(cfg['sync_interval'])
            except asyncio.CancelledError:
                raise
            except Exception:
                # Disabled modules do not index or issue model requests.
                await asyncio.sleep(30)

    async def close(self):
        for task in (self.periodic, self.task):
            if task and not task.done():
                task.cancel()
        await asyncio.gather(*(t for t in (self.periodic, self.task) if t), return_exceptions=True)

    async def sync(self):
        async with self.lock:
            try:
                settings()
                signature, spec = await self.model()
                table = 'hs_p_' + signature[:16]
                docs = await self.metadata()
                self.state.update(status='indexing', processed=0, total=len(docs), error=None)
                records = await self.query('SELECT * FROM hs_document WHERE generation=$generation', {'generation': table})
                current = {r['doc_id']: r for r in records}
                model = await model_manager.get_embedding_model()
                active = await self.active()
                dimension = active['dimension'] if active and active['signature'] == signature else None
                if dimension is None:
                    dimension = len((await model.aembed(['task: search result | query: index dimension']))[0])
                await self.prepare(table, dimension)
                for doc in docs:
                    settings()  # Stop admitting new work as soon as the module is disabled.
                    old = current.get(doc['id'])
                    if old and old['doc_hash'] == doc['doc_hash']:
                        self.state['processed'] += 1
                        continue
                    rows = await self.query(f"SELECT {doc['body_field']} AS content FROM $record", {'record': ensure_record_id(doc['id'])})
                    if not rows:
                        continue
                    text = rows[0]['content']
                    # A concurrently edited source is retried on the next pass, never mislabeled.
                    if digest(doc['title'] + '\n' + text) != doc['doc_hash']:
                        continue
                    parts = list(passages(text))
                    vectors = []
                    for offset in range(0, len(parts), 16):
                        batch = parts[offset:offset+16]
                        title = doc['title'].encode()[:200].decode('utf-8', errors='ignore')
                        inputs = [('title: '+title+' | text: '+p['content']) if 'embeddinggemma' in spec.name.lower() else p['content'] for p in batch]
                        values = await asyncio.wait_for(model.aembed(inputs), timeout=180)
                        if len(values) != len(batch) or any(len(v) != dimension or any(not math.isfinite(x) for x in v) for v in values):
                            raise ValueError('Embedding count, dimension or finite-value validation failed')
                        vectors.extend(values)
                    payload = []
                    for n, (part, vector) in enumerate(zip(parts, vectors)):
                        payload.append({**part, 'id': ensure_record_id(table+':'+digest(doc['id']+doc['doc_hash']+str(n))),
                            'doc_id': doc['id'], 'parent_id': doc['parent_id'], 'title': doc['title'], 'kind': doc['kind'],
                            'doc_hash': doc['doc_hash'], 'part': n, 'embedding': vector,
                            'search_tr': folded(doc['title']+'\n'+part['content']), 'search_en': folded(doc['title']+'\n'+part['content'])})
                    # One transaction replaces only this document's derived passages.
                    await self.query(f"""BEGIN TRANSACTION;
                        DELETE {table} WHERE doc_id=$doc;
                        INSERT INTO {table} $rows;
                        UPSERT $record CONTENT $meta;
                        COMMIT TRANSACTION;""", {'doc': doc['id'], 'rows': payload,
                            'record': ensure_record_id('hs_document:'+digest(table+doc['id'])),
                            'meta': {'doc_id': doc['id'], 'doc_hash': doc['doc_hash'], 'generation': table, 'parts': len(parts)}})
                    self.state['processed'] += 1
                latest = await self.metadata()
                indexed_rows = await self.query('SELECT doc_id,doc_hash FROM hs_document WHERE generation=$generation', {'generation':table})
                indexed_hashes = {r['doc_id']:r['doc_hash'] for r in indexed_rows}
                if any(indexed_hashes.get(d['id']) != d['doc_hash'] for d in latest):
                    self.state.update(status='indexing', error=None)
                    return  # Retry next cycle; do not publish a partial new generation.
                docs = latest
                ids = [d['id'] for d in docs]
                await self.query(f'DELETE {table} WHERE doc_id NOT IN $ids', {'ids': ids})
                await self.query('DELETE hs_document WHERE generation=$generation AND doc_id NOT IN $ids', {'ids': ids, 'generation': table})
                # Only publish a completely built generation; old generations remain recoverable.
                await self.query('UPSERT hs_state:active CONTENT $state', {'state': {'table': table, 'signature': signature, 'dimension': dimension,
                    'model': spec.name, 'synced_at': stamp(), 'documents': len(docs), 'version': VERSION}})
                self.state.update(status='ready', error=None, synced_at=stamp())
            except asyncio.CancelledError:
                self.state['status'] = 'interrupted'
                raise
            except Exception as exc:
                logger.warning('Hybrid index maintenance failed: {}', type(exc).__name__)
                self.state.update(status='error', error=type(exc).__name__)

    async def status(self):
        active = await self.active()
        records = await self.query('SELECT parts FROM hs_document WHERE generation=$generation', {'generation': active['table'] if active else ''})
        return {**self.state, 'index': active, 'passages': sum(r['parts'] for r in records), 'version': VERSION}

    async def scope(self, docs, notebook_ids, sources, notes):
        allowed = None
        if notebook_ids:
            nbs = [ensure_record_id(x) for x in notebook_ids]
            links = await self.query('SELECT in AS parent_id FROM reference WHERE out IN $nbs', {'nbs': nbs})
            links += await self.query('SELECT in AS parent_id FROM artifact WHERE out IN $nbs', {'nbs': nbs})
            allowed = {str(r['parent_id']) for r in links}
        return [d for d in docs if ((sources and d['kind'] == 'source') or (notes and d['kind'] == 'note'))
                and (allowed is None or d['parent_id'] in allowed)]

    async def lexical(self, table, terms, valid, count):
        if not terms or not valid:
            return {}
        # SurrealDB 2.6 does not support OR across full-text matches. Each term
        # uses an index; their bounded candidate lists are combined in Python.
        vars = {'valid': valid, 'count': count}
        clauses = []; names = []
        for language in ('tr', 'en'):
            for i, term in enumerate(terms[:8]):
                name = f'{language}{i}'; names.append(name); vars['q'+str(i)] = term
                clauses.append(f'LET ${name}=(SELECT id,doc_id,parent_id,title,kind,doc_hash,part,content,start,end,sha256, search::score(0) AS lexical_score FROM {table} WITH INDEX hs_{language}_text WHERE search_{language} @0@ $q{i} AND doc_id IN $valid ORDER BY lexical_score DESC LIMIT $count);')
        value = await self.query('\n'.join(clauses)+'\nRETURN {'+', '.join(f'{n}: ${n}' for n in names)+'};', vars)
        if isinstance(value, list) and value and isinstance(value[-1], dict) and 'tr0' in value[-1]:
            value = value[-1]
        combined = {}
        for language in ('tr', 'en'):
            rows = {}
            for name in [n for n in names if n.startswith(language)]:
                for row in value.get(name, []):
                    old = rows.setdefault(str(row['id']), {**row, 'lexical_total': 0.0})
                    old['lexical_total'] += max(0.0, float(row.get('lexical_score') or 0)) + .05
            combined['bm25_'+language] = sorted(rows.values(), key=lambda r: (-r['lexical_total'], str(r['id'])))[:count]
        return combined

    async def vector(self, table, query, signature, spec, valid, count, scoped):
        key = (signature, query)
        async with self.cache_lock:
            embed = self.cache.get(key)
            if embed is None:
                model = await model_manager.get_embedding_model()
                value = 'task: search result | query: '+query if 'embeddinggemma' in spec.name.lower() else query
                embed = (await asyncio.wait_for(model.aembed([value]), timeout=15))[0]
                self.cache[key] = embed
                if len(self.cache)>128:
                    self.cache.popitem(last=False)
        # Exact scoped cosine prevents post-filter ANN from silently starving a
        # small notebook. Global retrieval uses HNSW, widening only when needed.
        fields = 'id,doc_id,parent_id,title,kind,doc_hash,part,content,start,end,sha256'
        if scoped:
            return await self.query(f'SELECT {fields}, vector::similarity::cosine(embedding,$embed) AS similarity FROM {table} WHERE doc_id IN $valid ORDER BY similarity DESC LIMIT $count', {'embed': embed, 'valid': valid, 'count': count})
        result = await self.query(f'SELECT {fields}, vector::distance::knn() AS distance FROM {table} WHERE embedding <|{count},200|> $embed ORDER BY distance ASC', {'embed': embed})
        result = [r for r in result if r['doc_id'] in valid]
        if len(result) < count:
            # ANN candidates may contain deleted/stale documents or fewer than
            # requested eligible hits. Exact fallback guarantees scoped recall.
            return await self.query(f'SELECT {fields}, vector::similarity::cosine(embedding,$embed) AS similarity FROM {table} WHERE doc_id IN $valid ORDER BY similarity DESC LIMIT $count', {'embed':embed,'valid':valid,'count':count})
        return result

    async def rerank(self, query, rows, count):
        key_path = Path(os.getenv('LOCAL_RERANKER_KEY_FILE', 'data/hybrid-search/reranker.key'))
        base = os.getenv('LOCAL_RERANKER_URL', 'http://host.lima.internal:8321')
        key = key_path.read_text().strip()
        # Identical content gets one score, but every original attribution remains.
        unique = list(dict.fromkeys(r['title']+'\n'+r['content'] for r in rows[:count]))
        async with httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False) as client:
            response = await client.post(base+'/rerank', headers={'Authorization': 'Bearer '+key}, json={'query': query, 'documents': unique})
            response.raise_for_status(); values = response.json()['scores']
        if len(values) != len(unique) or any(not math.isfinite(float(v)) for v in values):
            raise ValueError('Invalid reranker scores')
        scores = dict(zip(unique, values))
        scored = [{**r, 'rerank_score': scores[r['title']+'\n'+r['content']]} for r in rows[:count]]
        scored.sort(key=lambda r: (not exact_match(query, r), -r['rerank_score'], -r['rrf_score']))
        return scored + rows[count:]

    async def search(self, keyword, results=30, source=True, note=True, notebook_ids=None, rerank=None):
        start = time.perf_counter(); cfg = settings(); warnings = []; timings = {}
        if not keyword.strip() or len(keyword)>4000:
            raise InvalidInputError('Search query must contain 1–4000 characters.')
        active = await self.active()
        if not active:
            await self.start()
            raise ConfigurationError('Hybrid index is being prepared. Check Search index status and retry shortly.')
        docs = await self.scope(await self.metadata(), notebook_ids, source, note)
        records = await self.query('SELECT doc_id,doc_hash FROM hs_document WHERE generation=$generation', {'generation': active['table']})
        indexed = {r['doc_id']: r['doc_hash'] for r in records}
        valid = [d['id'] for d in docs if indexed.get(d['id']) == d['doc_hash']]
        stale = len(docs)-len(valid)
        if stale:
            warnings.append('index_updating'); await self.start()
        count = max(cfg['candidate_count'], min(results*3, 150))
        try:
            signature, spec = await self.model()
        except Exception:
            signature, spec = None, None
        vector_ok = signature == active['signature']
        if not vector_ok:
            warnings.append('embedding_changed'); await self.start()
        table = active['table']
        # Select only hashes from the requested scope BEFORE limiting BM25 or cosine.
        tasks = [self.lexical(table, query_terms(keyword), valid, count)]
        if vector_ok and valid:
            tasks.append(self.vector(table, keyword, signature, spec, valid, count, bool(notebook_ids) or not(source and note)))
        before=time.perf_counter(); responses = await asyncio.gather(*tasks, return_exceptions=True)
        rankings = {}; errors = []
        for i, value in enumerate(responses):
            if isinstance(value, Exception):
                errors.append(type(value).__name__); warnings.append('lexical_unavailable' if i==0 else 'vector_unavailable')
            elif i == 0:
                rankings.update(value)
            else:
                rankings['vector'] = value
        if len(errors)==len(tasks):
            raise DatabaseOperationError('All hybrid retrieval channels failed; no answer should be inferred from this failure.')
        timings['retrieval_ms'] = round((time.perf_counter()-before)*1000)
        candidates = fuse(rankings)
        candidates.sort(key=lambda r: (not exact_match(keyword,r), -r['rrf_score']))
        # Hash filtering is not an authorization primitive; enforce original IDs
        # too, so equal content in a different notebook cannot cross scope.
        allowed = set(valid)
        candidates = [r for r in candidates if r['doc_id'] in allowed]
        candidates = diversify(candidates)
        used = False
        if candidates and (cfg['rerank_enabled'] if rerank is None else rerank):
            before=time.perf_counter()
            try:
                fused_candidates = list(candidates)
                candidates = await self.rerank(keyword, candidates, cfg['rerank_limit']); used=True
                # Abstain from neural ordering when every scored match is weak.
                # Preserve the alternatives; this threshold is not truth confidence.
                if max((r.get('rerank_score', 0) for r in candidates[:cfg['rerank_limit']]), default=0) < .001:
                    warnings.append('weak_relevance')
                    candidates = fused_candidates
                    used = False
            except Exception:
                warnings.append('reranker_unavailable')
                candidates.sort(key=lambda r: (not exact_match(keyword,r), -r['rrf_score']))
            timings['rerank_ms'] = round((time.perf_counter()-before)*1000)
        if not used:
            candidates.sort(key=lambda r: (not exact_match(keyword,r), -r['rrf_score']))
        rows = group_results(candidates, results)
        timings['total_ms'] = round((time.perf_counter()-start)*1000)
        return rows, {'mode':'hybrid','warnings':warnings,'channels':list(rankings),'reranked':used,
            'candidates':len(candidates),'indexed_documents':len(valid),'pending_documents':stale,
            'index_version':VERSION,'embedding_model':active['model'],'timings':timings}


engine = HybridSearch()
