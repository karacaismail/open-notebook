'use client'

import { AlertTriangle, Gauge, Files, Download } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useTranslation } from '@/lib/hooks/use-translation'
import { useContextPlan } from '@/modules/multi-model-research/hooks'
import { ContextPlanRow, ResearchPacket, ResearchRun } from '@/modules/multi-model-research/api'

export function SharedEvidencePacket({packet,onDownload}:{packet:ResearchPacket;onDownload:()=>void}) {
  const {t}=useTranslation()
  const shared=packet.evidence_packet
  if(!shared)return null
  return <div className="mb-5 rounded-xl border bg-muted/20 p-4">
    <p className="flex items-center gap-2 text-sm font-medium"><Files aria-hidden className="size-4 shrink-0"/>{t('research.sharedPacket')}</p>
    <p className="mt-2 text-xs leading-relaxed text-muted-foreground">{t('research.sharedPacketHelp')}</p>
    <div className="mt-3 flex flex-wrap items-center justify-between gap-2"><span className="text-xs text-muted-foreground">{t('research.sharedPacketBytes',{bytes:shared.bytes.toLocaleString()})}</span><Button size="sm" variant="outline" onClick={onDownload}><Download aria-hidden className="mr-2 size-3.5"/>{t('research.sharedPacketDownload')}</Button></div>
    <details className="mt-3 text-xs text-muted-foreground"><summary className="cursor-pointer">{t('research.sharedPacketHash')}</summary><code className="mt-2 block break-all">{shared.sha256}</code></details>
  </div>
}

export function PacketBudget({packet}:{packet:ResearchPacket}) {
  const {t}=useTranslation()
  if(packet.raw_tokens===undefined||!packet.token_margin)return null
  const number=(value:number|undefined)=>(value??0).toLocaleString()
  return <details className="rounded-lg border bg-muted/20 p-3 text-xs">
    <summary className="cursor-pointer font-medium">{t('research.budgetDetails')}</summary>
    <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 tabular-nums">
      <dt>{t('research.budgetRaw')}</dt><dd>{number(packet.raw_tokens)}</dd>
      <dt>{t('research.budgetTransport')}</dt><dd>{number(packet.transport_raw_tokens)}</dd>
      <dt>{t('research.budgetCounted')}</dt><dd>{number(packet.counted_tokens)} / {number(packet.automatic_input_limit)}</dd>
      <dt>{t('research.budgetMargin')}</dt><dd>×{packet.token_margin.multiplier} + {number(packet.token_margin.overhead_tokens)}</dd>
      <dt>{t('research.budgetEffective')}</dt><dd>{number(packet.effective_raw_limit)}</dd>
      <dt>{t('research.budgetRemaining')}</dt><dd>{number(packet.remaining_input_tokens)}</dd>
    </dl>
    <p className="mt-3 leading-relaxed text-muted-foreground">{t('research.budgetEstimateNote')}</p>
  </details>
}

export function ContextBudgetRows({rows,onSelect}:{rows:ContextPlanRow[];onSelect:(stage:string)=>void}) {
  const {t}=useTranslation()
  if(!rows.length)return null
  const warning=rows.some(row=>row.warning)
  return <section aria-label={t('research.budgetTitle')} className="rounded-xl border bg-card p-4">
    <div className="flex items-start gap-3">
      {warning?<AlertTriangle className="mt-0.5 size-5 shrink-0 text-warn"/>:<Gauge className="mt-0.5 size-5 shrink-0 text-muted-foreground"/>}
      <div className="min-w-0 flex-1"><h2 className="text-sm font-semibold">{t(warning?'research.budgetWarning':'research.budgetTitle')}</h2>
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{t('research.budgetPlanHelp')}</p>
        <div className="mt-3 grid gap-2 sm:grid-cols-3">{rows.map(row=><button type="button" key={row.stage_id} onClick={()=>onSelect(row.stage_id)} className="min-w-0 rounded-lg border p-3 text-left transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
          <span className="block text-xs font-medium">{row.provider} · {t('research.budgetRound',{round:row.round})}</span>
          {row.error?<span className="mt-1 block break-words text-xs text-destructive">{row.error}</span>:<>
            <span className="mt-2 block text-sm font-semibold tabular-nums">{row.counted_tokens?.toLocaleString()} / {row.automatic_input_limit?.toLocaleString()}</span>
            <span className="mt-1 block text-xs text-muted-foreground">{t(row.projection?'research.budgetProjected':'research.budgetMeasured')}</span>
            {row.projection&&<span className="mt-1 block text-xs text-muted-foreground">{t('research.budgetReserve',{reports:row.missing_reports,tokens:row.reserved_report_tokens?.toLocaleString()})}</span>}
            {row.warning&&<span className="mt-2 block text-xs font-medium text-warn">{t(row.fits?'research.budgetNearLimit':'research.budgetOverLimit')}</span>}
          </>}
        </button>)}</div>
      </div>
    </div>
  </section>
}

export function ResearchContextBudget({run,onSelect}:{run:ResearchRun;onSelect:(stage:string)=>void}) {
  const {t}=useTranslation()
  const enabled=run.status!=='completed'&&run.stages.filter(s=>s.round<=2).every(s=>s.status==='completed')
  const plan=useContextPlan(run.id,enabled)
  if(!enabled)return null
  if(plan.isError)return <p role="status" className="rounded-lg border p-3 text-sm text-muted-foreground">{t('research.budgetUnavailable')}</p>
  return <ContextBudgetRows rows={plan.data?.stages??[]} onSelect={onSelect}/>
}
