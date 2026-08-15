import type { ReactNode } from 'react'

export function StatusMessage({ tone = 'neutral', children }: { tone?: 'neutral' | 'error' | 'success'; children: ReactNode }) {
  const color = tone === 'error' ? 'border-red-900 bg-red-950/60 text-red-200' : tone === 'success' ? 'border-emerald-900 bg-emerald-950/60 text-emerald-200' : 'border-slate-700 bg-slate-950 text-slate-300'
  return <div className={`rounded-lg border p-3 text-sm ${color}`} role={tone === 'error' ? 'alert' : 'status'}>{children}</div>
}
