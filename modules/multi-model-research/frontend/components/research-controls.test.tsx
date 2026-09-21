import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ResearchRun } from '../api'
import { researchEn } from '../locales'
import { ResearchControls } from './ResearchControls'
import { ResearchActionDialog } from './ResearchActionDialog'

const mutation = vi.hoisted(() => ({ mutate: vi.fn(), mutateAsync: vi.fn().mockResolvedValue({}), isPending: false }))
vi.mock('../hooks', () => ({ useResearchActions: () => ({ action: mutation, error: vi.fn() }) }))
vi.mock('@/lib/hooks/use-translation', () => ({ useTranslation: () => ({ t: (key: string, params: Record<string, unknown> = {}) => Object.entries(params).reduce((s, [k, v]) => s.replaceAll('{{' + k + '}}', String(v)), researchEn[key.replace('research.', '') as keyof typeof researchEn] || key) }) }))
const run: ResearchRun = { id: 'test', question: 'Question', scope: '', as_of: '', language: 'English', auto_synthesize: true, execution_mode: 'browser', paused: false, status: 'running', created_at: '', updated_at: '', notebook_id: null, sync_error: null, stages: [
  { id: 'pre_research_claude', provider: 'Claude', round: 0, mode: 'account', status: 'running', attempts: 1, report: null, error: null, usage: null, note_id: null, browser_progress: null, retry_index: 0, next_retry_at: null },
] }
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('Research control protection', () => {
  it('offers distinct pause, immediate stop and end controls while running; does not submit on first click', async () => {
    render(<ResearchControls run={run} />)
    expect(screen.getByRole('button', { name: 'Pause after current steps' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'End research' })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Continue research' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Stop now' }))
    expect(screen.getByRole('alertdialog')).toBeVisible()
    expect(mutation.mutateAsync).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'I understand, continue' }))
    expect(mutation.mutateAsync).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('checkbox'))
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Yes, Stop now' })) })
    expect(mutation.mutateAsync).toHaveBeenCalledExactlyOnceWith({ id: 'test', action: 'stop', expectedState: {status:'running',paused:false,control_state:null,stages:[['pre_research_claude','running',1]]} })
  })
  it('dismisses a review without making any mutation', () => {
    render(<ResearchControls run={run} />)
    fireEvent.click(screen.getByRole('button', { name: 'End research' }))
    fireEvent.click(screen.getByRole('button', { name: 'Keep current state' }))
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    expect(mutation.mutateAsync).not.toHaveBeenCalled()
  })
  it('invalidates an accepted risk if polling changes an affected stage', () => {
    const view = render(<ResearchActionDialog run={run} action="stop" onClose={vi.fn()} onConfirm={mutation.mutateAsync} />)
    fireEvent.click(screen.getByRole('button', { name: 'I understand, continue' }))
    view.rerender(<ResearchActionDialog run={{ ...run, stages: [{ ...run.stages[0], status: 'completed' }] }} action="stop" onClose={vi.fn()} onConfirm={mutation.mutateAsync} />)
    expect(screen.queryByRole('button', { name: 'Yes, Stop now' })).not.toBeInTheDocument()
    expect(mutation.mutateAsync).not.toHaveBeenCalled()
  })
  it('explains web reconnection without promising remote cancellation', () => {
    render(<ResearchActionDialog run={{ ...run, stages: [{ ...run.stages[0], mode: 'browser' }] }} action="resume" onClose={vi.fn()} onConfirm={vi.fn()} />)
    expect(screen.getByText(/not a verified cancellation at the provider/)).toBeVisible()
    expect(screen.getByText(/uncertain submission is not blindly resent/)).toBeVisible()
  })
  it('locks further controls while stopping, and offers explicit restore only for an ended run', () => {
    const view = render(<ResearchControls run={{ ...run, paused: true, control_state: 'stopping', status: 'stopping' }} />)
    expect(screen.getByRole('button', { name: 'Stopping…' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Continue research' })).not.toBeInTheDocument()
    view.rerender(<ResearchControls run={{ ...run, paused: true, control_state: 'cancelled', status: 'cancelled', stages: [{ ...run.stages[0], status: 'interrupted' }] }} />)
    expect(screen.getByRole('button', { name: 'Restore and continue' })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Continue research' })).not.toBeInTheDocument()
  })
  it('guards double clicks during the final request', async () => {
    let resolve!: () => void
    const confirm = vi.fn(() => new Promise<void>(r => { resolve = r }))
    render(<ResearchActionDialog run={run} action="stop" onClose={vi.fn()} onConfirm={confirm} />)
    fireEvent.click(screen.getByRole('button', { name: 'I understand, continue' }))
    const button = screen.getByRole('button', { name: 'Yes, Stop now' })
    expect(button).toBeDisabled()
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(button); fireEvent.click(button)
    expect(confirm).toHaveBeenCalledOnce(); expect(button).toBeDisabled()
    await act(async () => resolve())
  })
})
