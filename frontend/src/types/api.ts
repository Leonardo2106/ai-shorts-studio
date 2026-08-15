export type MediaRole = 'SCREEN' | 'WEBCAM'
export type JobStatus = 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELLED'
export type CapabilityStatus = 'AVAILABLE' | 'MISSING' | 'ERROR'
export type TranscriptionPreset = 'ECONOMY' | 'BALANCED' | 'QUALITY' | 'MAXIMUM_QUALITY'

export interface FrameRate { numerator: number; denominator: number; value: number }
export interface VideoStream { index: number; codec_name: string | null; width: number | null; height: number | null; fps: number | null; bitrate: number | null; duration_ms?: number | null; metadata?: Record<string, string> }
export interface AudioStream { index: number; codec_name: string | null; sample_rate: number | null; channels: number | null; channel_layout: string | null; bitrate?: number | null; duration_ms?: number | null; language: string | null; metadata?: Record<string, string> }
export interface MediaProbe { duration_ms: number; format_name: string | null; bitrate: number | null; video_streams: VideoStream[]; audio_streams: AudioStream[]; metadata?: Record<string, string> }
export interface MediaAsset { id: string; role: MediaRole; original_filename: string; size_bytes: number; sha256: string; content_url: string; probe: MediaProbe }
export interface Project { id: string; name: string; stage: string; webcam_offset_ms: number; media: MediaAsset[]; created_at: string; updated_at: string }
export interface Capability { name: string; status?: CapabilityStatus; available?: boolean; version?: string | null; detail?: string | null }
export interface Capabilities { ffmpeg?: Capability; ffprobe?: Capability; faster_whisper?: Capability; transcription_presets?: TranscriptionPreset[]; advanced_options?: string[]; [key: string]: unknown }
export interface ApiErrorBody { error?: { code?: string; message?: string; details?: unknown }; detail?: string }
export interface JobError { code?: string; message: string; details?: unknown }
export interface Job { id: string; project_id: string; kind: string; status: JobStatus; progress: number; error?: JobError | null; result?: { transcript_id?: string; cached?: boolean } | null; cancellation_requested?: boolean }
export interface TranscriptWord { start_ms: number; end_ms: number; text: string; probability?: number | null }
export interface TranscriptSegment { start_ms: number; end_ms: number; text: string; words?: TranscriptWord[] | null }
export interface Transcript { id: string; project_id: string; media_id: string; source: string; audio_stream_index: number; language: string | null; duration_ms: number; engine: string; model: string; segments: TranscriptSegment[] }
export interface StartTranscription { media_id: string; audio_stream_index: number; preset: TranscriptionPreset; language: string | null; word_timestamps: boolean }
