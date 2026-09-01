import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, errorMessage } from '../../api/client'
import { StatusMessage } from '../../components/StatusMessage'
import type { Candidate, CaptionStyle, EditConfig, EditorElement, MediaAsset, MediaFit, Transcript } from '../../types/api'
import { CAPTION_PRESETS, createEditConfig, EDITOR_PRESETS, normalizeEditConfig, PORTABLE_CAPTION_FONTS, type CaptionPresetId } from './presets'
import { RenderingPanel } from '../rendering/RenderingPanel'
import { createCaptionPreviewTimeline, type CaptionPreview } from './captionPreview'

const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value))
const numeric = (value: string, fallback: number) => Number.isFinite(Number(value)) ? Number(value) : fallback
const captionTimingLabel = (source: import('../../types/api').CaptionCues['timing_source']) => source === 'WORDS' ? 'palavras' : source === 'WORDS_AND_SEGMENTS' ? 'palavras quando disponíveis, com fallback por segmento' : 'segmentos'

export function EditorPanel({ projectId, candidate, media = [], transcript, onClose }: { projectId: string; candidate: Candidate; media?: MediaAsset[]; transcript?: Transcript; onClose: () => void }) {
  const queryClient = useQueryClient()
  const stored = useQuery({ queryKey: ['edit-config', projectId, candidate.id], queryFn: () => api.getEditConfig(projectId, candidate.id), retry: (count, error) => !(error && typeof error === 'object' && 'status' in error && error.status === 404) && count < 2 })
  const cues = useQuery({ queryKey: ['caption-cues', projectId, candidate.id], queryFn: () => api.getCandidateCaptions(projectId, candidate.id) })
  const [config, setConfig] = useState<EditConfig>(() => createEditConfig())
  const [previewMs, setPreviewMs] = useState(0)
  const [selectedId, setSelectedId] = useState('webcam')
  const [captionPreset, setCaptionPreset] = useState<CaptionPresetId>('CLEAN')
  const initializedId = useRef<string | null>(null)
  const currentCandidateId = useRef(candidate.id)
  currentCandidateId.current = candidate.id
  const [readyCandidateId, setReadyCandidateId] = useState<string | null>(null)
  const [savedSignature, setSavedSignature] = useState<string | null>(null)
  const configNotFound = stored.isError && typeof stored.error === 'object' && stored.error !== null && 'status' in stored.error && stored.error.status === 404
  useEffect(() => {
    if (initializedId.current === candidate.id) return
    if (stored.data) {
      const normalized = normalizeEditConfig(stored.data.config)
      setConfig(normalized); setCaptionPreset('CUSTOM'); setSavedSignature(JSON.stringify(normalized)); setSelectedId('webcam'); setPreviewMs(0)
      initializedId.current = candidate.id; setReadyCandidateId(candidate.id)
    } else if (configNotFound) {
      const initial = createEditConfig()
      setConfig(initial); setCaptionPreset('CLEAN'); setSavedSignature(null); setSelectedId('webcam'); setPreviewMs(0)
      initializedId.current = candidate.id; setReadyCandidateId(candidate.id)
    }
  }, [candidate.id, configNotFound, stored.data])
  const selected = config.elements.find((element) => element.id === selectedId)
  const clipDurationMs = candidate.end_ms - candidate.start_ms
  const captionTimeline = useMemo(() => cues.data ? createCaptionPreviewTimeline(cues.data.items, {
    words_per_block: config.captions.words_per_block,
    gap_tolerance_ms: config.captions.gap_tolerance_ms,
    min_display_ms: config.captions.min_display_ms,
    hold_ms: config.captions.hold_ms,
  }, clipDurationMs) : undefined, [cues.data, config.captions.words_per_block, config.captions.gap_tolerance_ms, config.captions.min_display_ms, config.captions.hold_ms, clipDurationMs])
  const captionPreview = captionTimeline?.at(previewMs)
  const updateElement = (id: string, patch: Partial<EditorElement>) => setConfig((current) => ({ ...current, elements: current.elements.map((element) => element.id === id ? { ...element, ...patch } as EditorElement : element) }))
  const save = useMutation({ mutationFn: ({ candidateId, nextConfig }: { candidateId: string; nextConfig: EditConfig }) => api.saveEditConfig(projectId, candidateId, nextConfig), onSuccess: (data, variables) => { if (variables.candidateId !== currentCandidateId.current) return; const normalized = normalizeEditConfig(data.config); setConfig(normalized); setSavedSignature(JSON.stringify(normalized)); queryClient.setQueryData(['edit-config', projectId, variables.candidateId], { ...data, config: normalized }) } })
  const configSignature = JSON.stringify(config)
  const saveCurrent = () => save.mutateAsync({ candidateId: candidate.id, nextConfig: normalizeEditConfig(config) })

  const applyPreset = (presetId: string) => {
    const preset = EDITOR_PRESETS.find((item) => item.id === presetId) ?? EDITOR_PRESETS[0]
    setConfig((current) => ({ ...createEditConfig(preset), audio: current.audio }))
    setCaptionPreset('CLEAN')
  }
  const updateCaptions = (patch: Partial<CaptionStyle>) => { setCaptionPreset('CUSTOM'); setConfig((current) => ({ ...current, captions: { ...current.captions, ...patch } })) }
  const applyCaptionPreset = (preset: CaptionPresetId) => { setCaptionPreset(preset); if (preset !== 'CUSTOM') setConfig((current) => ({ ...current, captions: { ...CAPTION_PRESETS[preset].style } })) }
  const startDrag = (event: React.PointerEvent, element: EditorElement) => {
    if (element.kind === 'BACKGROUND') return
    event.currentTarget.setPointerCapture(event.pointerId)
    const startX = event.clientX; const startY = event.clientY; const originX = element.x; const originY = element.y
    const move = (next: PointerEvent) => updateElement(element.id, { x: clamp(originX + (next.clientX - startX) * 3, 0, 1080 - element.width), y: clamp(originY + (next.clientY - startY) * 3, 0, 1920 - element.height) })
    const finish = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', finish) }
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', finish)
  }

  const header = <div className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-xs font-semibold uppercase tracking-wider text-sky-400">Editor 9:16</p><h3 id="editor-title" className="text-lg font-semibold">Layout do clip</h3><p className="text-xs text-slate-400">Preview leve no navegador; nenhum render FFmpeg é iniciado ao editar.</p></div><button className="button-secondary" type="button" onClick={onClose}>Fechar editor</button></div>
  if (readyCandidateId !== candidate.id) return <section className="panel space-y-5" aria-labelledby="editor-title">{header}{stored.isPending && <StatusMessage>Procurando configuração salva…</StatusMessage>}{configNotFound && <StatusMessage>Preparando um preset inicial para este candidato…</StatusMessage>}{stored.isError && !configNotFound && <><StatusMessage tone="error">Não foi possível carregar a configuração: {errorMessage(stored.error)}</StatusMessage><button type="button" className="button-secondary" onClick={() => stored.refetch()}>Tentar novamente</button></>}</section>

  return <section className="panel space-y-5" aria-labelledby="editor-title">
    {header}
    {configNotFound && <StatusMessage>Nenhuma configuração salva. Um preset inicial foi aplicado.</StatusMessage>}
    <div><label className="label" htmlFor="layout-preset">Preset de layout</label><select id="layout-preset" className="field" value={config.preset} onChange={(event) => applyPreset(event.target.value)}>{EDITOR_PRESETS.map((preset) => <option value={preset.id} key={preset.id}>{preset.label}</option>)}</select></div>
    <div className="grid gap-5 xl:grid-cols-[minmax(260px,360px)_1fr]">
      <div className="mx-auto aspect-[9/16] w-full max-w-[360px] overflow-hidden rounded-xl border border-slate-600 bg-slate-950 shadow-2xl" aria-label="Canvas lógico 1080 por 1920" style={{ position: 'relative' }}>
        {config.elements.slice().sort((a, b) => a.z_index - b.z_index).filter((element) => element.visible && (element.kind !== 'CAPTIONS' || config.captions.enabled)).map((element) => <button type="button" key={element.id} aria-label={`Selecionar e mover ${element.kind}`} onPointerDown={(event) => startDrag(event, element)} onClick={() => setSelectedId(element.id)} className={`absolute overflow-hidden border text-[10px] font-semibold uppercase ${selectedId === element.id ? 'border-sky-400 ring-1 ring-sky-400' : 'border-slate-600'} cursor-move`} style={{ left: `${element.x / 10.8}%`, top: `${element.y / 19.2}%`, width: `${element.width / 10.8}%`, height: `${element.height / 19.2}%`, zIndex: element.z_index, opacity: element.opacity, background: element.kind === 'BANNER' ? config.banner.background_color : element.kind === 'CAPTIONS' ? config.captions.box_color ?? 'transparent' : element.kind === 'SCREEN' ? '#1e293b' : element.kind === 'WEBCAM' ? '#164e63' : config.background_color }}>
          <ElementPreviewContent element={element} config={config} captionPreview={captionPreview} captionsReady={cues.isSuccess} />
        </button>)}
      </div>
      <div className="space-y-4">
        <p className="text-sm text-slate-400">Trecho: {(candidate.start_ms / 1000).toFixed(1)}s–{(candidate.end_ms / 1000).toFixed(1)}s. Ajuste o corte na revisão do candidato.</p><label className="label">Instante do preview ({(previewMs / 1000).toFixed(1)}s)<input className="w-full" type="range" min={0} max={clipDurationMs} step={100} value={previewMs} onChange={(event) => setPreviewMs(Number(event.target.value))} /></label>
        {cues.isPending && <StatusMessage>Carregando legendas automáticas…</StatusMessage>}
        {cues.isError && <div className="space-y-2"><StatusMessage tone="error">Não foi possível carregar as legendas: {errorMessage(cues.error)}</StatusMessage><button type="button" className="button-secondary" onClick={() => void cues.refetch()}>Tentar carregar legendas novamente</button></div>}
        {cues.data && <p className="text-xs text-slate-500">Timing: {captionTimingLabel(cues.data.timing_source)}.</p>}
        <div><p className="label">Camadas</p><div className="flex flex-wrap gap-2">{config.elements.map((element) => <button key={element.id} type="button" className={selectedId === element.id ? 'button' : 'button-secondary'} onClick={() => setSelectedId(element.id)}>{element.kind}{!element.visible ? ' (oculto)' : ''}</button>)}</div></div>
        <AudioControls media={media} transcript={transcript} config={config} setConfig={setConfig} />
        {selected && <ElementControls element={selected} config={config} setConfig={setConfig} update={(patch) => updateElement(selected.id, patch)} captionPreset={captionPreset} onCaptionPreset={applyCaptionPreset} updateCaptions={updateCaptions} />}
        <div className="flex flex-wrap items-center gap-3"><button type="button" className="button" disabled={save.isPending} onClick={() => void saveCurrent()}>{save.isPending ? 'Salvando…' : 'Salvar configuração'}</button>{save.isSuccess && <span className="text-sm text-emerald-400" role="status">Configuração salva.</span>}</div>
        {save.isError && <StatusMessage tone="error">Não foi possível salvar: {errorMessage(save.error)}</StatusMessage>}
      </div>
    </div>
    <RenderingPanel key={candidate.id} projectId={projectId} candidateId={candidate.id} configSignature={configSignature} configDirty={savedSignature !== configSignature} saveConfig={saveCurrent} />
  </section>
}

function NumberField({ label, value, onChange, min = 0, max }: { label: string; value: number; onChange: (value: number) => void; min?: number; max?: number }) {
  return <label className="text-xs text-slate-300">{label}<input className="field mt-1" type="number" min={min} max={max} value={Math.round(value)} onChange={(event) => onChange(numeric(event.target.value, value))} /></label>
}

function ElementPreviewContent({ element, config, captionPreview, captionsReady }: { element: EditorElement; config: EditConfig; captionPreview?: CaptionPreview; captionsReady: boolean }) {
  if (element.kind === 'CAPTIONS') {
    if (!config.captions.enabled || !captionsReady) return null
    return <span style={{ color: config.captions.color, fontFamily: config.captions.font_family, fontSize: `${Math.max(9, config.captions.font_size / 4)}px`, fontStyle: config.captions.italic ? 'italic' : 'normal', fontWeight: config.captions.weight, textTransform: config.captions.uppercase ? 'uppercase' : 'none' }}>{captionPreview?.kind === 'WORDS' ? captionPreview.words.map((word, index) => <span key={`${word.startMs}-${word.endMs}-${index}`} data-caption-active={word.active || undefined} style={{ color: word.active ? config.captions.active_word_color ?? config.captions.color : config.captions.color }}>{index > 0 ? ' ' : ''}{word.text}</span>) : captionPreview?.text || 'Sem legenda neste instante'}</span>
  }
  if (element.kind === 'BANNER') return <>{config.banner.text}</>
  if (element.kind === 'SCREEN' || element.kind === 'WEBCAM') return <span className="relative flex h-full w-full items-center justify-center"><span>{element.kind} · {element.fit === 'CONTAIN' ? 'conter' : `${element.zoom.toFixed(1)}×`}</span>{element.fit !== 'CONTAIN' && <span aria-hidden="true" className="absolute h-2 w-2 rounded-full border border-white bg-sky-400" style={{ left: `${element.focal_x * 100}%`, top: `${element.focal_y * 100}%`, transform: 'translate(-50%, -50%)' }} />}</span>
  return <>{element.kind}</>
}

function ElementControls({ element, config, setConfig, update, captionPreset, onCaptionPreset, updateCaptions }: { element: EditorElement; config: EditConfig; setConfig: React.Dispatch<React.SetStateAction<EditConfig>>; update: (patch: Partial<EditorElement>) => void; captionPreset: CaptionPresetId; onCaptionPreset: (preset: CaptionPresetId) => void; updateCaptions: (patch: Partial<CaptionStyle>) => void }) {
  const caption = element.kind === 'CAPTIONS'
  const media = element.kind === 'SCREEN' || element.kind === 'WEBCAM'
  return <fieldset className="rounded-xl border border-slate-700 p-4"><legend className="px-2 font-semibold">{element.kind}</legend>
    <label className="mb-3 flex items-center gap-2 text-sm"><input type="checkbox" checked={element.visible} onChange={(event) => update({ visible: event.target.checked })} /> Visível</label>
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4"><NumberField label="X" value={element.x} max={1080} onChange={(x) => update({ x: clamp(x, 0, 1080 - element.width) })} /><NumberField label="Y" value={element.y} max={1920} onChange={(y) => update({ y: clamp(y, 0, 1920 - element.height) })} /><NumberField label="Largura" value={element.width} min={40} max={1080} onChange={(width) => update({ width: clamp(width, 40, 1080 - element.x) })} /><NumberField label="Altura" value={element.height} min={40} max={1920} onChange={(height) => update({ height: clamp(height, 40, 1920 - element.y) })} /><NumberField label="Camada" value={element.z_index} max={99} onChange={(z_index) => update({ z_index })} /><NumberField label="Opacidade %" value={element.opacity * 100} max={100} onChange={(value) => update({ opacity: clamp(value / 100, 0, 1) })} />{media && <NumberField label="Borda" value={element.border_width ?? 0} max={50} onChange={(border_width) => update({ border_width })} />}</div>
    {media && <div className="mt-4 space-y-3"><label className="label">Ajuste<select className="field" value={element.fit} onChange={(event) => { const fit = event.target.value as MediaFit; update({ fit, ...(fit === 'CONTAIN' ? { zoom: 1, focal_x: .5, focal_y: .5 } : {}) }) }}><option value="COVER">Preencher</option><option value="CONTAIN">Conter</option><option value="CROP">Recortar</option></select></label><label className="label">Zoom ({element.zoom.toFixed(1)}×)<input aria-label="Zoom" className="w-full" type="range" min={1} max={3} step={.1} value={element.zoom} disabled={element.fit === 'CONTAIN'} onChange={(event) => update({ zoom: Number(event.target.value) })} /></label><div className="grid grid-cols-2 gap-3"><label className="label">Foco horizontal ({Math.round(element.focal_x * 100)}%)<input aria-label="Foco horizontal" className="w-full" type="range" min={0} max={100} value={element.focal_x * 100} disabled={element.fit === 'CONTAIN'} onChange={(event) => update({ focal_x: Number(event.target.value) / 100 })} /></label><label className="label">Foco vertical ({Math.round(element.focal_y * 100)}%)<input aria-label="Foco vertical" className="w-full" type="range" min={0} max={100} value={element.focal_y * 100} disabled={element.fit === 'CONTAIN'} onChange={(event) => update({ focal_y: Number(event.target.value) / 100 })} /></label></div>{element.fit === 'CONTAIN' && <p className="text-xs text-amber-300">Zoom e foco ficam neutros em “Conter”. Use “Preencher sem bordas” para habilitar o enquadramento.</p>}<button type="button" className="button-secondary" onClick={() => update({ fit: 'COVER', padding: 0, border_width: 0 })}>Preencher sem bordas</button><p className="text-xs text-slate-500">O ponto indica o foco do único recorte configurado; o FFmpeg Preview confirma o enquadramento fiel da mídia.</p></div>}
    {caption && <div className="mt-4 space-y-4"><label className="label">Estilo de legenda<select className="field" value={captionPreset} onChange={(event) => onCaptionPreset(event.target.value as CaptionPresetId)}>{Object.entries(CAPTION_PRESETS).map(([id, preset]) => <option key={id} value={id}>{preset.label}</option>)}<option value="CUSTOM">Custom</option></select></label><div className="grid gap-3 sm:grid-cols-2"><label className="label">Família da fonte<select className="field" value={config.captions.font_family} onChange={(event) => updateCaptions({ font_family: event.target.value })}>{!(PORTABLE_CAPTION_FONTS as readonly string[]).includes(config.captions.font_family) && <option value={config.captions.font_family}>{config.captions.font_family} (salva)</option>}{PORTABLE_CAPTION_FONTS.map((font) => <option key={font} value={font}>{font === 'sans-serif' ? 'Sans-serif do sistema' : font}</option>)}</select><span className="mt-1 block text-xs font-normal text-slate-500">O resultado depende da fonte instalada. Se ela não estiver disponível, o FFmpeg pode usar uma fonte fallback.</span></label><NumberField label="Tamanho da fonte" value={config.captions.font_size} min={12} max={240} onChange={(font_size) => updateCaptions({ font_size })} /><label className="label">Peso<select className="field" value={config.captions.weight} onChange={(event) => updateCaptions({ weight: Number(event.target.value) })}><option value={400}>Regular</option><option value={700}>Bold</option><option value={800}>Extra bold</option><option value={900}>Black</option></select></label><NumberField label="Contorno" value={config.captions.outline_width} max={20} onChange={(outline_width) => updateCaptions({ outline_width })} /><label className="label">Cor do texto<input type="color" className="field" value={config.captions.color} onChange={(event) => updateCaptions({ color: event.target.value })} /></label><label className="label">Palavra ativa<input type="color" className="field" value={config.captions.active_word_color ?? '#FFFFFF'} onChange={(event) => updateCaptions({ active_word_color: event.target.value })} /></label><label className="label">Cor do contorno<input type="color" className="field" value={config.captions.outline_color} onChange={(event) => updateCaptions({ outline_color: event.target.value })} /></label>{config.captions.box_color && <label className="label">Cor do fundo<input type="color" className="field" value={config.captions.box_color} onChange={(event) => updateCaptions({ box_color: event.target.value })} /></label>}<NumberField label="Palavras por linha" value={config.captions.words_per_line} min={1} max={20} onChange={(words_per_line) => updateCaptions({ words_per_line })} /><NumberField label="Palavras por bloco" value={config.captions.words_per_block} min={1} max={50} onChange={(words_per_block) => updateCaptions({ words_per_block })} /><NumberField label="Unir pausas até (ms)" value={config.captions.gap_tolerance_ms} max={1000} onChange={(gap_tolerance_ms) => updateCaptions({ gap_tolerance_ms })} /><NumberField label="Exibição mínima (ms)" value={config.captions.min_display_ms} max={2000} onChange={(min_display_ms) => updateCaptions({ min_display_ms })} /><NumberField label="Segurar após fala (ms)" value={config.captions.hold_ms} max={2000} onChange={(hold_ms) => updateCaptions({ hold_ms })} /></div><div className="grid gap-2 sm:grid-cols-2"><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={config.captions.enabled} onChange={(event) => updateCaptions({ enabled: event.target.checked })} /> Legendas ativas</label><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={config.captions.uppercase} onChange={(event) => updateCaptions({ uppercase: event.target.checked })} /> Caixa alta</label><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={config.captions.italic} onChange={(event) => updateCaptions({ italic: event.target.checked })} /> Itálico</label><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={config.captions.shadow} onChange={(event) => updateCaptions({ shadow: event.target.checked })} /> Sombra</label><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={config.captions.box_color !== null} onChange={(event) => updateCaptions({ box_color: event.target.checked ? '#000000' : null })} /> Fundo da legenda</label></div></div>}
    {element.kind === 'BANNER' && <div className="mt-4 grid gap-3 sm:grid-cols-2"><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={config.banner.enabled} onChange={(event) => setConfig((current) => ({ ...current, banner: { ...current.banner, enabled: event.target.checked } }))} /> Banner ativo</label><label className="label">Texto<input className="field" value={config.banner.text} onChange={(event) => setConfig((current) => ({ ...current, banner: { ...current.banner, text: event.target.value } }))} /></label><label className="label">Fundo<input className="field" type="color" value={config.banner.background_color} onChange={(event) => setConfig((current) => ({ ...current, banner: { ...current.banner, background_color: event.target.value } }))} /></label><NumberField label="Exibir de (ms)" value={config.banner.start_ms} onChange={(start_ms) => setConfig((current) => ({ ...current, banner: { ...current.banner, start_ms } }))} /><NumberField label="Exibir até (ms)" value={config.banner.end_ms ?? candidateDurationFallback(config.banner.start_ms)} onChange={(end_ms) => setConfig((current) => ({ ...current, banner: { ...current.banner, end_ms } }))} /></div>}
  </fieldset>
}

const candidateDurationFallback = (start: number) => start + 60_000

function AudioControls({ media, transcript, config, setConfig }: { media: MediaAsset[]; transcript?: Transcript; config: EditConfig; setConfig: React.Dispatch<React.SetStateAction<EditConfig>> }) {
  const available = media.flatMap((asset) => asset.probe.audio_streams.map((stream) => ({ asset, stream })))
  const keys = new Set(available.map(({ asset, stream }) => `${asset.id}:${stream.index}`))
  const missing = config.audio.tracks.filter((track) => track.enabled && !keys.has(`${track.media_id}:${track.stream_index}`))
  const activeTrackCount = config.audio.tracks.filter((track) => track.enabled).length
  const setMode = (mode: EditConfig['audio']['mode']) => setConfig((current) => { const additions = mode === 'CUSTOM' ? available.filter(({ asset, stream }) => !current.audio.tracks.some((track) => track.media_id === asset.id && track.stream_index === stream.index)).map(({ asset, stream }) => ({ media_id: asset.id, stream_index: stream.index, enabled: asset.id === transcript?.media_id && stream.index === transcript.audio_stream_index, gain_db: 0 })) : []; return { ...current, audio: { mode, tracks: [...current.audio.tracks, ...additions] } } })
  const updateTrack = (mediaId: string, streamIndex: number, patch: Partial<EditConfig['audio']['tracks'][number]>) => setConfig((current) => { const exists = current.audio.tracks.some((track) => track.media_id === mediaId && track.stream_index === streamIndex); return { ...current, audio: { ...current.audio, tracks: exists ? current.audio.tracks.map((track) => track.media_id === mediaId && track.stream_index === streamIndex ? { ...track, ...patch } : track) : [...current.audio.tracks, { media_id: mediaId, stream_index: streamIndex, enabled: false, gain_db: 0, ...patch }] } } })
  return <fieldset className="rounded-xl border border-slate-700 p-4"><legend className="px-2 font-semibold">Áudio</legend><label className="label">Fonte do áudio<select className="field" value={config.audio.mode} onChange={(event) => setMode(event.target.value as EditConfig['audio']['mode'])}><option value="TRANSCRIPT_DEFAULT">Track usada na transcrição</option><option value="CUSTOM">Seleção personalizada</option></select></label>{config.audio.mode === 'CUSTOM' && <div className="mt-3 space-y-3">{available.map(({ asset, stream }) => { const track = config.audio.tracks.find((item) => item.media_id === asset.id && item.stream_index === stream.index); const enabled = track?.enabled ?? false; return <div className="rounded border border-slate-700 p-3" key={`${asset.id}:${stream.index}`}><label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={enabled} disabled={!enabled && activeTrackCount >= 8} onChange={(event) => updateTrack(asset.id, stream.index, { enabled: event.target.checked })} /><span>{asset.role === 'SCREEN' ? 'Tela' : 'Webcam'} · {asset.original_filename} · Track {stream.index} · {stream.metadata?.title ? `${stream.metadata.title} · ` : ''}{stream.codec_name ?? 'codec desconhecido'} · {stream.channels ?? '?'} canais · {stream.sample_rate ?? '?'} Hz · {stream.language ?? 'idioma não informado'}</span></label><label className="label mt-2">Ganho (dB)<input className="field" type="number" min={-60} max={12} step={.5} disabled={!enabled} value={track?.gain_db ?? 0} onChange={(event) => updateTrack(asset.id, stream.index, { gain_db: clamp(numeric(event.target.value, track?.gain_db ?? 0), -60, 12) })} /></label></div>})}{activeTrackCount >= 8 && <StatusMessage>Limite de 8 tracks ativas</StatusMessage>}{!config.audio.tracks.some((track) => track.enabled) && <StatusMessage>Render sem áudio</StatusMessage>}{missing.map((track) => <StatusMessage key={`${track.media_id}:${track.stream_index}`} tone="error">Track salva indisponível: mídia {track.media_id}, Track {track.stream_index}.</StatusMessage>)}</div>}</fieldset>
}
