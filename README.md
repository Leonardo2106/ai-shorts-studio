# AI Shorts Studio

> Bootstrap de arquitetura e desenvolvimento. **O produto ainda não está implementado.**

AI Shorts Studio é uma aplicação local-first planejada para transformar gravações longas de **tela + webcam** em YouTube Shorts de forma automática e semiautomática. O foco é reduzir o trabalho de reassistir horas de vídeo, sincronizar fontes, transcrever, encontrar bons momentos, montar o layout vertical, legendar e renderizar.

## Problema

Criadores que gravam gameplays, programação, aulas, tutoriais ou reações normalmente precisam localizar manualmente momentos interessantes e depois repetir um pipeline trabalhoso de corte, sync, legenda e composição vertical.

## Solução planejada

```text
Criar projeto
  ↓
Importar tela / webcam / áudio
  ↓
ffprobe + sincronização
  ↓
faster-whisper local
  ↓
candidatos locais
  ↓
IA externa opcional + visão local
  ↓
score explicável + ranking
  ↓
revisão do usuário
  ↓
editor 9:16
  ↓
preview
  ↓
render FFmpeg
```

A IA externa melhora a seleção, mas **não deve ser requisito** para abrir, editar ou renderizar um projeto.

## Status

### Implementado neste bootstrap

- configuração Codex project-scoped;
- 8 agentes especializados;
- regras globais em `AGENTS.md`;
- 3 roadmaps progressivos;
- arquitetura inicial e estratégia de desenvolvimento documentadas.

### Planejado para o produto

- projetos locais com UUID;
- import de screen/webcam e múltiplas tracks;
- ffprobe + sync manual;
- faster-whisper local;
- jobs/progresso;
- candidatos e scoring explicável;
- OpenAI/Gemini/Groq opcionais;
- OpenCV/MediaPipe para sinais observáveis;
- editor vertical 9:16;
- captions/banner;
- preview/render FFmpeg;
- suporte Windows nativo e Linux.

### Futuro

- sincronização automática por waveform se provar valor;
- PostgreSQL somente se houver necessidade real;
- análise multimodal externa somente opt-in;
- autenticação/contas/pagamentos somente se o produto deixar de ser uma ferramenta local gratuita.

## Arquitetura inicial

**Monólito modular local-first**:

```text
React/Vite UI
    ↓ HTTP local
FastAPI
 ├─ Projects + SQLite metadata
 ├─ Media / ffprobe / sync
 ├─ Transcription / faster-whisper
 ├─ Jobs local runner
 ├─ Candidate generation
 ├─ Optional AI provider adapters
 ├─ Vision local
 ├─ Scoring / ranking
 └─ Rendering / FFmpeg
        ↓
storage/projects/<uuid>/
```

Escolhas principais:
- SQLite para metadata do MVP;
- filesystem para vídeos, áudio, cache, previews e renders;
- JobRunner local substituível;
- sem Redis/Celery/microserviços no MVP;
- providers externos atrás de interface comum;
- segurança de paths e subprocessos desde o início.

## Stack

### Backend
- Python 3.12+
- FastAPI
- Pydantic v2 + pydantic-settings
- SQLAlchemy 2.x ou SQLModel, decisão final no Roadmap 00
- SQLite

### Media / IA local
- FFmpeg + ffprobe
- faster-whisper
- OpenCV
- MediaPipe quando validado
- NumPy
- Pillow

### Frontend
- React
- Vite
- TypeScript
- Tailwind CSS
- TanStack Query
- Zustand ou Context API para estado local do editor, conforme necessidade real

### Testes
- pytest / pytest-asyncio / httpx
- Vitest / React Testing Library
- Playwright somente quando E2E realmente agregar valor

### IA externa opcional
- OpenAI
- Google Gemini
- Groq

## Estrutura atual

```text
ai-shorts-studio/
├── .codex/
│   ├── agents/
│   │   ├── backend.toml
│   │   ├── code-reviewer.toml
│   │   ├── documentation.toml
│   │   ├── explorer.toml
│   │   ├── frontend.toml
│   │   ├── qa.toml
│   │   ├── security.toml
│   │   └── tech-lead.toml
│   ├── config.toml
│   └── README.md
├── .roadmap/
│   ├── 00-foundation-media-whisper-roadmap.md
│   ├── 01-ai-vision-scoring-editor-roadmap.md
│   └── 02-rendering-quality-release-roadmap.md
├── AGENTS.md
└── README.md
```

## Estrutura de código planejada

O Roadmap 00 deve criar a estrutura mínima necessária, provavelmente `backend/` e `frontend/`, sem gerar dezenas de arquivos vazios. Veja `AGENTS.md` para os limites modulares sugeridos.

## Agentes Codex

- **Tech Lead — Sol/High:** arquitetura, contratos, decomposição e integração.
- **Security — Sol/High:** review read-only de superfícies sensíveis.
- **Code Reviewer — Sol/High:** review read-only de correctness/arquitetura/regressões.
- **Backend — Sol/Medium:** implementação backend/media/IA local.
- **Frontend — Sol/Medium:** implementação da experiência web/editor.
- **QA — Terra/Medium:** testes e regressão.
- **Explorer — Terra/Low:** investigação read-only.
- **Documentation — Luna/Low:** documentação previsível e setup.

Veja `.codex/README.md` para o fluxo de delegação.

## Roadmap

1. **Roadmap 00 — Foundation, Media e Whisper**  
   Primeira vertical slice: projeto, storage, import, ffprobe, sync manual, job runner e transcrição local.

2. **Roadmap 01 — IA, Visão, Scoring e Editor**  
   Candidatos, providers opcionais, visão local, score explicável, ranking e editor 9:16.

3. **Roadmap 02 — Rendering, Qualidade e Release**  
   RenderPlan, FFmpeg, preview/final render, cache/performance, Windows/Linux, security e release.

O Codex só deve executar o roadmap explicitamente solicitado.

## Requisitos de desenvolvimento planejados

Ainda não há aplicação executável. Quando o Roadmap 00 começar, o ambiente deverá exigir aproximadamente:

- Git;
- Python 3.12+;
- Node.js LTS compatível com Vite;
- FFmpeg/ffprobe no PATH ou path configurado;
- gerenciador Python/Node definido no bootstrap real;
- GPU **não obrigatória**.

## Windows

Windows nativo é requisito do MVP. WSL pode ser usado por desenvolvedores, mas não substitui validação Windows.

Regras do projeto:
- usar `pathlib`;
- evitar scripts exclusivamente Bash;
- fornecer equivalentes PowerShell quando necessário;
- testar paths com espaços e Unicode;
- validar comportamento de subprocess/cancelamento no Windows real.

## Linux

Linux é plataforma de primeira classe para desenvolvimento e execução. Instruções específicas serão adicionadas quando o Roadmap 00 definir os comandos e dependências exatas.

## Docker

Docker/Docker Compose são opcionais. Não serão requisito para usar o MVP local. Podem ser adicionados se simplificarem desenvolvimento/reprodutibilidade sem esconder problemas de suporte nativo.

## Variáveis de ambiente planejadas

O arquivo `.env.example` só deve ser criado quando houver código que consuma essas opções. Possíveis grupos:

```text
APP_STORAGE_ROOT=
APP_DATABASE_URL=
FFMPEG_PATH=
FFPROBE_PATH=

OPENAI_API_KEY=
GEMINI_API_KEY=
GROQ_API_KEY=
```

