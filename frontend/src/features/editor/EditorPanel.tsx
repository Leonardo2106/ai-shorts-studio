import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, errorMessage } from '../../api/client'
import { StatusMessage } from '../../components/StatusMessage'
import type { Candidate, EditConfig, EditorElement, MediaFit } from '../../types/api'
import { createEditConfig, EDITOR_PRESETS } from './presets'

const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value))
const numeric = (value: string, fallback: number) => Number.isFinite(Number(value)) ? Number(value) : fallback
const captionTimingLabel = (source: import('../../types/api').CaptionCues['timing_source']) => source === 'WORDS' ? 'palavras' : source === 'WORDS_AND_SEGMENTS' ? 'palavras quando disponíveis, com fallback por segmento' : 'segmentos'

export function EditorPanel({ projectId, candidate, onClose }: { projectId: string; candidate: Candidate; onClose: () => void }) {
  const queryClient = useQueryClient()
  const stored = useQuery({ queryKey: ['edit-config', projectId, candidate.id], queryFn: () => api.getEditConfig(projectId, candidate.id), retry: (count, error) => !(error && typeof error === 'object' && 'status' in error && error.status === 404) && count < 2 })
  const cues = useQuery({ queryKey: ['caption-cues', projectId, candidate.id], queryFn: () => api.getCandidateCaptions(projectId, candidate.id) })
  const [config, setConfig] = useState<EditConfig>(() => createEditConfig())
  const [previewMs, setPreviewMs] = useState(0)
  const [selectedId, setSelectedId] = useState('webcam')
  const loadedId = useRef<string | null>(null)
  useEffect(() => { if (stored.data && loadedId.current !== candidate.id) { setConfig(stored.data.config); loadedId.current = candidate.id } }, [stored.data, candidate.id])
  const selected = config.elements.find((element) => element.id === selectedId)
  const activeCue = cues.data?.items.find((cue) => cue.start_ms <= previewMs && cue.end_ms > previewMs)
  const updateElement = (id: string, patch: Partial<EditorElement>) => setConfig((current) => ({ ...current, elements: current.elements.map((element) => element.id === id ? { ...element, ...patch } as EditorElement : element) }))
  const save = useMutation({ mutationFn: () => api.saveEditConfig(projectId, candidate.id, config), onSuccess: (data) => { setConfig(data.config); queryClient.setQueryData(['edit-config', projectId, candidate.id], data) } })

  const applyPreset = (presetId: string) => {
    const preset = EDITOR_PRESETS.find((item) => item.id === presetId) ?? EDITOR_PRESETS[0]
    setConfig(createEditConfig(preset))
  }
  const startDrag = (event: React.PointerEvent, element: EditorElement) => {
    if (element.kind === 'BACKGROUND') return
    event.currentTarget.setPointerCapture(event.pointerId)
    const startX = event.clientX; const startY = event.clientY; const originX = element.x; const originY = element.y
    const move = (next: PointerEvent) => updateElement(element.id, { x: clamp(originX + (next.clientX - startX) * 3, 0, 1080 - element.width), y: clamp(originY + (next.clientY - startY) * 3, 0, 1920 - element.height) })
    const finish = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', finish) }
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', finish)
  }

  return <section className="panel space-y-5" aria-labelledby="editor-title">
    <div className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-xs font-semibold uppercase tracking-wider text-sky-400">Editor 9:16</p><h3 id="editor-title" className="text-lg font-semibold">Layout do clip</h3><p className="text-xs text-slate-400">Preview leve no navegador; nenhum render FFmpeg é iniciado ao editar.</p></div><button className="button-secondary" type="button" onClick={onClose}>Fechar editor</button></div>
    {stored.isPending && <StatusMessage>Procurando configuração salva…</StatusMessage>}
    {stored.isError && <StatusMessage>Nenhuma configuração salva. Um preset inicial foi aplicado.</StatusMessage>}
    <div><label className="label" htmlFor="layout-preset">Preset de layout</label><select id="layout-preset" className="field" value={config.preset} onChange={(event) => applyPreset(event.target.value)}>{EDITOR_PRESETS.map((preset) => <option value={preset.id} key={preset.id}>{preset.label}</option>)}</select></div>
    <div className="grid gap-5 xl:grid-cols-[minmax(260px,360px)_1fr]">
      <div className="mx-auto aspect-[9/16] w-full max-w-[360px] overflow-hidden rounded-xl border border-slate-600 bg-slate-950 shadow-2xl" aria-label="Canvas lógico 1080 por 1920" style={{ position: 'relative' }}>
        {config.elements.slice().sort((a, b) => a.z_index - b.z_index).filter((element) => element.visible).map((element) => <button type="button" key={element.id} aria-label={`Selecionar e mover ${element.kind}`} onPointerDown={(event) => startDrag(event, element)} onClick={() => setSelectedId(element.id)} className={`absolute overflow-hidden border text-[10px] font-semibold uppercase ${selectedId === element.id ? 'border-sky-400 ring-1 ring-sky-400' : 'border-slate-600'} cursor-move`} style={{ left: `${element.x / 10.8}%`, top: `${element.y / 19.2}%`, width: `${element.width / 10.8}%`, height: `${element.height / 19.2}%`, zIndex: element.z_index, opacity: element.opacity, borderRadius: `${element.radius / 3}px`, background: element.kind === 'BANNER' ? config.banner.background_color : element.kind === 'CAPTIONS' ? config.captions.box_color ?? 'transparent' : element.kind === 'SCREEN' ? '#1e293b' : element.kind === 'WEBCAM' ? '#164e63' : config.background_color }}>
          {element.kind === 'CAPTIONS' ? <span style={{ color: config.captions.active_word_color ?? config.captions.color, fontSize: `${Math.max(9, config.captions.font_size / 4)}px`, textTransform: config.captions.uppercase ? 'uppercase' : 'none' }}>{activeCue?.text || 'Sem legenda neste instante'}</span> : element.kind === 'BANNER' ? config.banner.text : element.kind}
        </button>)}
      </div>
      <div className="space-y-4">
        <p className="text-sm text-slate-400">Trecho: {(candidate.start_ms / 1000).toFixed(1)}s–{(candidate.end_ms / 1000).toFixed(1)}s. Ajuste o corte na revisão do candidato.</p><label className="label">Instante do preview ({(previewMs / 1000).toFixed(1)}s)<input className="w-full" type="range" min={0} max={candidate.end_ms - candidate.start_ms} step={100} value={previewMs} onChange={(event) => setPreviewMs(Number(event.target.value))} /></label>{cues.data && <p className="text-xs text-slate-500">Timing: {captionTimingLabel(cues.data.timing_source)}.</p>}
        <div><p className="label">Camadas</p><div className="flex flex-wrap gap-2">{config.elements.map((element) => <button key={element.id} type="button" className={selectedId === element.id ? 'button' : 'button-secondary'} onClick={() => setSelectedId(element.id)}>{element.kind}{!element.visible ? ' (oculto)' : ''}</button>)}</div></div>
        {selected && <ElementControls element={selected} config={config} setConfig={setConfig} update={(patch) => updateElement(selected.id, patch)} />}
        <div className="flex flex-wrap items-center gap-3"><button type="button" className="button" disabled={save.isPending} onClick={() => save.mutate()}>{save.isPending ? 'Salvando…' : 'Salvar configuração'}</button>{save.isSuccess && <span className="text-sm text-emerald-400" role="status">Configuração salva.</span>}</div>
        {save.isError && <StatusMessage tone="error">Não foi possível salvar: {errorMessage(save.error)}</StatusMessage>}
      </div>
    </div>
  </section>
}

function NumberField({ label, value, onChange, min = 0, max }: { label: string; value: number; onChange: (value: number) => void; min?: number; max?: number }) {
  return <label className="text-xs text-slate-300">{label}<input className="field mt-1" type="number" min={min} max={max} value={Math.round(value)} onChange={(event) => onChange(numeric(event.target.value, value))} /></label>
}

function ElementControls({ element, config, setConfig, update }: { element: EditorElement; config: EditConfig; setConfig: React.Dispatch<React.SetStateAction<EditConfig>>; update: (patch: Partial<EditorElement>) => void }) {
  const caption = element.kind === 'CAPTIONS'
  const media = element.kind === 'SCREEN' || element.kind === 'WEBCAM'
  return <fieldset className="rounded-xl border border-slate-700 p-4"><legend className="px-2 font-semibold">{element.kind}</legend>
    <label className="mb-3 flex items-center gap-2 text-sm"><input type="checkbox" checked={element.visible} onChange={(event) => update({ visible: event.target.checked })} /> Visível</label>
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4"><NumberField label="X" value={element.x} max={1080} onChange={(x) => update({ x: clamp(x, 0, 1080 - element.width) })} /><NumberField label="Y" value={element.y} max={1920} onChange={(y) => update({ y: clamp(y, 0, 1920 - element.height) })} /><NumberField label="Largura" value={element.width} min={40} max={1080} onChange={(width) => update({ width: clamp(width, 40, 1080 - element.x) })} /><NumberField label="Altura" value={element.height} min={40} max={1920} onChange={(height) => update({ height: clamp(height, 40, 1920 - element.y) })} /><NumberField label="Camada" value={element.z_index} max={99} onChange={(z_index) => update({ z_index })} /><NumberField label="Opacidade %" value={element.opacity * 100} max={100} onChange={(value) => update({ opacity: clamp(value / 100, 0, 1) })} /><NumberField label="Borda" value={element.border_width ?? 0} max={50} onChange={(border_width) => update({ border_width })} /><NumberField label="Raio" value={element.radius ?? 0} max={540} onChange={(radius) => update({ radius })} /><NumberField label="Padding" value={element.padding ?? 0} max={300} onChange={(padding) => update({ padding })} /></div>
    {media && <label className="label mt-3">Ajuste<select className="field" value={element.fit} onChange={(event) => update({ fit: event.target.value as MediaFit })}><option value="COVER">Preencher</option><option value="CONTAIN">Conter</option><option value="CROP">Recortar</option></select></label>}
    {caption && <div className="mt-4 grid gap-3 sm:grid-cols-2"><NumberField label="Tamanho da fonte" value={config.captions.font_size} min={12} max={240} onChange={(font_size) => setConfig((current) => ({ ...current, captions: { ...current.captions, font_size } }))} /><NumberField label="Palavras por linha" value={config.captions.words_per_line} min={1} max={20} onChange={(words_per_line) => setConfig((current) => ({ ...current, captions: { ...current.captions, words_per_line } }))} /><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={config.captions.enabled} onChange={(event) => setConfig((current) => ({ ...current, captions: { ...current.captions, enabled: event.target.checked } }))} /> Legendas ativas (usa timestamps por palavra quando disponíveis)</label><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={config.captions.uppercase} onChange={(event) => setConfig((current) => ({ ...current, captions: { ...current.captions, uppercase: event.target.checked } }))} /> Caixa alta</label><label className="label">Cor<input type="color" className="field" value={config.captions.color} onChange={(event) => setConfig((current) => ({ ...current, captions: { ...current.captions, color: event.target.value } }))} /></label><label className="label">Palavra ativa<input type="color" className="field" value={config.captions.active_word_color ?? '#FFFFFF'} onChange={(event) => setConfig((current) => ({ ...current, captions: { ...current.captions, active_word_color: event.target.value } }))} /></label></div>}
    {element.kind === 'BANNER' && <div className="mt-4 grid gap-3 sm:grid-cols-2"><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={config.banner.enabled} onChange={(event) => setConfig((current) => ({ ...current, banner: { ...current.banner, enabled: event.target.checked } }))} /> Banner ativo</label><label className="label">Texto<input className="field" value={config.banner.text} onChange={(event) => setConfig((current) => ({ ...current, banner: { ...current.banner, text: event.target.value } }))} /></label><label className="label">Fundo<input className="field" type="color" value={config.banner.background_color} onChange={(event) => setConfig((current) => ({ ...current, banner: { ...current.banner, background_color: event.target.value } }))} /></label><NumberField label="Exibir de (ms)" value={config.banner.start_ms} onChange={(start_ms) => setConfig((current) => ({ ...current, banner: { ...current.banner, start_ms } }))} /><NumberField label="Exibir até (ms)" value={config.banner.end_ms ?? candidateDurationFallback(config.banner.start_ms)} onChange={(end_ms) => setConfig((current) => ({ ...current, banner: { ...current.banner, end_ms } }))} /></div>}
  </fieldset>
}

const candidateDurationFallback = (start: number) => start + 60_000
