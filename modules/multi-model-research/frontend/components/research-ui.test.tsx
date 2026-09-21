import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ResearchRun, ResearchStage } from '@/modules/multi-model-research/api'
import { researchEn } from '@/modules/multi-model-research/locales'
import { ResearchRetryPanel, retryTiming } from './ResearchRetryPanel'
import { ResearchQuestionCard } from './ResearchQuestionCard'
import { ResearchWorkflow } from './ResearchWorkflow'
import { ResearchAttentionSummary, ResearchStopFeedback } from './ResearchStopFeedback'

vi.mock('@/lib/hooks/use-translation', () => ({ useTranslation: () => ({ language: 'en-US', t: (key: string, values: Record<string, unknown> = {}) => {
  const value = key === 'common.close' ? 'Close' : researchEn[key.replace('research.', '') as keyof typeof researchEn] || key
  return Object.entries(values).reduce((text, [name, replacement]) => text.replaceAll('{{' + name + '}}', String(replacement)), value)
} }) }))

const now = Date.parse('2026-09-21T12:00:00Z')
function makeStage(overrides: Partial<ResearchStage> = {}): ResearchStage {
  return { id: 'synthesis_chatgpt', provider: 'ChatGPT', round: 3, mode: 'account', status: 'failed', attempts: 2, report: null, error: null, usage: null, note_id: null, browser_progress: null, retry_index: 2, next_retry_at: new Date(now + 300_000).toISOString(), ...overrides }
}
function makeRun(stage: ResearchStage, overrides: Partial<ResearchRun> = {}): ResearchRun {
  return { id: 'test-run', question: '# A long research question\n\n**Important evidence**\n\n' + 'Context to preserve. '.repeat(200) + '\n\nThe final sentence is retained.', scope: '## Scope\n\n- Primary sources\n- Dates', as_of: '2026-09-21', language: 'English', auto_synthesize: true, execution_mode: 'browser', paused: false, status: 'needs_attention', stages: [stage], created_at: new Date(now).toISOString(), updated_at: new Date(now).toISOString(), notebook_id: null, sync_error: null, ...overrides }
}

afterEach(() => { cleanup(); vi.useRealTimers() })

describe('Recorded stop reasons', () => {
  it('shows the exact provider failure instead of inventing a root cause', () => {
    render(<ResearchStopFeedback stage={makeStage({ error: 'CLI exited with status 7: provider unavailable' })} />)
    expect(screen.getByText('CLI exited with status 7: provider unavailable')).toBeInTheDocument()
    expect(screen.getByText('Completed reports and sources remain saved.')).toBeInTheDocument()
    expect(screen.queryByText(/timeout/i)).not.toBeInTheDocument()
  })
  it('states that a missing reason is unknown', () => {
    render(<ResearchStopFeedback stage={makeStage({ error: '  ' })} />)
    expect(screen.getByText(/did not record a detailed reason/)).toBeInTheDocument()
  })
  it('surfaces all failed providers without changing the selected report', () => {
    const one = makeStage({ error: 'Account timed out' }), two = makeStage({ id: 'synthesis_claude', provider: 'Claude', status: 'submission_uncertain', error: 'Submission not confirmed', next_retry_at: null })
    const select = vi.fn()
    render(<ResearchAttentionSummary run={makeRun(one, { stages: [one, two] })} onSelect={select} />)
    expect(screen.getByText('Account timed out')).toBeInTheDocument()
    expect(screen.getByText('Submission not confirmed')).toBeInTheDocument()
    expect(screen.getByText(/No automatic retry is scheduled/)).toBeInTheDocument()
    fireEvent.click(screen.getAllByRole('button', { name: 'View stage' })[1])
    expect(select).toHaveBeenCalledWith('synthesis_claude')
  })
  it('distinguishes a user pause from provider failure and clears recovered errors', () => {
    const stage = makeStage({ status: 'running', error: 'Old error' })
    render(<><ResearchStopFeedback stage={stage} /><ResearchAttentionSummary run={makeRun(stage, { paused: true })} onSelect={vi.fn()} /></>)
    expect(screen.queryByText('Old error')).not.toBeInTheDocument()
    expect(screen.getByText(/Following steps were paused/)).toBeInTheDocument()
  })
})

describe('Server-backed retry display', () => {
  beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(now) })

  it.each([1, 5, 30, 90, 250])('shows the correct %i-minute cycle', minutes => {
    const cycle = [1, 5, 30, 90, 250].indexOf(minutes) + 1
    const stage = makeStage({ retry_index: cycle, next_retry_at: new Date(now + minutes * 60_000).toISOString() })
    render(<ResearchRetryPanel run={makeRun(stage)} stage={stage} onRetry={vi.fn()} />)
    expect(screen.getByText(`Retry cycle ${cycle}/5 · ${minutes}-minute interval`)).toBeInTheDocument()
    expect(screen.getByText(/Next: attempt 3/)).toBeInTheDocument()
    expect(screen.getByRole('list').querySelector('[aria-current="step"]')).toHaveTextContent(`${minutes} min`)
  })

  it('ticks locally but never triggers a model call when the deadline passes', () => {
    const stage = makeStage({ next_retry_at: new Date(now + 2000).toISOString() }), onRetry = vi.fn()
    render(<ResearchRetryPanel run={makeRun(stage)} stage={stage} onRetry={onRetry} />)
    expect(screen.getByRole('timer')).toHaveTextContent('00:02')
    act(() => { vi.advanceTimersByTime(1000) })
    expect(screen.getByRole('timer')).toHaveTextContent('00:01')
    act(() => { vi.advanceTimersByTime(4000) })
    expect(screen.getByRole('timer')).toHaveTextContent('00:00')
    expect(screen.getByText('Waiting for the scheduler')).toBeInTheDocument()
    expect(onRetry).not.toHaveBeenCalled()
  })

  it('allows an immediate manual retry and disables a pending request', () => {
    const stage = makeStage(), run = makeRun(stage), onRetry = vi.fn()
    const view = render(<ResearchRetryPanel run={run} stage={stage} onRetry={onRetry} />)
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
    view.rerender(<ResearchRetryPanel run={run} stage={stage} pending onRetry={onRetry} />)
    expect(screen.getByRole('button', { name: 'Try again' })).toBeDisabled()
  })

  it('hides manual retry even when the run is paused and a sibling is running', () => {
    const stage = makeStage(), sibling = makeStage({ id: 'synthesis_claude', provider: 'Claude', status: 'running' })
    render(<ResearchRetryPanel run={makeRun(stage, { paused: true, status: 'paused', stages: [stage, sibling] })} stage={stage} onRetry={vi.fn()} />)
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()
    expect(screen.getByRole('timer')).toHaveTextContent('—:—')
    expect(screen.getByText('Manual retry is available when the active stages finish.')).toBeInTheDocument()
  })

  it('shows the current attempt without a fictional timer while running', () => {
    const stage = makeStage({ status: 'running', next_retry_at: null, retry_index: 0 })
    render(<ResearchRetryPanel run={makeRun(stage, { status: 'running' })} stage={stage} onRetry={vi.fn()} />)
    expect(screen.getByText('Attempt 2 in progress')).toBeInTheDocument()
    expect(screen.queryByRole('timer')).not.toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('distinguishes an unscheduled login problem from an exhausted schedule', () => {
    const stage = makeStage({ status: 'login_required', retry_index: 0, next_retry_at: null })
    const view = render(<ResearchRetryPanel run={makeRun(stage)} stage={stage} onRetry={vi.fn()} />)
    expect(screen.getByText('Review needed before continuing')).toBeInTheDocument()
    expect(screen.queryByText('Automatic retry limit reached')).not.toBeInTheDocument()
    const exhausted = { ...stage, status: 'failed', retry_index: 5 }
    view.rerender(<ResearchRetryPanel run={makeRun(exhausted)} stage={exhausted} onRetry={vi.fn()} />)
    expect(screen.getByText('Automatic retry limit reached')).toBeInTheDocument()
  })

  it('handles legacy missing fields and malformed timestamps without NaN', () => {
    const stage = makeStage({ retry_index: undefined as unknown as number, next_retry_at: 'invalid' })
    expect(retryTiming(stage, now)).toMatchObject({ index: 0, scheduled: false, seconds: 0, progress: 0, exhausted: false })
  })
})

describe('Brief and workflow', () => {
  it('keeps the page compact and shows the entire formatted brief and scope in the modal', async () => {
    render(<ResearchQuestionCard run={makeRun(makeStage())} />)
    expect(screen.queryByText('The final sentence is retained.')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Open the full research brief' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('heading', { name: 'A long research question' })).toBeInTheDocument()
    expect(within(dialog).getByText('Important evidence').tagName).toBe('STRONG')
    expect(within(dialog).getByText('The final sentence is retained.')).toBeInTheDocument()
    expect(within(dialog).getByText('Primary sources')).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('selects the exact stage and labels progress as completed reports', () => {
    const stage = makeStage({ status: 'running' }), onSelect = vi.fn()
    render(<ResearchWorkflow run={makeRun(stage)} selected={stage.id} onSelect={onSelect} />)
    const button = screen.getByRole('button', { name: 'ChatGPT · Independent syntheses · Running' })
    expect(button).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(button)
    expect(onSelect).toHaveBeenCalledWith('synthesis_chatgpt')
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '0')
  })
})
