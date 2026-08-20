# AI Shorts Studio

Aplicação local-first para importar gravações de tela/webcam, sincronizá-las e transformar uma transcrição em candidatos editáveis. Roadmap 00 e o fluxo principal do Roadmap 01 estão implementados; o render final continua no Roadmap 02.

## O que está implementado

- FastAPI + SQLite para projetos e metadados; arquivos ficam em `storage/projects/<uuid>/`.
- Importação de mídia com `ffprobe`, incluindo zero, uma ou múltiplas faixas de áudio.
- Offset manual de webcam, preview no navegador quando o codec é compatível e JobRunner local.
- Transcrição opcional com `faster-whisper` (CPU suportada), presets e cache por mídia/parâmetros.
- Frontend React/Vite/TypeScript com testes e fluxo de projeto, mídia, sync e transcript.
- Geração local de candidatos, revisão/aceite/rejeição e ajuste manual de início/fim.
- Scoring explicável, ranking com deduplicação/overlap/diversidade e perfis/regras persistíveis.
- Providers opcionais OpenAI, Gemini e Groq para análise semântica. O uso externo exige opt-in textual; chaves ficam somente no backend.
- Visão local opcional com OpenCV, sampling temporal e análise mais densa por candidato; a disponibilidade é informada por `capabilities`.
- Editor lógico 9:16 persistível (`EditConfig`), com canvas 1080x1920, quatro presets, captions e banner.

Não há login, Redis/Celery, microserviços ou renderização final. MediaPipe pose/mouth ainda não está validado.

### Status

- **Implementado (Roadmap 00→01):** importação/probe, Whisper, candidatos locais, análise opcional por provider, visão OpenCV opcional, score/ranking, review e editor `EditConfig`.
- **Planejado/Futuro:** render FFmpeg final e release do Roadmap 02. Não confundir o preview leve do editor com renderização.
- **Limitações atuais:** providers reais e Windows nativo ainda não foram validados fisicamente; cancelamento de jobs HTTP pode aguardar o timeout do processo lento.

## Requisitos

- Python 3.12+ e Node.js `^20.19.0` ou `>=22.12.0` (requisito do Vite 8).
- FFmpeg, incluindo `ffprobe`, disponíveis no `PATH`.
- Git. GPU não é necessária.

### FFmpeg

Linux (Debian/Ubuntu):

```bash
sudo apt update && sudo apt install ffmpeg
ffmpeg -version
ffprobe -version
```

Windows nativo (PowerShell, usando `winget`):

```powershell
winget install Gyan.FFmpeg.Shared
ffmpeg -version
ffprobe -version
```

Feche e reabra o terminal após alterar o `PATH`. Alternativamente, defina `AI_SHORTS_FFMPEG_BINARY` e `AI_SHORTS_FFPROBE_BINARY` com o caminho do executável. WSL não substitui a validação do Windows nativo; esta ainda não foi feita fisicamente.

## Configuração e execução

Copie `.env.example` para `backend/.env` e ajuste caminhos/limites locais. O backend lê esse arquivo ao iniciar a partir de `backend/`. Esse arquivo local é ignorado pelo Git e pode conter chaves de provider; nunca coloque secrets no frontend nem em arquivos versionados.

Linux:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e './backend[dev,whisper]'
cp .env.example backend/.env
cd backend && uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Em outro terminal:

```bash
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
```

PowerShell (Windows nativo):

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

Abra `http://127.0.0.1:5173`. O bind de backend e frontend deve permanecer em loopback; use um único worker do backend porque o JobRunner e o SQLite são locais.

## Variáveis de ambiente

Todas usam o prefixo `AI_SHORTS_` e correspondem a campos consumidos por `backend/app/core/settings.py`. Além de caminhos, FFmpeg/Whisper e limites de upload, estão disponíveis `AI_SHORTS_VISION_TIMEOUT_SECONDS` e `AI_SHORTS_PROVIDER_MAX_RESPONSE_BYTES`. As chaves `AI_SHORTS_OPENAI_API_KEY`, `AI_SHORTS_GEMINI_API_KEY` e `AI_SHORTS_GROQ_API_KEY` são opcionais e só devem existir no `backend/.env` local. Modelos e parâmetros aceitos não são inventados na UI: consulte o endpoint `/api/v1/capabilities`.

## Fluxo

Criar projeto → importar screen/webcam → `ffprobe` detecta streams → ajustar offset → transcrever → gerar candidatos locais → (opcional) análise semântica/visão → score e ranking → revisar/ajustar candidato → salvar `EditConfig`, captions e banner. O modelo Whisper pode ser baixado no primeiro uso; isso requer rede e espaço em disco. Testes padrão não dependem de GPU nem de modelo grande.

## Validação local

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

No PowerShell, use os mesmos comandos após `Set-Location`; `pytest`, `ruff` e `mypy` devem ser invocados no ambiente virtual. `npm ci` requer `frontend/package-lock.json`.

## Troubleshooting

- `ffprobe was not found`: instale FFmpeg e confirme `ffprobe -version`, ou defina `AI_SHORTS_FFPROBE_BINARY`.
- `FFmpeg was not found` ao transcrever: confirme `ffmpeg -version` e `AI_SHORTS_FFMPEG_BINARY`.
- `faster-whisper` indisponível: instale `python -m pip install -e './backend[whisper]'`; o modelo pode baixar no primeiro uso.
- Codec sem preview no browser: a mídia pode estar válida; use um codec suportado pelo navegador ou aguarde uma etapa futura de preview/transcode.
- Falhas no Windows podem envolver caminhos com espaços, arquivos reparse/symlink e diferenças de multiprocessing; o suporte nativo é requisito, mas ainda precisa de validação física.
- Verifique espaço livre: uploads, SQLite, cache e modelos ficam no filesystem local.

## Arquitetura resumida

React/Vite → HTTP local → FastAPI → serviços de projetos, mídia, jobs, transcrição, candidatos, IA, visão, scoring e editor → SQLite (metadata) + `storage/projects/<uuid>/` (mídia/cache/transcripts). FFmpeg/ffprobe, faster-whisper e OpenCV são executados localmente; providers recebem somente chunks/candidatos após opt-in, nunca vídeo/webcam automaticamente.

Consulte `.roadmap/00-foundation-media-whisper-roadmap.md` e `.roadmap/01-ai-vision-scoring-editor-roadmap.md` para escopo e critérios. O render final do `.roadmap/02-rendering-quality-release-roadmap.md` não está implementado.
