import { FormEvent, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api, errorMessage } from '../../api/client'
import { StatusMessage } from '../../components/StatusMessage'

export function SyncControl({ projectId, initialOffset }: { projectId: string; initialOffset: number }) {
  const [offset, setOffset] = useState(String(initialOffset))
  const client = useQueryClient()
  const update = useMutation({ mutationFn: (value: number) => api.updateSync(projectId, value), onSuccess: (project) => { setOffset(String(project.webcam_offset_ms)); client.setQueryData(['project', projectId], project); void client.invalidateQueries({ queryKey: ['projects'] }) } })
  const numericOffset = Number(offset)
  const valid = Number.isInteger(numericOffset) && Math.abs(numericOffset) <= 86_400_000
  const submit = (event: FormEvent) => { event.preventDefault(); if (valid) update.mutate(numericOffset) }
  return <section className="panel"><h3 className="font-semibold">Sincronização manual</h3><p className="mt-1 text-sm text-slate-400">Valor positivo: a webcam começa depois da tela. Valor negativo: começa antes.</p>
    <form className="mt-4 flex flex-col gap-2 sm:flex-row sm:items-end" onSubmit={submit}><div className="flex-1"><label className="label" htmlFor="offset">Offset da webcam (ms)</label><input className="field" id="offset" type="number" step="1" min="-86400000" max="86400000" value={offset} onChange={(e) => setOffset(e.target.value)} aria-invalid={!valid} /></div><button className="button" disabled={!valid || update.isPending}>{update.isPending ? 'Salvando…' : 'Salvar offset'}</button></form>
    {!valid && <p className="mt-2 text-sm text-red-300">Use um número inteiro entre −86.400.000 e 86.400.000 ms.</p>}{update.isError && <div className="mt-2"><StatusMessage tone="error">{errorMessage(update.error)}</StatusMessage></div>}{update.isSuccess && <div className="mt-2"><StatusMessage tone="success">Offset persistido.</StatusMessage></div>}
  </section>
}
