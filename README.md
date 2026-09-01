# AI Shorts Studio

Aplicação local-first para transformar gravações de tela/webcam em Shorts editáveis. O MVP usa FastAPI, React/Vite, SQLite, filesystem e FFmpeg; não exige conta, cloud ou API paga.

## Status

- ✅ **Implementado:** projetos, importação e `ffprobe`; sync manual; transcrição opcional com `faster-whisper`; candidatos, análise opcional, visão OpenCV, scoring/ranking; editor `EditConfig` 9:16; `RenderPlan`; preview/render FFmpeg com jobs, progresso, cancelamento, validação e cache.
- 🧪 **Experimental/opcional:** providers OpenAI, Gemini e Groq; faster-whisper e visão local dependem das instalações/modelos disponíveis. Preview/render dependem de mídia compatível e FFmpeg local.
- 📋 **Planejado:** validação física do fluxo completo em Windows nativo e uma futura mistura de múltiplas fontes de áudio.
- 🔮 **Futuro:** autenticação, SaaS, infraestrutura distribuída e recursos de edição/encoding avançados.

Limitações conhecidas: o plano de áudio usa a fonte/stream selecionada pela transcrição e não é um mixer; `radius` customizado é rejeitado pelo renderer; a política de `z-index` é validada para manter captions/banner acima dos vídeos; banner é uma imagem validada; active-word highlighting segue os dados normalizados disponíveis. Windows nativo ainda não foi validado fisicamente nesta máquina.

## Requisitos

- Python 3.12+;
- Node.js `^20.19.0` ou `>=22.12.0`;
- FFmpeg e `ffprobe` no `PATH` (ou caminhos configurados no backend);
- Git. GPU não é necessária.

`faster-whisper` é opcional e instala-se pelo extra `whisper`. Providers são opt-in e as chaves ficam somente no backend.

### FFmpeg

Linux (Debian/Ubuntu):

```bash
sudo apt update && sudo apt install ffmpeg
ffmpeg -version
ffprobe -version
```

Windows nativo (PowerShell):

```powershell
winget install Gyan.FFmpeg.Shared
ffmpeg -version
ffprobe -version
```

Se os executáveis não estiverem no `PATH`, configure `AI_SHORTS_FFMPEG_BINARY` e `AI_SHORTS_FFPROBE_BINARY` em `backend/.env`. WSL não substitui a validação Windows nativa.

## Setup

Copie `.env.example` para `backend/.env`. O arquivo é ignorado pelo Git; não coloque secrets no frontend nem em arquivos versionados.

Linux:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e './backend[dev,whisper]'
cp .env.example backend/.env
cd backend && python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Em outro terminal:

```bash
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
```

Windows nativo (PowerShell):

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e '.\backend[dev,whisper]'
Copy-Item .env.example backend/.env
Push-Location backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Em outro PowerShell:

```powershell
Set-Location frontend
npm ci
npm run dev -- --host 127.0.0.1
```

Abra `http://127.0.0.1:5173`. Use um worker: o JobRunner e o SQLite são locais.

## Configuração

Todas as variáveis usam o prefixo `AI_SHORTS_` e são campos de `backend/app/core/settings.py`. O `.env.example` contém defaults sem secrets, incluindo limites de upload, tempo/limites do FFmpeg, reserva de disco, workers, cache de previews, `AI_SHORTS_MAX_RENDER_DURATION_MS`, `AI_SHORTS_MAX_RENDER_OUTPUT_BYTES`, `AI_SHORTS_MAX_BANNER_ASSET_BYTES`, `AI_SHORTS_RENDER_CANCEL_GRACE_SECONDS`, `AI_SHORTS_RENDER_STDERR_MAX_BYTES`, `AI_SHORTS_PROVIDER_JOB_TIMEOUT_SECONDS`, `AI_SHORTS_PROVIDER_MAX_CHUNKS_PER_JOB` e `AI_SHORTS_PROVIDER_MAX_CALLS_PER_JOB`.

As chaves opcionais são `AI_SHORTS_OPENAI_API_KEY`, `AI_SHORTS_GEMINI_API_KEY` e `AI_SHORTS_GROQ_API_KEY`. O Groq atualmente expõe `openai/gpt-oss-120b`; Gemini usa resposta JSON estruturada com o mesmo schema semântico interno. Chaves são lidas no backend e exigem reinício; espaços em branco são removidos e valor vazio não configura provider. Consulte `/api/v1/capabilities` para modelos e parâmetros realmente suportados.

Análise externa exige opt-in explícito, envia contexto textual em chunks e usa cache por entrada. Há preflight de provider e limites globais de tempo, chunks e chamadas; provider de fallback só é usado se estiver configurado. Cache hits não consomem chamadas externas.

## Fluxo de uso

Criar projeto → importar screen/webcam → `ffprobe` detectar streams → ajustar sync → transcrever → gerar/analisar candidatos → score/ranking → revisar no editor → salvar `EditConfig` → solicitar preview → aprovar → renderizar Short.

O editor oferece preview imediato no browser. **FFmpeg Preview** gera um MP4 menor e cacheável, reproduzindo fielmente layout, corte, captions, banner e áudio selecionado. **Final Render** usa a qualidade escolhida (Rápida, Balanceada ou Alta), valida o MP4 com `ffprobe` e salva o resultado no projeto.

O editor suporta zoom e ponto focal para `COVER`/`CROP`; `CONTAIN` preserva a mídia inteira e mantém zoom/foco neutros. “Preencher sem bordas” aplica preenchimento sem bordas. Captions podem usar presets/custom, fonte portátil, peso, italic, outline, shadow, cor e background desligado (`box_color` nulo); `gap_tolerance_ms`, `min_display_ms` e `hold_ms` controlam pausas entre falas.

Jobs exibem `PENDING`, `RUNNING`, `COMPLETED`, `FAILED` ou `CANCELLED`, com progresso estruturado do FFmpeg. Cancelamento encerra o processo, remove parciais e preserva originais/projeto.

## Arquivos, cache e cleanup

Cada projeto usa `storage/projects/<uuid>/`, normalmente com:

```text
screen.mp4 / webcam.mp4  originais importados atualmente
transcripts/             documentos de transcrição
cache/                   áudio extraído e outros derivados locais
assets/                  imagens de banner referenciadas pelo editor
previews/                previews FFmpeg cacheados
renders/                 renders finais
temp/                    ASS e intermediários transitórios
```

`ffprobe`, projetos, candidatos, análises, jobs e artifacts são metadata no SQLite; não são BLOBs nem arquivos duplicados nessa árvore.

Mudanças de legenda/layout invalidam preview/render sem rerodar Whisper. Mudanças de score invalidam ranking, não mídia/transcript. Outputs existentes não são sobrescritos silenciosamente; parciais e temporários órfãos podem ser limpos pelo renderer. O original nunca é removido por cleanup normal.

## Validação

```bash
cd backend
python -m pytest
python -m ruff check .
python -m mypy app
cd ../frontend
npm test
npm run lint
npm run typecheck
npm run build
```

No PowerShell, execute os mesmos comandos após `Set-Location`; use o Python do ambiente virtual.

## Troubleshooting

- `ffprobe was not found`/`FFmpeg was not found`: instale FFmpeg, confirme `ffprobe -version`/`ffmpeg -version` e revise os caminhos no `.env`.
- Falha ao abrir fonte ou legenda ASS: confirme que os arquivos estão dentro do projeto, que a mídia tem streams válidos e que a fonte referenciada está instalada; o ASS é gerado pelo backend, não aceito como texto arbitrário.
- Erro de rendering: expanda os detalhes técnicos retornados pela API; decodificação inválida normalmente indica codec/fonte incompatível.
- Caminhos com espaços ou Unicode são suportados por `pathlib`/argumentos de processo; não use comandos shell concatenados.
- Falta de espaço: uploads, modelos Whisper, cache, previews e renders ficam no disco local; libere espaço ou ajuste limites com cuidado.
- `faster-whisper` indisponível: instale `python -m pip install -e './backend[whisper]'`; o primeiro uso pode baixar o modelo.

## Arquitetura

`EditConfig → RenderPlan → FilterGraphBuilder → FFmpegCommandBuilder → Renderer`. O plano normaliza canvas, clip, inputs, offsets, layers, captions/banner, áudio e output antes da execução. Builders aceitam somente estruturas internas validadas; o renderer usa listas de argumentos, `shell=False`, progresso, cancelamento, validação e cleanup.

React/Vite fala com FastAPI; SQLite guarda metadata e `storage/` guarda mídia/artefatos. Providers externos recebem somente contexto textual após opt-in; vídeo/webcam não são enviados automaticamente.

## Documentação de escopo

Roadmaps 00 e 01 registram decisões e critérios anteriores. O escopo ativo de rendering é `.roadmap/02-rendering-quality-release-roadmap.md`. Não há licença versionada no repositório; nenhuma foi inventada.
