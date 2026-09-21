'use client'

import { Check, CircleAlert, Loader2 } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { useTranslation } from '@/lib/hooks/use-translation'
import type { ResearchRun, ResearchStage } from '@/modules/multi-model-research/api'
import { cn } from '@/lib/utils'

export const attentionStates = ['integrity_error', 'calibration_required', 'failed', 'interrupted', 'context_limit', 'login_required', 'verification_required', 'quota_wait', 'research_unavailable', 'browser_changed', 'browser_unavailable', 'submission_uncertain']
export const roundKeys: Record<number, string> = { 1: 'research.round1', 2: 'research.round2', 3: 'research.round3', 4: 'research.round4' }
export const statusKeys: Record<string, string> = {
  integrity_error: 'research.integrity_error', calibration_required: 'research.calibration_required', waiting_input: 'research.waiting_input', pending: 'research.pending', ready: 'research.ready', running: 'research.running', completed: 'research.completed', failed: 'research.failed', interrupted: 'research.interrupted', context_limit: 'research.context_limit', paused: 'research.paused', needs_attention: 'research.needs_attention', connected: 'research.connected', login_required: 'research.login_required', verification_required: 'research.verification_required', quota_wait: 'research.quota_wait', research_unavailable: 'research.research_unavailable', browser_changed: 'research.browser_changed', browser_unavailable: 'research.browser_unavailable', submission_uncertain: 'research.submission_uncertain',
}

export function hasRunningStage(run: ResearchRun) {
  return run.stages.some(stage => stage.status === 'running')
}

export function canRetryStage(run: ResearchRun, stage: ResearchStage) {
  return stage.mode !== 'import' && attentionStates.includes(stage.status) && !hasRunningStage(run)
}

export function ResearchStatus({ value }: { value: string }) {
  const { t } = useTranslation()
  return <Badge variant="outline" className={cn('gap-1.5 whitespace-normal text-xs font-medium',
    value === 'completed' && 'border-emerald-500/25 bg-emerald-500/5 text-emerald-700 dark:text-emerald-400',
    value === 'running' && 'border-blue-500/30 bg-blue-500/10 text-blue-700 dark:text-blue-300',
    attentionStates.includes(value) && 'border-amber-500/30 bg-amber-500/5 text-amber-800 dark:text-amber-300')}>
    {value === 'running' && <Loader2 aria-hidden className="size-3 motion-safe:animate-spin" />}
    {value === 'completed' && <Check aria-hidden className="size-3" />}
    {attentionStates.includes(value) && <CircleAlert aria-hidden className="size-3" />}
    {t(statusKeys[value] || 'research.needs_attention')}
  </Badge>
}
