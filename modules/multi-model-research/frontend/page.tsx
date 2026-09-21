'use client'

import { useEffect, useRef, useState } from 'react'
import { ArrowUpRight, Copy, Download, Loader2, Plus, RefreshCw, Telescope, Upload } from 'lucide-react'
import { toast } from 'sonner'
import { ResearchShell } from '@/modules/multi-model-research/components/ResearchShell'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import { MarkdownRenderer } from '@/components/ui/markdown-renderer'
import { useTranslation } from '@/lib/hooks/use-translation'
import { useBrowserLogin, useBrowserStatus, useResearchActions, useResearchPacket, useResearchRun, useResearchRuns } from '@/modules/multi-model-research/hooks'
import { downloadResearchFile, ExecutionMode, ResearchRun, ResearchStage } from '@/modules/multi-model-research/api'

import { ResearchQuestionCard, briefPreview } from '@/modules/multi-model-research/components/ResearchQuestionCard'
import { ResearchWorkflow } from '@/modules/multi-model-research/components/ResearchWorkflow'
import { ResearchControls } from '@/modules/multi-model-research/components/ResearchControls'
import { ResearchRetryPanel } from '@/modules/multi-model-research/components/ResearchRetryPanel'
import { attentionStates, roundKeys, ResearchStatus as Status } from '@/modules/multi-model-research/components/research-state'

import { ResearchStopFeedback, ResearchAttentionSummary } from './components/ResearchStopFeedback'

const providerUrls:Record<string,string>={Gemini:'https://gemini.google.com/app',ChatGPT:'https://chatgpt.com/',Claude:'https://claude.ai/new'}

const provenanceKeys:Record<string,string>={browser_deep_research:'research.browser_deep_research',web_deep_research_import:'research.web_deep_research_import',account_synthesis:'research.account_synthesis',manual_synthesis_import:'research.manual_synthesis_import'}

function NewResearch({onCreated}:{onCreated:(id:string)=>void}) {
  const {t}=useTranslation();const {create}=useResearchActions()
  const [question,setQuestion]=useState('');const [scope,setScope]=useState('');const [language,setLanguage]=useState('Türkçe');const [auto,setAuto]=useState(true)
  const [asOf,setAsOf]=useState(()=>new Date().toLocaleDateString('sv-SE'));const [mode,setMode]=useState<ExecutionMode>('browser')
  const request=useRef<{signature:string;key:string}|null>(null)
  return <form className="space-y-5 rounded-xl border bg-card p-6" onSubmit={async event=>{event.preventDefault();try{const body={question,scope,language,as_of:asOf,auto_synthesize:auto,execution_mode:mode};const signature=JSON.stringify(body);if(request.current?.signature!==signature)request.current={signature,key:crypto.randomUUID()};const run=await create.mutateAsync({body,key:request.current.key});onCreated(run.id)}catch { /* Mutation errors are displayed by the hook. */ }}}>
    <fieldset className="space-y-3"><legend className="mb-2 text-sm font-medium">{t('research.flow')}</legend>
      {([['browser',t('research.automaticMode'),t('research.automaticModeHelp')],
         ['imports',t('research.importMode'),t('research.importModeHelp')]] as const).map(([value,label,help])=>
        <label key={value} className={cn('flex cursor-pointer items-start gap-3 rounded-lg border p-3 text-sm transition-colors',mode===value?'border-primary bg-primary/5':'border-border hover:bg-accent')}>
          <input type="radio" name="execution-mode" value={value} checked={mode===value} onChange={()=>setMode(value)} className="mt-1 accent-blue-600"/>
          <span><span className="font-medium">{label}</span><span className="mt-1 block text-xs leading-relaxed text-muted-foreground">{help}</span></span>
        </label>)}
    </fieldset>
    <div className="space-y-2"><Label htmlFor="research-question">{t('research.question')}</Label><Textarea id="research-question" value={question} onChange={e=>setQuestion(e.target.value)} placeholder={t('research.questionPlaceholder')} required minLength={5} maxLength={12000} className="min-h-28 text-base"/></div>
    <div className="space-y-2"><Label htmlFor="research-scope">{t('research.scope')}</Label><Textarea id="research-scope" value={scope} onChange={e=>setScope(e.target.value)} placeholder={t('research.scopePlaceholder')} maxLength={30000}/></div>
    <div className="max-w-xs space-y-2"><Label htmlFor="research-language">{t('research.language')}</Label><Input id="research-language" value={language} onChange={e=>setLanguage(e.target.value)} required minLength={2} maxLength={60}/></div>
    <div className="max-w-xs space-y-2"><Label htmlFor="research-date">{t('research.asOf')}</Label><Input id="research-date" type="date" value={asOf} onChange={e=>setAsOf(e.target.value)} required/></div>
    <label className="flex items-start gap-3 text-sm"><input type="checkbox" checked={auto} onChange={e=>setAuto(e.target.checked)} className="mt-1 accent-blue-600"/>{t('research.auto')}</label>
    <Button type="submit" disabled={create.isPending||question.trim().length<5}>{create.isPending?<Loader2 className="mr-2 size-4 animate-spin"/>:<Plus className="mr-2 size-4"/>}{t('research.start')}</Button>
    {mode==='browser'&&<p className="text-xs leading-relaxed text-muted-foreground">{t('research.notGuaranteed')}</p>}
  </form>
}

function BrowserConnections() {
  const {t}=useTranslation();const [deep,setDeep]=useState(false);const [enabled,setEnabled]=useState(false)
  const status=useBrowserStatus(enabled,deep);const login=useBrowserLogin()
  return <section className="rounded-xl border bg-card p-5" aria-label={t('research.connections')}>
    <div className="mb-3 flex flex-wrap items-center justify-between gap-3"><h3 className="font-medium">{t('research.connections')}</h3>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="outline" disabled={status.isFetching} onClick={()=>{setEnabled(true);status.refetch()}}>{status.isFetching?<Loader2 className="mr-2 size-4 animate-spin"/>:<RefreshCw className="mr-2 size-4"/>}{t('research.checkConnections')}</Button>
        <Button size="sm" variant="outline" disabled={login.isPending} onClick={()=>login.mutate()}>{login.isPending?<Loader2 className="mr-2 size-4 animate-spin"/>:<ArrowUpRight className="mr-2 size-4"/>}{t('research.openLogin')}</Button>
      </div>
    </div>
    <label className="mb-3 flex items-center gap-2 text-xs text-muted-foreground"><input type="checkbox" checked={deep} onChange={e=>setDeep(e.target.checked)} className="accent-blue-600"/>{t('research.deepCheck')}</label>
    {status.data&&<ul className="space-y-2">{status.data.connections.map(item=><li key={item.provider} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border p-3 text-sm"><span className="font-medium">{item.provider}</span><Status value={item.status}/><span className="w-full text-xs text-muted-foreground">{item.message}</span></li>)}</ul>}
    {status.isError&&<p role="alert" className="text-sm text-destructive">{t('research.error')}</p>}
    <p className="mt-3 text-xs leading-relaxed text-muted-foreground">{t('research.connectionHelp')}</p>
  </section>
}

function StageDetail({run,stage}:{run:ResearchRun;stage:ResearchStage}) {
  const {t}=useTranslation();const {upload,retry,error}=useResearchActions()
  const packet=useResearchPacket(run.id,stage.id,stage.status!=='pending')
  const [text,setText]=useState('');const [file,setFile]=useState<File|null>(null);const [evidence,setEvidence]=useState<File[]>([]);const [url,setUrl]=useState('');const [manual,setManual]=useState(false)
  const [researchedAt,setResearchedAt]=useState('')
  const unlocked=stage.status!=='pending';const canImport=unlocked&&!['running','completed'].includes(stage.status)
  async function submit(event:React.FormEvent) {
    event.preventDefault();if(!packet.data)return
    const data=new FormData();data.append('packet_sha',packet.data.sha256);data.append('origin_url',url);if(researchedAt)data.append('researched_at',researchedAt)
    if(file)data.append('file',file);else data.append('text',text)
    evidence.forEach(f=>data.append('evidence_files',f))
    try{await upload.mutateAsync({id:run.id,stage:stage.id,data})}catch { /* Mutation errors are displayed by the hook. */ }
  }
  return <section className="min-w-0 rounded-2xl border bg-card p-5 shadow-sm sm:p-6" aria-label={`${stage.provider} ${t(roundKeys[stage.round])}`}>
    <div className="mb-5 flex flex-wrap items-center justify-between gap-3"><div><p className="mb-1 text-xs uppercase tracking-wide text-muted-foreground">{t(roundKeys[stage.round])}</p><h2 className="text-xl font-semibold">{stage.provider}</h2></div><Status value={stage.status}/></div>
    {!unlocked?<p className="py-8 text-muted-foreground">{t('research.blocked')}</p>:<>
      <ResearchStopFeedback stage={stage}/>
      <ResearchRetryPanel run={run} stage={stage} pending={retry.isPending} onRetry={()=>retry.mutate({id:run.id,stage:stage.id})}/>
      {stage.report?<div className="space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground"><span>{t(provenanceKeys[stage.report.provenance]||'research.manual_synthesis_import')}</span><Button size="sm" variant="outline" onClick={()=>downloadResearchFile(stage.report!.content,stage.id+'.md')}><Download className="mr-2 size-4"/>{t('research.report')}</Button></div>
        <div className="max-h-[65vh] overflow-y-auto pr-2"><MarkdownRenderer components={{img:({alt})=><span>{alt}</span>,a:({href,children})=><a href={href} target="_blank" rel="noopener noreferrer">{children}</a>}}>{stage.report.content}</MarkdownRenderer></div>
        <details className="rounded-lg border p-3"><summary className="cursor-pointer text-sm font-medium">{t('research.sources')} ({stage.report.citations.length})</summary><ul className="mt-3 space-y-2 break-all text-xs">{stage.report.citations.map(source=><li key={source}><a href={source} target="_blank" rel="noopener noreferrer" className="text-blue-500 hover:underline">{source}</a></li>)}{!stage.report.citations.length&&<li>{t('research.noSources')}</li>}</ul></details>
        {stage.report.evidence.map((item,i)=><details key={i} className="rounded-lg border p-3"><summary className="cursor-pointer text-sm">{item.name}</summary><pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap text-xs">{item.content}</pre></details>)}
        <div className="space-y-1 text-xs text-muted-foreground"><p>{t('research.reportHash')}: <code className="break-all">{stage.report.sha256}</code></p>{stage.report.original_files.length>0&&<p>{t('research.savedFiles')}: {stage.report.original_files.map(f=>f.name).join(', ')}</p>}{stage.usage&&<p>{t('research.usage')}: {stage.usage.total_tokens??((stage.usage.prompt_tokens||0)+(stage.usage.completion_tokens||0))}</p>}</div>
        <p className="text-xs text-muted-foreground">{t('research.researchedAt')}: {stage.report.researched_at||t('research.unknownDate')}</p>
        <p className="text-xs text-muted-foreground">{t('research.immutable')}</p>
      </div>:<div className="space-y-5">
        {stage.mode==='browser'&&stage.browser_progress&&<div className="rounded-lg border bg-muted/40 p-3 text-sm"><p className="flex items-center gap-2">{stage.status==='running'&&<Loader2 className="size-4 animate-spin"/>}{stage.browser_progress.message}</p>{stage.browser_progress.url&&<a href={stage.browser_progress.url} target="_blank" rel="noopener noreferrer" className="mt-2 inline-flex items-center text-xs text-blue-500 hover:underline">{t('research.openConversation')}<ArrowUpRight className="ml-1 size-3"/></a>}</div>}
        {stage.mode==='browser'&&attentionStates.includes(stage.status)&&<p role="status" className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-sm">{t('research.attentionNeeded')}</p>}
        <div className="space-y-3"><h3 className="text-sm font-semibold">{stage.mode==='import'?t('research.step1'):stage.mode==='browser'?t('research.browserBusy'):t('research.accountMode')}</h3><p className="text-sm leading-relaxed text-muted-foreground">{stage.mode==='import'?t('research.step1Help'):stage.mode==='browser'?t('research.browserStageHelp'):t('research.automatic')}</p>
          <div className="flex flex-wrap gap-2"><Button asChild variant="outline" size="sm"><a href={providerUrls[stage.provider]} target="_blank" rel="noopener noreferrer">{t('research.openProvider',{provider:stage.provider})}<ArrowUpRight className="ml-2 size-4"/></a></Button>
            <Button variant="outline" size="sm" disabled={!packet.data} onClick={async()=>{try{await navigator.clipboard.writeText(packet.data!.prompt);toast.success(t('research.copied'))}catch(err){error(err)}}}><Copy className="mr-2 size-4"/>{t('research.copyPrompt')}</Button>
            <Button variant="outline" size="sm" disabled={!packet.data} onClick={()=>downloadResearchFile(packet.data!.prompt,stage.id+'-input.md')}><Download className="mr-2 size-4"/>{t('research.downloadPacket')}</Button>
          </div>
          {packet.data&&<p className="text-xs text-muted-foreground">{t('research.packetInfo',{reports:packet.data.report_count,tokens:packet.data.estimated_tokens.toLocaleString()})}</p>}
          {packet.isError&&<p role="alert" className="text-sm text-destructive">{t('research.error')} <button className="underline" onClick={()=>packet.refetch()}>{t('common.retryConnection')}</button></p>}
          {packet.data&&<details><summary className="cursor-pointer text-xs text-muted-foreground">{t('research.prompt')}</summary><pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap rounded-lg bg-muted p-3 text-xs">{packet.data.prompt}</pre></details>}
        </div>
        {canImport&&stage.mode!=='import'&&!manual&&<Button variant="outline" onClick={()=>setManual(true)}><Upload className="mr-2 size-4"/>{t('research.manualFallback')}</Button>}
        {canImport&&(stage.mode==='import'||manual)&&<form className="space-y-4 border-t pt-5" onSubmit={submit}>
          <div><h3 className="text-sm font-semibold">{t('research.step2')}</h3><p className="mt-1 text-xs leading-relaxed text-muted-foreground">{t('research.importHelp')}</p></div>
          <div className="space-y-2"><Label htmlFor="report-text">{t('research.paste')}</Label><Textarea id="report-text" value={text} onChange={e=>setText(e.target.value)} disabled={!!file} rows={7} className="font-mono text-xs"/></div>
          <div className="space-y-2"><Label htmlFor="report-file">{t('research.upload')}</Label><Input id="report-file" type="file" accept=".md,.markdown,.txt,.pdf,.docx" disabled={!!text.trim()} onChange={e=>setFile(e.target.files?.[0]||null)} className="h-auto py-2"/></div>
          <div className="space-y-2"><Label htmlFor="report-evidence">{t('research.evidence')}</Label><Input id="report-evidence" type="file" multiple accept=".md,.markdown,.txt,.pdf,.docx" onChange={e=>setEvidence(Array.from(e.target.files||[]))} className="h-auto py-2"/><p className="text-xs text-muted-foreground">{t('research.formats')}</p></div>
          <div className="space-y-2"><Label htmlFor="report-url">{t('research.reportUrl')}</Label><Input id="report-url" type="url" value={url} onChange={e=>setUrl(e.target.value)}/></div>
          <div className="max-w-xs space-y-2"><Label htmlFor="report-date">{t('research.researchedAt')}</Label><Input id="report-date" type="date" value={researchedAt} onChange={e=>setResearchedAt(e.target.value)}/></div>
          <Button type="submit" disabled={upload.isPending||!packet.data||(!file&&text.trim().length<40)}>{upload.isPending?<Loader2 className="mr-2 size-4 animate-spin"/>:<Upload className="mr-2 size-4"/>}{t('research.submit')}</Button>
        </form>}
      </div>}
    </>}
  </section>
}

function Workspace({run}:{run:ResearchRun}) {
  const {t}=useTranslation()
  const [selected,setSelected]=useState(()=>run.stages.find(s=>s.status==='running')?.id||run.stages.find(s=>attentionStates.includes(s.status))?.id||(run.status==='completed'?'final_chatgpt':'research_gemini'))
  const stage=run.stages.find(s=>s.id===selected)||run.stages[0]
  return <div className="space-y-5">
    <ResearchQuestionCard run={run}/>
    <ResearchControls run={run}/>
    <ResearchAttentionSummary run={run} onSelect={setSelected}/>
    <div className="grid items-start gap-5 lg:grid-cols-[minmax(300px,0.7fr)_minmax(0,1.3fr)] xl:grid-cols-[380px_minmax(0,1fr)]">
      <div className="order-2 min-w-0 lg:order-1"><ResearchWorkflow run={run} selected={stage.id} onSelect={setSelected}/></div>
      <div id="research-stage-detail" className="order-1 min-w-0 scroll-mt-24 lg:order-2"><StageDetail key={run.id+stage.id} run={run} stage={stage}/></div>
    </div>
    <p className="text-xs leading-relaxed text-muted-foreground">{t('research.freshness')} {t('research.quota')}</p>
  </div>
}

export default function ResearchPage() {
  const {t}=useTranslation();const runs=useResearchRuns();const [id,setId]=useState<string|null>(null);const run=useResearchRun(id)
  useEffect(()=>{const update=()=>setId(new URLSearchParams(window.location.search).get('run'));update();window.addEventListener('popstate',update);return()=>window.removeEventListener('popstate',update)},[])
  function select(value:string|null){setId(value);window.history.pushState({},'',value?'/research?run='+value:'/research')}
  return <ResearchShell><div className="flex-1 overflow-y-auto"><div className="mx-auto max-w-[1550px] space-y-6 px-5 py-7 md:px-8">
    <header className="flex flex-wrap items-center justify-between gap-4"><div><h1 className="flex items-center gap-3 text-2xl font-semibold tracking-tight"><Telescope className="size-6 text-blue-500"/>{t('research.title')}</h1><p className="mt-2 text-sm text-muted-foreground">{t('research.subtitle')}</p></div>{id&&<Button variant="outline" onClick={()=>select(null)}><Plus className="mr-2 size-4"/>{t('research.create')}</Button>}</header>
    {!id&&<BrowserConnections/>}
    {(runs.data?.length||0)>0&&<div className="flex items-center gap-3"><Label htmlFor="research-history" className="shrink-0 text-xs text-muted-foreground">{t('research.history')}</Label><select id="research-history" value={id||''} onChange={e=>select(e.target.value||null)} className="min-w-0 flex-1 rounded-lg border bg-background px-3 py-2 text-sm"><option value="">{t('research.create')}</option>{runs.data!.map(item=><option key={item.id} value={item.id}>{briefPreview(item.question).slice(0,100)} · {item.completed}/8</option>)}</select></div>}
    {runs.isError&&<p role="alert" className="text-destructive">{t('research.error')} <Button variant="outline" size="sm" onClick={()=>runs.refetch()}>{t('common.retryConnection')}</Button></p>}
    {!id?<div className="max-w-4xl"><NewResearch key="new" onCreated={select}/><p className="mt-4 text-xs text-muted-foreground">3 → 2 → 2 → 1 · {t('research.allReports')}</p></div>:run.data?<Workspace key={id} run={run.data}/>:run.isError?<p role="alert">{t('research.error')}</p>:<div className="flex items-center gap-2 py-12 text-muted-foreground"><Loader2 className="size-5 animate-spin"/>{t('common.loading')}</div>}
  </div></div></ResearchShell>
}
