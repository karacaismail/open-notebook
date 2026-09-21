'use client'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '@/lib/api/client'
import type { ModuleInfo } from './types'

export const moduleKey = ['module-catalog'] as const
export function useModules() {
  return useQuery({ queryKey: moduleKey, queryFn: async () => (await apiClient.get<ModuleInfo[]>('/modules')).data, retry: false, staleTime: 10_000, refetchInterval: 30_000 })
}
export function useModuleUpdate() {
  const client = useQueryClient()
  return useMutation({ mutationFn: async ({ id, enabled }: { id: string; enabled: boolean }) =>
    (await apiClient.put<ModuleInfo[]>('/modules/' + encodeURIComponent(id), { enabled })).data,
    retry: false, onSuccess: data => { client.setQueryData(moduleKey, data) },
  })
}
