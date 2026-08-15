import { useQuery } from '@tanstack/react-query'
import { api, errorMessage } from '../../api/client'
import { StatusMessage } from '../../components/StatusMessage'
import { MediaCard } from '../media/MediaCard'
import { TranscriptionPanel } from '../transcription/TranscriptionPanel'
import { SyncControl } from './SyncControl'

export function ProjectWorkspace({ projectId, transcriptionAvailable }: { projectId: string; transcriptionAvailable?: boolean }) {
  const project = useQuery({ queryKey: ['project', projectId], queryFn: () => api.getProject(projectId) })
  if (project.isPending) return <div className="panel" role="status">Abrindo projeto…</div>
  if (project.isError) return <StatusMessage tone="error">Não foi possível abrir o projeto: {errorMessage(project.error)}</StatusMessage>
  const screen = project.data.media?.find((item) => item.role === 'SCREEN')
  const webcam = project.data.media?.find((item) => item.role === 'WEBCAM')
  return <main className="space-y-4"><div><p className="text-xs font-semibold uppercase tracking-[0.2em] text-sky-400">Projeto</p><h2 className="mt-1 text-2xl font-bold">{project.data.name}</h2></div>
    <div className="grid gap-4 xl:grid-cols-2"><MediaCard projectId={projectId} role="SCREEN" media={screen} /><MediaCard projectId={projectId} role="WEBCAM" media={webcam} /></div>
    <SyncControl key={`${projectId}:${project.data.webcam_offset_ms}`} projectId={projectId} initialOffset={project.data.webcam_offset_ms} />
    <TranscriptionPanel projectId={projectId} media={project.data.media ?? []} transcriptionAvailable={transcriptionAvailable} />
  </main>
}
