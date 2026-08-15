import type { ApiErrorBody, Capabilities, Job, MediaAsset, MediaRole, Project, StartTranscription, Transcript } from '../types/api'

const API_ROOT = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '') ?? '/api/v1'

export class ApiError extends Error {
  constructor(public readonly status: number, public readonly code: string, message: string, public readonly details?: unknown) { super(message); this.name = 'ApiError' }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_ROOT}${path}`, { ...init, headers: { ...(!(init?.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}), ...init?.headers } })
  if (!response.ok) {
    let body: ApiErrorBody = {}
    try { body = await response.json() as ApiErrorBody } catch { /* non-JSON backend response */ }
    throw new ApiError(response.status, body.error?.code ?? 'HTTP_ERROR', body.error?.message ?? body.detail ?? `Falha na requisição (${response.status})`, body.error?.details)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const api = {
  capabilities: () => request<Capabilities>('/capabilities'),
  listProjects: () => request<{ items: Project[] }>('/projects').then((response) => response.items),
  createProject: (name: string) => request<Project>('/projects', { method: 'POST', body: JSON.stringify({ name }) }),
  getProject: (id: string) => request<Project>(`/projects/${encodeURIComponent(id)}`),
  updateSync: (id: string, webcamOffsetMs: number) => request<Project>(`/projects/${encodeURIComponent(id)}/sync`, { method: 'PATCH', body: JSON.stringify({ webcam_offset_ms: webcamOffsetMs }) }),
  uploadMedia: (id: string, role: MediaRole, file: File) => { const body = new FormData(); body.append('role', role); body.append('file', file); return request<MediaAsset>(`/projects/${encodeURIComponent(id)}/media`, { method: 'POST', body }) },
  startTranscription: (id: string, input: StartTranscription) => request<Job>(`/projects/${encodeURIComponent(id)}/transcription-jobs`, { method: 'POST', body: JSON.stringify(input) }),
  getJob: (id: string) => request<Job>(`/jobs/${encodeURIComponent(id)}`),
  cancelJob: (id: string) => request<Job>(`/jobs/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),
  getTranscript: (projectId: string, id: string) => request<Transcript>(`/projects/${encodeURIComponent(projectId)}/transcripts/${encodeURIComponent(id)}`),
}

export function errorMessage(error: unknown): string { return error instanceof Error ? error.message : 'Ocorreu um erro inesperado.' }
export function mediaContentUrl(path: string): string { if (/^https?:\/\//i.test(path)) return path; const base = (import.meta.env.VITE_BACKEND_URL as string | undefined)?.replace(/\/$/, '') ?? ''; return `${base}${path.startsWith('/') ? '' : '/'}${path}` }
