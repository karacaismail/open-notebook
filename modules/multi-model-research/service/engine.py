from __future__ import annotations
import asyncio
import copy
import json
from pathlib import Path
import uuid
import httpx
from browser_runtime import BrowserAttention
from datetime import datetime, timedelta, timezone
from workflow import ATTENTION, AUTO_RETRY, RETRY_BACKOFF, SYSTEM, ancestors, citations, digest, initial_stages, now, prompt_for, ready, refresh_status, report_packet
from evidence_protocol import VERSION, audit_report, audit_appendix
from packet_markdown import FORMAT, evidence_identity
from token_budget import MARKDOWN_TRANSPORT
from research_rules import ResearchRules

class ServiceError(Exception):
    def __init__(self,message,status=400,kind=None,policy=None):
        super().__init__(message);self.status=status;self.kind=kind;self.policy=policy

class AccountProvider:
    def __init__(self,key_path,timeout=3900):
        self.key_path=key_path
        # Must exceed the bridge's own CLI timeout so the bridge reports the real reason.
        self.timeout=timeout
    async def synthesize(self,stage,prompt):
        model={'ChatGPT':'chatgpt-account','Claude':'claude-account'}[stage['provider']]
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            if stage.get('account_input_format')==MARKDOWN_TRANSPORT:
                health=await client.get('http://127.0.0.1:8317/health',timeout=10)
                if health.status_code!=200 or MARKDOWN_TRANSPORT not in health.json().get('prompt_formats',[]):
                    raise ServiceError('Hesap köprüsü kayıpsız Markdown taşımasını desteklemiyor. Köprüyü güncelleyin; model isteği gönderilmedi.',503)
                expected = stage.get('input_budget', {}).get('token_margin', {}).get('calibration_fingerprint')
                policy = ResearchRules.provider_check(expected, health.json().get('runtime_fingerprints', {}).get(model))
                if policy['blocked']:
                    raise ServiceError(policy['findings'][0]['message'],503,kind='calibration_required',policy=policy)
            response=await client.post('http://127.0.0.1:8317/v1/chat/completions',
                headers={'Authorization':'Bearer '+self.key_path.read_text().strip()},
                json={'model':model,'local_profile':'research_synthesis','stream':False,
                      'local_prompt_format':stage.get('account_input_format','json-v1'),
                      'messages':[{'role':'system','content':SYSTEM},{'role':'user','content':prompt}]})
        if response.status_code!=200:
            messages={401:'Hesap oturumu gerekli.',403:'Sağlayıcı bu sentez isteğini reddetti.',
                      429:'Hesap kotası doldu. Kota yenilendikten sonra devam edebilirsiniz.',
                      504:'Hesap isteği zaman aşımına uğradı.'}
            raise ServiceError(messages.get(response.status_code,'Hesap bağlantısı hata verdi ('+str(response.status_code)+').'),502)
        data=response.json();choice=data['choices'][0];text=choice['message'].get('content','')
        if choice.get('finish_reason')=='length':raise ServiceError('Yanıt çıktı sınırında kesildi; tamamlanmış sayılmadı.',502)
        if not text.strip():raise ServiceError('Hesap boş yanıt döndürdü.',502)
        usage = data.get('usage',{})
        if data.get('execution'):
            usage = dict(usage, execution=data['execution'])
        return text,usage

class NotebookSink:
    def __init__(self,password=''):self.password=password
    async def request(self,method,path,data=None):
        headers={'Authorization':'Bearer '+self.password} if self.password else {}
        async with httpx.AsyncClient(timeout=90) as client:
            response=await client.request(method,'http://127.0.0.1:5055/api/'+path,json=data,headers=headers)
            response.raise_for_status();return response.json()
    async def notebook(self,run):
        if run.get('notebook_id'):return run['notebook_id']
        # Reconcile a lost POST response using a unique, stable marker.
        marker='[research:'+run['id']+']'
        notebooks=await self.request('GET','notebooks')
        found=next((n for n in notebooks if marker in (n.get('description') or '')),None)
        if found:return found['id']
        value=await self.request('POST','notebooks',{'name':run['question'][:90],
            'description':marker+'\nÇok modelli araştırma\n'+run['scope']})
        return value['id']
    async def note(self,run,stage):
        title='[Araştırma '+run['id'][:8]+'/'+stage['id']+'] '+stage['provider']
        notes=await self.request('GET','notes?notebook_id='+run['notebook_id'])
        found=next((n for n in notes if n.get('title')==title),None)
        if found:return found['id']
        report=stage['report']
        content=('# '+run['question']+'\n\n**Aşama:** '+stage['id']+'\n\n**Kaynak:** '+report['provenance']+
                 '\n\n**Kanıtlar için hedef tarih:** '+(run.get('as_of') or run['created_at'][:10])+
                 '\n\n**Bildirilen araştırma tarihi:** '+(report.get('researched_at') or 'Bilinmiyor')+
                 '\n\n**Kaydedilme zamanı:** '+str(stage.get('finished_at') or '')+
                 '\n\n**Orijinal rapor bağlantısı:** '+(report.get('origin_url') or 'Belirtilmedi')+
                 '\n\n'+report['content'])
        for evidence in report.get('evidence',[]):content+='\n\n---\n## Ek kanıt: '+evidence['name']+'\n\n'+evidence['content']
        content += audit_appendix(run, stage)
        value=await self.request('POST','notes',{'title':title,'content':content,'note_type':'ai','notebook_id':run['notebook_id']})
        return value['id']

class Engine:
    def __init__(self,store,provider,sink,token_counter,token_limit=90000,browser=None,budget=None,input_limits=None):
        self.store=store;self.provider=provider;self.sink=sink;self.browser=browser
        self.token_counter=token_counter;self.token_limit=token_limit
        self.budget=budget
        self.input_limits=dict(input_limits or {})
        for provider,limit in self.input_limits.items():
            if provider not in ('ChatGPT','Claude') or type(limit) is not int or limit<=0:
                raise ValueError('Provider input limit must name an account provider and be positive.')
            if limit>token_limit and (not budget or not getattr(budget.margins.get(provider),'calibration_fingerprint',None)):
                raise ValueError('A larger provider budget requires runtime-bound measured calibration.')
        self.rules=ResearchRules()
        self.plan_cache={}
        self.lock=asyncio.Lock();self.tasks={};self.sync_locks={}
        self.provider_locks={p:asyncio.Lock() for p in ('ChatGPT','Claude','Gemini')}
        self.background=set()
        self.retry_task=None
    async def recover(self):
        resume=[]
        async with self.lock:
            for run in await self.store.all():
                for s in run['stages']:
                    # Adopt the lossless format only for never-submitted account stages.
                    # Sent packets, completed imports and browser submission journals stay pinned.
                    if s['mode']=='account' and not s['attempts'] and s['status'] not in ('completed','running','submission_uncertain'):
                        s.update(packet_format=FORMAT,account_input_format=MARKDOWN_TRANSPORT)
                    # A browser job survives both a crash and an orderly shutdown: its journal
                    # is reconciled against the provider before anything is ever re-sent.
                    if s['mode']=='browser' and s['status'] in ('running','interrupted') and self.browser and self.browser.can_resume(run['id'],s['id']):
                        s['status']='ready';s['error']=None
                    elif s['status']=='running':
                        s['status']='interrupted';s['error']='Servis yeniden başladı. Tamamlanan raporlar korundu; bu aşamayı açıkça devam ettirin.'
                refresh_status(run);await self.store.save(run)
                if run.get('execution_mode')=='browser' and not run['paused']:resume.append(run['id'])
        for run_id in resume:await self.kick(run_id)
        if self.retry_task is None:self.retry_task=asyncio.create_task(self.retry_loop())
        # Browser jobs reconcile their durable submission journal; account calls never auto-repeat.
    async def get(self,run_id):
        run=await self.store.get(run_id)
        if run is None:raise ServiceError('Araştırma bulunamadı.',404)
        return run
    @staticmethod
    def stage(run,stage_id):
        for stage in run['stages']:
            if stage['id']==stage_id:return stage
        raise ServiceError('Aşama bulunamadı.',404)
    async def create(self,data,key):
        fingerprint=digest(json.dumps(data,sort_keys=True,ensure_ascii=False))
        async with self.lock:
            old=await self.store.by_key(key)
            if old:
                if old['request_fingerprint']!=fingerprint:raise ServiceError('Aynı istek kimliği farklı bir soru için kullanılamaz.',409)
                return old
            run={'id':uuid.uuid4().hex,'idempotency_key':key,'request_fingerprint':fingerprint,
                 **data,'prompt_version':VERSION,'packet_format':FORMAT,'created_at':now(),'updated_at':now(),'paused':False,'status':'waiting_input',
                 'notebook_id':data.get('notebook_id'),'sync_error':None,'stages':initial_stages(data.get('execution_mode','imports'))}
            for s in run['stages']:
                if s['mode']=='account':s['account_input_format']=MARKDOWN_TRANSPORT
            await self.store.save(run)
        if data.get('execution_mode')=='browser':
            await self.kick(run['id'])
            return await self.get(run['id'])
        return run
    async def packet(self,run_id,stage_id):
        run=await self.get(run_id);stage=self.stage(run,stage_id)
        if not ready(run,stage):raise ServiceError('Önceki tur tamamlanmadan bu paketi oluşturamazsınız.',409)
        prompt=self.input_prompt(run,stage)
        budget=await asyncio.to_thread(self.measure_input,prompt,stage)
        identity=self.evidence_id(prompt)
        # Historical exports are not new submission candidates. Recorded policy
        # remains on the stage; do not display a new blocking decision on completed work.
        policy={} if stage['status']=='completed' else {'policy':await asyncio.to_thread(self.check_packet,run,stage,budget)}
        if policy:self.check_shared(run,stage,policy['policy'],identity)
        return {'prompt':prompt,'sha256':digest(prompt),'evidence_packet':identity,**budget,**policy,
                'report_count':len(ancestors(run,stage))}

    @staticmethod
    def evidence_id(prompt):
        try:return evidence_identity(prompt)
        except ValueError as exc:raise ServiceError(str(exc),409,kind='integrity_error') from exc

    @staticmethod
    def check_shared(run,stage,policy,identity):
        peers=[s for s in run['stages'] if s['round']==stage['round'] and s['id']!=stage['id'] and s.get('evidence_packet')]
        mismatches=[s['id'] for s in peers if s['evidence_packet']!=identity]
        policy['rules_evaluated']+=1
        if mismatches:
            policy.update(blocked=True,block_status='integrity_error')
            policy['findings'].append({'id':'ECA-018','action':'block_submission','severity':'error',
                'message':'Aynı turdaki modellere verilen ortak kanıt dosyası eşleşmiyor. Farklı veriyle gönderim durduruldu.',
                'evidence':{'different_packet_stages':mismatches}})

    def check_packet(self,run,stage,budget,event='packet_prepared'):
        if stage['attempts']:
            # input_prompt has checked the saved bytes. Never re-audit a different
            # live reconstruction as if it were that immutable sent packet.
            policy=copy.deepcopy(next((p for p in reversed(stage.get('policy_events',[]))
                if p['event']=='before_submit' and not p['blocked'] and p.get('input_sha256')==stage.get('input_sha256')),None))
            if policy is None:
                policy={'version':'research-eca-v1','rules_evaluated':0,'findings':[{
                    'id':'ECA-017','severity':'warning','action':'preserve_and_warn',
                    'message':'Bu kayıtlı girdi kurallar eklenmeden önce gönderilmiş. SHA-256 doğrulandı; eski gönderime geriye dönük denetim yapılmış sayılmaz.',
                    'evidence':{'saved_input':True}}],'factual_verification':False}
            policy.update(event=event,blocked=False,block_status=None)
            policy['findings']=[f for f in policy['findings'] if f['id'] not in ('ECA-010','ECA-011')]
            if stage['mode']=='account' and budget['estimated_tokens']>budget['automatic_input_limit']:
                policy.update(blocked=True,block_status='context_limit')
                policy['findings'].append({'id':'ECA-011','severity':'error','action':'block_submission',
                    'message':'Kaydedilmiş tam girdi güncel bütçeyi aşıyor; değiştirilmedi ve yeniden gönderilmedi.',
                    'evidence':{'counted_tokens':budget['estimated_tokens']}})
            return policy
        compact=(stage.get('packet_format') or run.get('packet_format'))==FORMAT and not stage['attempts']
        return self.rules.evaluate(report_packet(run,stage),budget,event,account=stage['mode']=='account',compact=compact)

    @staticmethod
    def record_policy(stage,policy,prompt_hash=None):
        record=dict(policy,checked_at=now(),input_sha256=prompt_hash)
        stage['policy']=record
        stage.setdefault('policy_events',[]).append(record)

    def measure_input(self,prompt,stage):
        limit=self.input_limits.get(stage['provider'],self.token_limit)
        if self.budget:
            return self.budget.measure(prompt,stage['provider'],limit,SYSTEM,
                                       stage.get('account_input_format','json-v1'))
        # Existing integrations that inject an already-adjusted counter remain compatible.
        count=self.token_counter(prompt)
        return {'estimated_tokens':count,'automatic_input_limit':limit}

    async def context_plan(self,run_id):
        run=await self.get(run_id)
        if not self.budget:return {'stages':[]}
        if not all(s['status']=='completed' for s in run['stages'] if s['round']<=2):
            return {'stages':[]}
        signature=digest(json.dumps([(s['id'],s['status'],s.get('input_sha256'),s.get('packet_format'),
                     s.get('account_input_format'),(s.get('report') or {}).get('sha256')) for s in run['stages']]))
        if (run_id,signature) in self.plan_cache:return self.plan_cache[(run_id,signature)]
        result=await asyncio.to_thread(self._context_plan,run)
        if len(self.plan_cache)>=8:self.plan_cache.clear()
        self.plan_cache[(run_id,signature)]=result
        return result

    def _context_plan(self,run):
        rows=[]
        for stage in run['stages']:
            if stage['mode']!='account' or stage['status']=='completed':continue
            missing=[s for s in ancestors(run,stage) if s['status']!='completed']
            try:
                if not missing:
                    prompt=self.input_prompt(run,stage)
                else:
                    # A projection only: never submit this incomplete reference packet.
                    projected=copy.deepcopy(run)
                    projected['stages']=[s for s in projected['stages'] if s['status']=='completed' or s['id']==stage['id']]
                    prompt=prompt_for(projected,stage)
                measured=self.measure_input(prompt,stage)
                reserve=sum(self.budget.output_tokens_by_round.get(s['round'],32000) for s in missing)
                estimated=self.budget.from_counts(measured['raw_tokens']+reserve,
                    measured['transport_raw_tokens']+reserve,stage['provider'],measured['automatic_input_limit'],
                    stage.get('account_input_format','json-v1')) if missing else measured
                rows.append({'stage_id':stage['id'],'provider':stage['provider'],'round':stage['round'],
                    **estimated,'projection':bool(missing),'known_raw_tokens':measured['raw_tokens'],
                    'missing_reports':len(missing),'reserved_report_tokens':reserve,
                    'output_tokens_by_round':self.budget.output_tokens_by_round,
                    'warning':estimated['utilization']>=.85})
            except (ServiceError,ValueError) as exc:
                rows.append({'stage_id':stage['id'],'provider':stage['provider'],'round':stage['round'],
                             'error':str(exc),'warning':True})
        return {'stages':rows}
    def input_path(self,run,stage):
        return self.store.root/'artifacts'/run['id']/stage['id']/'input-packet.md'
    def input_prompt(self,run,stage):
        path=self.input_path(run,stage)
        if stage['attempts'] and path.is_file():
            text=path.read_bytes().decode('utf-8')
            if digest(text)!=stage['input_sha256']:
                raise ServiceError('Kaydedilmiş girdi paketi özeti uyuşmuyor; istek yeniden gönderilmedi.',409)
            return text
        if stage['attempts'] and stage.get('input_sha256'):
            raise ServiceError('Gönderilmiş girdi paketi bulunamadı; değiştirilmiş bir paketle yeniden gönderilmedi.',409)
        try:
            return prompt_for(run,stage)
        except ValueError as exc:
            raise ServiceError(str(exc),409) from exc

    def audit(self, run, stage):
        if run.get('prompt_version',1) >= VERSION:
            stage['evidence_audit'] = audit_report(run,stage)
    async def import_report(self,run_id,stage_id,text,evidence,origin_url,packet_sha,files,researched_at=None):
        report_hash=digest(json.dumps({'content':text,'evidence':evidence,'researched_at':researched_at},ensure_ascii=False,sort_keys=True))
        async with self.lock:
            run=await self.get(run_id);stage=self.stage(run,stage_id)
            if stage['status']=='completed':
                if stage['report']['sha256']==report_hash:return run
                raise ServiceError('Bu aşamada zaten bir rapor var. Tamamlanmış raporlar değiştirilmez; yeni bir araştırma oluşturun.',409)
            if stage['status']=='running':raise ServiceError('Çalışan sentez tamamlanmadan rapor içe aktarılamaz.',409)
            if not ready(run,stage):raise ServiceError('Önceki turdaki bütün raporlar gerekli.',409)
            expected=digest(self.input_prompt(run,stage))
            if packet_sha!=expected:raise ServiceError('Rapor paketi güncel değil. Güncel paketi yeniden açın.',409)
            folder=self.store.root/'artifacts'/run_id/stage_id;folder.mkdir(parents=True,exist_ok=True,mode=0o700)
            stored=[]
            for i,(name,raw) in enumerate(files):
                suffix=Path(name).suffix.lower();filename=str(i)+suffix
                target=folder/filename;target.write_bytes(raw);target.chmod(0o600)
                stored.append({'name':Path(name).name,'file':filename,'sha256':digest(raw),'bytes':len(raw)})
            combined=text+'\n'+'\n'.join(e['content'] for e in evidence)
            stage.update(status='completed',error=None,finished_at=now(),input_sha256=expected,
                report={'content':text,'evidence':evidence,'sha256':report_hash,'citations':citations(combined),
                        'origin_url':origin_url,'provenance':'web_deep_research_import' if stage['mode'] in ('import','browser') else 'manual_synthesis_import',
                        'original_files':stored,'researched_at':researched_at})
            self.audit(run, stage)
            refresh_status(run);await self.store.save(run)
        await self.kick(run_id);self.schedule_sync(run_id)
        return await self.get(run_id)
    def schedule_sync(self,run_id):
        task=asyncio.create_task(self.sync(run_id));self.background.add(task);task.add_done_callback(self.background.discard)
    async def sync(self,run_id):
        lock=self.sync_locks.setdefault(run_id,asyncio.Lock())
        async with lock:
            try:
                run=await self.get(run_id)
                if not any(s['status']=='completed' for s in run['stages']):return run
                notebook_id=await self.sink.notebook(run)
                async with self.lock:
                    run=await self.get(run_id);run['notebook_id']=notebook_id;run['sync_error']=None;await self.store.save(run)
                for stage in run['stages']:
                    if stage['status']!='completed' or stage.get('note_id'):continue
                    note_id=await self.sink.note(run,stage)
                    async with self.lock:
                        latest=await self.get(run_id);self.stage(latest,stage['id'])['note_id']=note_id;await self.store.save(latest)
            except Exception:
                async with self.lock:
                    run=await self.get(run_id);run['sync_error']='Not defterine aktarım tamamlanamadı. Raporlar korunuyor; aktarımı tekrar deneyin.';await self.store.save(run)
            return await self.get(run_id)
    async def kick(self,run_id):
        async with self.lock:
            run=await self.get(run_id)
            if run['paused']:return
            launches=[]
            for stage in run['stages']:
                if stage['mode'] not in ('account','browser') or stage['status']!='ready':continue
                # auto_synthesize pauses the synthesis rounds only. A web research stage that
                # is already ready is part of the single-question flow, not an optional extra.
                if stage['mode']=='account' and not run['auto_synthesize']:continue
                try:
                    prompt=self.input_prompt(run,stage)
                    identity=self.evidence_id(prompt)
                except ServiceError as exc:
                    policy={'version':'research-eca-v1','event':'before_submit','rules_evaluated':1,
                        'blocked':True,'block_status':'integrity_error','factual_verification':False,
                        'findings':[{'id':'ECA-016','action':'block_submission','severity':'error',
                        'message':str(exc),'evidence':{'input_integrity':False}}]}
                    self.record_policy(stage,policy)
                    stage.update(status='integrity_error',error=str(exc),next_retry_at=None);continue
                measured=await asyncio.to_thread(self.measure_input,prompt,stage)
                count=measured['estimated_tokens']
                stage['estimated_input_tokens']=count
                stage['input_budget']=measured
                policy=await asyncio.to_thread(self.check_packet,run,stage,measured,'before_submit')
                self.check_shared(run,stage,policy,identity)
                stage['evidence_packet']=identity
                self.record_policy(stage,policy,digest(prompt))
                if policy['blocked']:
                    reasons=' '.join(f['message'] for f in policy['findings'] if f['action']=='block_submission')
                    stage.update(status=policy['block_status'],next_retry_at=None,error=reasons+f' Girdi: {count:,}; sınır: {measured["automatic_input_limit"]:,}.');continue
                path=self.input_path(run,stage);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                temporary=path.with_suffix('.tmp');temporary.touch(mode=0o600)
                temporary.write_text(prompt);temporary.replace(path)
                stage.update(status='running',started_at=now(),finished_at=None,error=None,attempts=stage['attempts']+1,input_sha256=digest(prompt))
                launches.append((stage['id'],prompt))
            refresh_status(run);await self.store.save(run)
            for stage_id,prompt in launches:
                task=asyncio.create_task(self.execute(run_id,stage_id,prompt));self.tasks[(run_id,stage_id)]=task
    async def browser_progress(self,run_id,stage_id,progress):
        async with self.lock:
            run=await self.get(run_id);stage=self.stage(run,stage_id)
            stage['browser_progress']=progress;run['updated_at']=now();await self.store.save(run)

    async def execute(self,run_id,stage_id,prompt):
        try:
            run=await self.get(run_id);stage=self.stage(run,stage_id)
            async with self.provider_locks[stage['provider']]:
                async with self.lock:
                    latest=await self.get(run_id)
                    if latest['paused']:
                        queued=self.stage(latest,stage_id)
                        queued.update(status='ready',started_at=None,attempts=max(0,queued['attempts']-1))
                        refresh_status(latest);await self.store.save(latest)
                        return
                browser_report=None
                if stage['mode']=='browser':
                    if not self.browser:raise BrowserAttention('Tarayıcı araştırma bağlantısı kurulmadı.','browser_unavailable')
                    browser_report=await self.browser.research(run_id,stage,prompt,lambda value:self.browser_progress(run_id,stage_id,value))
                    text=browser_report['content'];usage={}
                else:
                    text,usage=await self.provider.synthesize(stage,prompt)
            async with self.lock:
                run=await self.get(run_id);stage=self.stage(run,stage_id)
                stage.update(status='completed',finished_at=now(),usage=usage,error=None,
                    retry_index=0,next_retry_at=None,
                    report={'content':text,'sha256':digest(json.dumps({'content':text,'evidence':[]},ensure_ascii=False,sort_keys=True)),
                            'evidence':[],'citations':citations(text),'origin_url':browser_report['url'] if browser_report else '',
                            'provenance':'browser_deep_research' if browser_report else 'account_synthesis','original_files':[],
                            'browser_files':self.browser_files(run_id,stage_id) if browser_report else [],
                            'researched_at':now()[:10] if browser_report else None})
                if browser_report:stage['browser_progress']={'phase':'completed','message':'Web araştırması tamamlandı.','url':browser_report['url']}
                self.audit(run,stage)
                refresh_status(run);await self.store.save(run)
        except asyncio.CancelledError:
            # The bridge may still finish an in-flight request. Do not auto-retry.
            async with self.lock:
                run=await self.get(run_id);stage=self.stage(run,stage_id)
                resumable=stage['mode']=='browser' and self.browser is not None and self.browser.can_resume(run_id,stage_id)
                stage.update(status='interrupted',error=('Servis durdu. Tarayıcı işi kayıtlı; yeniden başlatıldığında aynı araştırma yeniden gönderilmeden kontrol edilir.' if resumable
                    else 'Servis durdu. İstek sağlayıcıda bitmiş olabilir; otomatik olarak tekrarlanmadı.'));refresh_status(run);await self.store.save(run)
            raise
        except Exception as exc:
            async with self.lock:
                run=await self.get(run_id);stage=self.stage(run,stage_id)
                stage.update(status=(exc.kind or 'failed') if isinstance(exc,(BrowserAttention,ServiceError)) else 'failed',error=str(exc) if isinstance(exc,(ServiceError,BrowserAttention)) else 'Aşama tamamlanamadı. Bağlantıyı kontrol edip devam edin.',finished_at=now())
                if isinstance(exc,ServiceError) and exc.policy:self.record_policy(stage,exc.policy,stage.get('input_sha256'))
                self.arm_retry(stage)
                refresh_status(run);await self.store.save(run)
        finally:self.tasks.pop((run_id,stage_id),None)
        await self.kick(run_id);self.schedule_sync(run_id)
    def browser_files(self,run_id,stage_id):
        """Job-log paths relative to the state root, so export and backup can find them."""
        if not self.browser:return []
        items=[]
        for path in self.browser.artifacts(run_id,stage_id):
            items.append({'name':path.name,'path':str(path.relative_to(self.store.root)),
                          'sha256':digest(path.read_bytes()),'bytes':path.stat().st_size})
        return items
    @staticmethod
    def arm_retry(stage):
        """Schedule the next automatic attempt, or stop and wait for the user.

        Only transient stalls are rearmed. An uncertain submission, a sign-in, a
        verification and a context limit are never repeated on a timer.
        """
        if stage['status'] not in AUTO_RETRY:
            stage['next_retry_at']=None;return
        index=stage.get('retry_index') or 0
        if index>=len(RETRY_BACKOFF):
            stage['next_retry_at']=None;return
        due=datetime.now(timezone.utc)+timedelta(seconds=RETRY_BACKOFF[index])
        stage['retry_index']=index+1
        stage['next_retry_at']=due.isoformat()

    @staticmethod
    def retry_due(stage,at=None):
        due=stage.get('next_retry_at')
        if not due or stage['status'] not in AUTO_RETRY:return False
        try:moment=datetime.fromisoformat(due)
        except (TypeError,ValueError):return False
        return moment<=(at or datetime.now(timezone.utc))

    async def sweep_retries(self):
        """Flip stages whose backoff has elapsed back to ready, then start them."""
        due=[]
        async with self.lock:
            for run in await self.store.all():
                if run['paused']:continue
                changed=False
                for stage in run['stages']:
                    if self.retry_due(stage):
                        stage.update(status='ready',error=None,next_retry_at=None);changed=True
                if changed:
                    refresh_status(run);await self.store.save(run);due.append(run['id'])
        for run_id in due:await self.kick(run_id)
        return due

    async def retry_loop(self,interval=20):
        while True:
            try:await asyncio.sleep(interval);await self.sweep_retries()
            except asyncio.CancelledError:raise
            except Exception:continue

    async def retry_stage(self,run_id,stage_id):
        """User-triggered retry of one stalled stage. Resets the backoff schedule."""
        async with self.lock:
            run=await self.get(run_id);stage=self.stage(run,stage_id)
            if stage['status']=='running':raise ServiceError('Bu aşama zaten çalışıyor.',409)
            if stage['status'] not in ATTENTION:raise ServiceError('Bu aşama tekrar denemeye uygun değil.',409)
            if stage['mode'] not in ('account','browser'):raise ServiceError('Elle içe aktarılan aşama tekrar denenmez.',409)
            stage.update(status='ready',error=None,retry_index=0,next_retry_at=None)
            run['paused']=False
            if stage['mode']=='account':run['auto_synthesize']=True
            refresh_status(run);await self.store.save(run)
        await self.kick(run_id)
        return await self.get(run_id)

    async def action(self,run_id,action):
        async with self.lock:
            run=await self.get(run_id)
            if action=='pause':run['paused']=True
            elif action=='automate':
                if run.get('execution_mode')=='browser':return run
                if any(s['report'] or s['attempts'] or s['status']=='running' for s in run['stages']):
                    raise ServiceError('Başlamış araştırmanın yürütme yöntemi değiştirilemez.',409)
                if not self.browser:raise ServiceError('Chrome araştırma bağlantısı yapılandırılmadı.',503)
                run.update(execution_mode='browser',paused=False,auto_synthesize=True,stages=initial_stages('browser'))
            elif action=='resume':
                run['paused']=False;run['auto_synthesize']=True
                for stage in run['stages']:
                    if stage['mode'] in ('account','browser') and stage['status'] in ATTENTION:
                        stage.update(status='ready',error=None,retry_index=0,next_retry_at=None)
            else:raise ServiceError('Bilinmeyen işlem.')
            refresh_status(run);await self.store.save(run)
        if action in ('resume','automate'):await self.kick(run_id)
        return await self.get(run_id)
    async def close(self):
        if self.retry_task:self.retry_task.cancel()
        tasks=list(self.tasks.values())+list(self.background)+([self.retry_task] if self.retry_task else [])
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        if self.browser:
            try:await self.browser.close()
            except Exception:pass
