'use client'

import { useState } from 'react'
import Link from 'next/link'
import { ArrowUpRight, Download, Loader2, NotebookPen, Pause, Play } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useTranslation } from '@/lib/hooks/use-translation'
import { useResearchActions } from '@/modules/multi-model-research/hooks'
import { downloadResearchFile, researchApi, type ResearchRun } from '@/modules/multi-model-research/api'
import { hasRunningStage, ResearchStatus } from './research-state'

export function ResearchControls({ run }: { run: ResearchRun }) {
  const { t } = useTranslation()
  const { action, error } = useResearchActions()
  const [exporting, setExporting] = useState(false)
  const busy = hasRunningStage(run)
  const finished = run.status === 'completed'
  const active = run.stages.filter(stage => stage.status === 'running').map(stage => stage.provider).join(' + ')
  return <div className="z-20 rounded-2xl border border-border bg-background/95 p-4 shadow-sm backdrop-blur-md sm:p-5 md:sticky md:top-0">
    <div className="flex flex-wrap items-center justify-between gap-4">
      <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h2 className="text-sm font-semibold">{t('research.runControls')}</h2><ResearchStatus value={run.status} /></div><p className="mt-1.5 text-xs text-muted-foreground">{run.paused ? t('research.pauseHelp') : busy ? t('research.activeAgents', { agents: active }) : finished ? t('research.researchReady') : t('research.chooseNextAction')}</p></div>
      <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
        {run.execution_mode !== 'browser' && run.stages.every(stage => !stage.report && !stage.attempts) && <Button className="min-h-11" disabled={action.isPending} onClick={() => action.mutate({ id: run.id, action: 'automate' })}><Play aria-hidden className="me-2 size-4" />{t('research.automate')}</Button>}
        {!finished && !busy && <Button className="min-h-11 shadow-sm" disabled={action.isPending} title={t('research.resumeHelp')} onClick={() => action.mutate({ id: run.id, action: 'resume' })}>{action.isPending ? <Loader2 aria-hidden className="me-2 size-4 motion-safe:animate-spin" /> : <Play aria-hidden className="me-2 size-4" />}{t('research.resume')}</Button>}
        {!finished && !run.paused && <Button variant="outline" className="min-h-11 border-amber-600/40 bg-amber-500/5 text-amber-900 hover:bg-amber-500/10 dark:text-amber-200" disabled={action.isPending} title={t('research.pauseHelp')} onClick={() => action.mutate({ id: run.id, action: 'pause' })}><Pause aria-hidden className="me-2 size-4" />{t('research.pause')}</Button>}
        <Button variant={finished ? 'default' : 'outline'} className="min-h-11" disabled={exporting} onClick={async () => { setExporting(true); try { downloadResearchFile(await researchApi.export(run.id), 'research-' + run.id.slice(0, 8) + '.zip') } catch (err) { error(err) } finally { setExporting(false) } }}>{exporting ? <Loader2 aria-hidden className="me-2 size-4 motion-safe:animate-spin" /> : <Download aria-hidden className="me-2 size-4" />}{t('research.export')}</Button>
        {run.notebook_id && <Button variant="outline" className="min-h-11" asChild><Link href={`/notebooks/${run.notebook_id}`}><NotebookPen aria-hidden className="me-2 size-4" />{t('research.notebook')}<ArrowUpRight aria-hidden className="ms-2 size-3.5" /></Link></Button>}
      </div>
    </div>
    {run.sync_error && <div role="alert" className="mt-4 flex flex-wrap items-center gap-2 rounded-lg border border-amber-500/40 p-3 text-sm">{run.sync_error}<Button variant="outline" size="sm" disabled={action.isPending} onClick={() => action.mutate({ id: run.id, action: 'sync' })}>{t('research.sync')}</Button></div>}
  </div>
}
