import type { ComponentType } from 'react'

export type ModuleInfo = {
  id: string; name: string; description: string; version: string
  activation: 'runtime' | 'external' | 'build'; dependencies: string[]
  installed: boolean; enabled: boolean
  frontend: { route: string; label_key: string; section: string; icon: string } | null
}
export type ModuleEntry = { Page: ComponentType; locales?: Record<string, Record<string, unknown>> }
