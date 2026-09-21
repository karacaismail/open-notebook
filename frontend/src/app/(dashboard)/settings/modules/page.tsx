'use client'
import { Puzzle, Loader2 } from 'lucide-react'
import { isAxiosError } from 'axios'
import { AppShell } from '@/components/layout/AppShell'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { useTranslation } from '@/lib/hooks/use-translation'
import { useModules, useModuleUpdate } from '@/lib/modules/hooks'

export default function ModulesPage() {
  const { t } = useTranslation(), query = useModules(), update = useModuleUpdate()
  const message = isAxiosError(update.error) && typeof update.error.response?.data?.detail === 'string' ? update.error.response.data.detail : t('modules.operationError')
  return <AppShell><main className="flex-1 overflow-y-auto p-6 md:p-9"><div className="mx-auto max-w-5xl space-y-6">
    <header><h1 className="flex items-center gap-3 text-2xl font-semibold"><Puzzle aria-hidden className="size-6" />{t('modules.title')}</h1><p className="mt-2 text-sm text-muted-foreground">{t('modules.subtitle')}</p></header>
    {query.isLoading && <p role="status">{t('common.loading')}</p>}
    {query.isError && <div role="alert">{t('modules.error')} <Button variant="outline" onClick={() => query.refetch()}>{t('modules.retry')}</Button></div>}
    {update.isError && <p role="alert" className="rounded-xl border border-destructive p-4">{message}</p>}
    {update.isSuccess && <p role="status">{t('modules.saved')}</p>}
    <div className="grid gap-4 md:grid-cols-2">{query.data?.map(module => <section key={module.id} className="rounded-2xl border bg-card p-5">
      <div className="flex flex-wrap items-start justify-between gap-3"><h2 className="font-semibold">{module.name}</h2><Badge variant="outline">{t(module.enabled ? 'modules.enabled' : 'modules.disabled')}</Badge></div>
      <p className="mt-3 text-sm text-muted-foreground">{module.description}</p>
      <p className="mt-3 text-xs text-muted-foreground">{module.version} · {t('modules.' + module.activation)} · {t(module.installed ? 'modules.installed' : 'modules.notInstalled')}</p>
      <p className="mt-2 text-xs">{t('modules.dependencies')}: {module.dependencies.join(', ') || t('modules.noDependencies')}</p>
      <p className="mt-4 text-xs leading-relaxed text-muted-foreground">{t('modules.' + (module.installed ? module.activation + 'Help' : 'installHelp'))}</p>
      {module.installed && module.activation === 'runtime' && <Button className="mt-4" variant={module.enabled ? 'outline' : 'default'} disabled={update.isPending} onClick={() => update.mutate({ id: module.id, enabled: !module.enabled })}>
        {update.isPending && update.variables?.id === module.id && <Loader2 aria-hidden className="mr-2 size-4 animate-spin" />}{t(module.enabled ? 'modules.disable' : 'modules.enable')}
      </Button>}
    </section>)}</div>
  </div></main></AppShell>
}
