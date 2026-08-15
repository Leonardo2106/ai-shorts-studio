# AI Shorts Studio

Aplicação local-first para importar gravações de tela/webcam, sincronizá-las e gerar uma transcrição local com timestamps. A entrega atual corresponde ao Roadmap 00 (M00-1 a M00-7). IA avançada, visão, scoring, editor 9:16 e render final pertencem ao Roadmap 01/02 e ainda não estão implementados.

## O que está implementado

- FastAPI + SQLite para projetos e metadados; arquivos ficam em `storage/projects/<uuid>/`.
- Importação de mídia com `ffprobe`, incluindo zero, uma ou múltiplas faixas de áudio.
- Offset manual de webcam, preview no navegador quando o codec é compatível e JobRunner local.
- Transcrição opcional com `faster-whisper` (CPU suportada), presets e cache por mídia/parâmetros.
- Frontend React/Vite/TypeScript com testes e fluxo de projeto, mídia, sync e transcript.

Não há login, APIs pagas, Redis/Celery, microserviços ou renderização final.

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

Copie `.env.example` para `backend/.env` e ajuste apenas caminhos locais. O backend lê esse arquivo ao iniciar a partir de `backend/`; não coloque chaves ou segredos nele.

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

Todas usam o prefixo `AI_SHORTS_` e correspondem a campos consumidos por `backend/app/core/settings.py`. As mais comuns são `AI_SHORTS_STORAGE_ROOT`, `AI_SHORTS_DATABASE_PATH`, `AI_SHORTS_FFMPEG_BINARY` e `AI_SHORTS_FFPROBE_BINARY`. Timeouts, limites de upload/duração, `AI_SHORTS_JOB_WORKERS` (fixo em `1`), `AI_SHORTS_MAX_ACTIVE_JOBS`, `AI_SHORTS_CORS_ORIGINS` e `AI_SHORTS_ALLOWED_HOSTS` também podem ser definidos; consulte o arquivo de settings antes de adicionar uma variável. Providers OpenAI/Gemini/Groq ainda não são consumidos e não devem ser configurados.

## Fluxo

Criar projeto → importar screen/webcam → `ffprobe` detecta streams e propriedades → ajustar offset manual → iniciar job de transcrição → consultar progresso → abrir transcript com timestamps. O modelo Whisper pode ser baixado no primeiro uso; isso requer rede e espaço em disco. Testes padrão não dependem de GPU nem de modelo grande.

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

React/Vite → HTTP local → FastAPI → serviços de projetos, mídia, jobs e transcrição → SQLite (metadata) + `storage/projects/<uuid>/` (mídia/cache/transcripts). FFmpeg/ffprobe e faster-whisper são executados localmente; nenhum vídeo é enviado a um provider externo.

Consulte `.roadmap/00-foundation-media-whisper-roadmap.md` para o escopo e os critérios desta etapa. O próximo roadmap não deve ser tratado como concluído.
