export type MediaRole = 'SCREEN' | 'WEBCAM'
export type JobStatus = 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELLED'
export type JobKind = 'TRANSCRIPTION' | 'CANDIDATE_GENERATION' | 'SEMANTIC_ANALYSIS' | 'VISION_ANALYSIS' | 'RENDER_PREVIEW' | 'RENDER_FINAL'
export type CapabilityStatus = 'AVAILABLE' | 'MISSING' | 'ERROR'
export type TranscriptionPreset = 'ECONOMY' | 'BALANCED' | 'QUALITY' | 'MAXIMUM_QUALITY'

export interface FrameRate { numerator: number; denominator: number; value: number }
export interface VideoStream { index: number; codec_name: string | null; width: number | null; height: number | null; fps: number | null; bitrate: number | null; duration_ms?: number | null; metadata?: Record<string, string> }
export interface AudioStream { index: number; codec_name: string | null; sample_rate: number | null; channels: number | null; channel_layout: string | null; bitrate?: number | null; duration_ms?: number | null; language: string | null; metadata?: Record<string, string> }
export interface MediaProbe { duration_ms: number; format_name: string | null; bitrate: number | null; video_streams: VideoStream[]; audio_streams: AudioStream[]; metadata?: Record<string, string> }
export interface MediaAsset { id: string; role: MediaRole; original_filename: string; size_bytes: number; sha256: string; content_url: string; probe: MediaProbe }
export interface Project { id: string; name: string; stage: string; webcam_offset_ms: number; media: MediaAsset[]; created_at: string; updated_at: string }
export interface Capability { name: string; status?: CapabilityStatus; available?: boolean; version?: string | null; detail?: string | null }
export interface Capabilities { ffmpeg?: Capability; ffprobe?: Capability; faster_whisper?: Capability; transcription_presets?: TranscriptionPreset[]; advanced_options?: string[]; ai_providers?: AiProviderCapability[]; vision?: { available: boolean; analyzer_version: number }; editor?: { canvas: { width: number; height: number }; schema_version: number }; [key: string]: unknown }
export interface ApiErrorBody { error?: { code?: string; message?: string; details?: unknown }; detail?: string }
export interface JobError { code?: string; message: string; details?: unknown }
export interface RenderArtifact { id: string; project_id: string; candidate_id: string; edit_config_id: string; job_id: string; kind: 'PREVIEW' | 'FINAL'; quality: RenderQuality; dependency_fingerprint: string; content_url: string; size_bytes: number; duration_ms: number; width: number; height: number; has_audio: boolean; created_at: string }
export interface JobResult {
  transcript_id?: string
  cached?: boolean
  cache_hit?: boolean
  cached_chunks?: number
  candidate_ids?: string[]
  chunks?: number
  analyses?: unknown[]
  artifact_id?: string
}
export interface Job { id: string; project_id: string; kind: string; status: JobStatus; progress: number; error?: JobError | null; result?: JobResult | null; cancellation_requested?: boolean; created_at?: string; started_at?: string | null; finished_at?: string | null }
export interface ProjectJobFilters { kind?: JobKind; status?: JobStatus; limit?: number }
export interface TranscriptWord { start_ms: number; end_ms: number; text: string; probability?: number | null }
export interface TranscriptSegment { start_ms: number; end_ms: number; text: string; words?: TranscriptWord[] | null }
export interface Transcript { id: string; project_id: string; media_id: string; source: string; audio_stream_index: number; language: string | null; duration_ms: number; engine: string; model: string; segments: TranscriptSegment[] }
export interface StartTranscription { media_id: string; audio_stream_index: number; preset: TranscriptionPreset; language: string | null; word_timestamps: boolean }

export interface TranscriptSummary { id: string; project_id: string; media_id: string; language: string | null; duration_ms: number; created_at: string }
export interface ScoreContribution { rule?: string; key?: string; label?: string; value?: number; weight?: number; contribution: number; enabled?: boolean; [key: string]: unknown }
export interface Candidate {
  id: string; project_id: string; transcript_id: string; schema_version: number; start_ms: number; end_ms: number; title: string
  status: 'PENDING' | 'ACCEPTED' | 'REJECTED'; text: string; origin: string; local_features: Record<string, unknown>
  reasons: string[]; context: Record<string, unknown>; signals: Record<string, unknown>; score: number | null
  score_breakdown: ScoreContribution[] | null; created_at: string; updated_at: string
}
export interface CandidateGenerationInput { transcript_id: string; min_duration_ms: number; ideal_min_ms: number; ideal_max_ms: number; max_duration_ms: number; pre_roll_ms: number; post_roll_ms: number; top_n: number }
export interface CandidateUpdate { start_ms?: number; end_ms?: number; status?: 'PENDING' | 'ACCEPTED' | 'REJECTED' }
export interface ScoreRule { key: string; label: string; weight: number; enabled: boolean }
export interface ScoreProfile { id: string; project_id: string | null; name: string; rules: ScoreRule[]; is_default: boolean; created_at: string; updated_at: string }
export interface ProviderParameter { name: string; label?: string; type: 'number' | 'integer' | 'string' | 'boolean' | 'select'; minimum?: number; maximum?: number; step?: number; default?: string | number | boolean | null; options?: Array<string | { value: string; label: string }> }
export interface AiProviderCapability { provider: 'OPENAI' | 'GEMINI' | 'GROQ'; configured: boolean; models: string[]; parameters: string[] }
export interface AnalysisEstimate { chunks: number; estimated_input_tokens: number; candidates: number; planned_provider_calls: number; max_provider_calls: number }
export interface AiAnalysisInput { provider: string; model: string; candidate_ids: string[]; opt_in_external_processing: boolean; max_output_tokens: number; temperature?: number; top_p?: number; reasoning_effort?: string; timeout_seconds: number; retries: number; fallback_provider?: string; chunk_char_limit: number }
export interface VisionAnalysisInput { media_id: string; candidate_ids: string[]; sample_interval_ms: number; max_samples: number; max_dimension: number }
export interface CaptionCueWord { start_ms: number; end_ms: number; text: string }
export interface CaptionCue { start_ms: number; end_ms: number; text: string; words: CaptionCueWord[] | null }
export interface CaptionCues { items: CaptionCue[]; timing_source: 'WORDS' | 'WORDS_AND_SEGMENTS' | 'SEGMENTS' }

export type ElementKind = 'SCREEN' | 'WEBCAM' | 'CAPTIONS' | 'BANNER' | 'BACKGROUND' | 'TEXT' | 'IMAGE'
export type MediaFit = 'COVER' | 'CONTAIN' | 'CROP'
export type LayoutPreset = 'WEBCAM_TOP_SCREEN_BOTTOM' | 'WEBCAM_TOP_SCREEN_MIDDLE_BANNER_BOTTOM' | 'SCREEN_FULLSCREEN_WEBCAM_OVERLAY' | 'WEBCAM_FULLSCREEN_SCREEN_PIP'
export interface EditorElement { id: string; kind: ElementKind; x: number; y: number; width: number; height: number; z_index: number; visible: boolean; fit: MediaFit; opacity: number; border_width: number; border_color: string; radius: number; padding: number; zoom: number; focal_x: number; focal_y: number }
export interface CaptionStyle { enabled: boolean; font_family: string; font_size: number; color: string; weight: number; uppercase: boolean; outline_width: number; outline_color: string; shadow: boolean; italic: boolean; box_color: string | null; max_width: number; words_per_line: number; words_per_block: number; active_word_color: string | null; gap_tolerance_ms: number; min_display_ms: number; hold_ms: number }
export interface BannerStyle { enabled: boolean; text: string; image_relative_path: string | null; background_color: string; opacity: number; start_ms: number; end_ms: number | null }
export interface AudioTrackConfig { media_id: string; stream_index: number; enabled: boolean; gain_db: number }
export interface AudioConfig { mode: 'TRANSCRIPT_DEFAULT' | 'CUSTOM'; tracks: AudioTrackConfig[] }
export interface EditConfig { schema_version: 2; canvas_width: 1080; canvas_height: 1920; preset: LayoutPreset; elements: EditorElement[]; captions: CaptionStyle; banner: BannerStyle; audio: AudioConfig; background_color: string }
export interface EditConfigResponse { id: string; project_id: string; candidate_id: string; schema_version: number; config: EditConfig; created_at: string; updated_at: string }

export type RenderKind = 'PREVIEW' | 'FINAL'
export type RenderQuality = 'FAST' | 'BALANCED' | 'HIGH'
export interface StartRenderInput { quality: RenderQuality }
export interface RenderPlanSummary {
  schema_version: 1; project_id: string; candidate_id: string; edit_config_id: string; kind: RenderKind; quality: RenderQuality
  clip: { timeline_start_ms: number; timeline_end_ms: number; duration_ms: number }
  canvas: { logical_width: number; logical_height: number; output_width: number; output_height: number; fps: number }
  layers: Array<{ id: string; kind: string; z_index: number }>
  captions: { enabled: boolean }
  banner: { enabled: boolean }
  audio: { mode: string }
  edit_config_fingerprint: string
  dependency_fingerprint: string
  cacheable: boolean
}
