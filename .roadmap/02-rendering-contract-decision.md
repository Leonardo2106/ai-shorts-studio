# Decisão R02 — contrato de rendering

`EditConfig` permanece a intenção declarativa persistida pelo editor. A camada de serviço resolve Project, Candidate,
Media, Transcript e assets por `ProjectStorage` e entrega um `ResolvedRenderContext` confiável ao
`RenderPlanBuilder`. O plano normaliza tempo, canvas, inputs, offsets, layers, captions, banner, áudio e output sem
conter comandos FFmpeg.

`EditConfig` v2 adiciona `AudioConfig`. `TRANSCRIPT_DEFAULT` preserva o comportamento local-first selecionando
explicitamente o stream usado pela transcrição; ausência de transcript produz output silencioso. `CUSTOM` referencia
tracks somente por `(media_id, stream_index)`, com ganho entre -60 dB e +12 dB, até 32 configurações e no máximo oito
habilitadas. Tracks desabilitadas não são validadas e não bloqueiam quando a mídia/stream deixou de existir. Payloads
v1 migram de forma lazy para v2 em `TRANSCRIPT_DEFAULT` e são persistidos no primeiro GET/render.

O `RenderPlan` normaliza áudio em `SILENT`, `SINGLE_TRACK` ou `MIXED_TRACKS`, com uma lista de fontes e seus trims,
offsets e ganhos. O filtergraph converte cada fonte para 48 kHz estéreo, aplica ganho e limiter; múltiplas fontes usam
`amix` com normalização antes do limiter. O renderer não infere significados como microfone ou desktop a partir de
nomes ou da ordem das tracks. Captions dependem do transcript, mas permanecem independentes da seleção de áudio.

Preview é cacheável por hash canônico de EditConfig, clip, offsets, hashes/streams de mídia, transcript/cues, asset e
perfil de output. Mudanças visuais invalidam o preview, mas não alteram as identidades de transcript, visão ou IA.
