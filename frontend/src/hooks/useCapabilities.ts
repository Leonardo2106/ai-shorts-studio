import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

export function useCapabilities() {
  return useQuery({ queryKey: ['capabilities'], queryFn: api.capabilities, staleTime: 30_000 })
}
