import { FormEvent, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, errorMessage } from '../../api/client'
import { StatusMessage } from '../../components/StatusMessage'

export function ProjectList({ selectedId, onSelect }: { selectedId: string | null; onSelect: (id: string) => void }) {
  const [name, setName] = useState('')
  const client = useQueryClient()
  const projects = useQuery({ queryKey: ['projects'], queryFn: api.listProjects })
  const create = useMutation({ mutationFn: api.createProject, onSuccess: async (project) => { setName(''); await client.invalidateQueries({ queryKey: ['projects'] }); onSelect(project.id) } })
  const submit = (event: FormEvent) => { event.preventDefault(); const value = name.trim(); if (value) create.mutate(value) }

  return <aside className="panel h-fit lg:sticky lg:top-6" aria-label="Projetos">
    <h2 className="text-lg font-semibold">Projetos</h2>
    <form onSubmit={submit} className="mt-4 space-y-2">
      <label className="label" htmlFor="project-name">Novo projeto</label>
      <input id="project-name" className="field" value={name} onChange={(e) => setName(e.target.value)} placeholder="Nome da gravação" maxLength={120} />
      <button className="button w-full" disabled={!name.trim() || create.isPending}>{create.isPending ? 'Criando…' : 'Criar projeto'}</button>
      {create.isError && <StatusMessage tone="error">{errorMessage(create.error)}</StatusMessage>}
    </form>
    <div className="mt-6">
      {projects.isPending && <p className="text-sm text-slate-400" role="status">Carregando projetos…</p>}
      {projects.isError && <StatusMessage tone="error">{errorMessage(projects.error)}</StatusMessage>}
      {projects.data?.length === 0 && <p className="text-sm text-slate-400">Nenhum projeto. Crie o primeiro acima.</p>}
      <ul className="space-y-2">
        {projects.data?.map((project) => <li key={project.id}><button type="button" onClick={() => onSelect(project.id)} aria-current={selectedId === project.id ? 'page' : undefined} className={`w-full rounded-lg border p-3 text-left transition ${selectedId === project.id ? 'border-sky-500 bg-sky-950/40' : 'border-slate-800 hover:bg-slate-800'}`}>
          <span className="block font-medium">{project.name}</span><span className="text-xs text-slate-400">{project.media?.length ?? 0}/2 mídias</span>
        </button></li>)}
      </ul>
    </div>
  </aside>
}
