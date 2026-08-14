# AGENTS.md — AI Shorts Studio

## 1. Missão do repositório

Construir progressivamente uma aplicação **local-first** que transforma gravações longas de tela + webcam em Shorts editáveis. O MVP deve continuar útil sem APIs pagas, funcionar nativamente em **Windows e Linux** e evitar overengineering.

A fonte de verdade de execução é o roadmap explicitamente solicitado em `.roadmap/`. **Nunca avance automaticamente para o roadmap seguinte.**

## 2. Arquitetura obrigatória do MVP

- Monólito modular local-first.
- Backend: Python 3.12+ / FastAPI / Pydantic v2.
- Frontend: React / Vite / TypeScript / Tailwind.
- Metadata: SQLite.
- Mídia, previews, cache e renders: filesystem em `storage/projects/<uuid>/`.
- Jobs pesados: JobRunner local substituível.
- IA externa: adapters opcionais para OpenAI, Gemini e Groq.
- Mídia local: ffprobe, FFmpeg, faster-whisper, OpenCV e MediaPipe somente quando validado.
- Sem login, JWT, OAuth, Redis, Celery, Kubernetes ou microserviços no MVP salvo mudança arquitetural explicitamente aprovada.

## 3. Organização planejada

Quando o código começar, prefira limites semelhantes a:

```text
backend/
  app/
    api/            # rotas/HTTP; sem regra pesada
    core/           # settings, errors, logging
    db/             # models, sessions, repositories
    projects/       # project/storage lifecycle
    media/          # probe/import/sync
    transcription/  # Whisper + transcript schemas
    jobs/           # job model/runner/progress
    candidates/     # geração local de candidatos
    ai/             # interfaces + provider adapters
    vision/         # sinais observáveis locais
    scoring/        # regras, perfis, breakdown, ranking
    editor/         # EditConfig/RenderPlan schemas
    rendering/      # filtergraph/ffmpeg/renderer
frontend/
  src/
    api/
    components/
    features/
    hooks/
    state/
    types/
```

A estrutura pode evoluir, mas os módulos não devem criar dependências circulares nem acoplar regra de negócio a SDKs externos.

## 4. Backend

- Use typing explícito nas bordas públicas e modelos internos relevantes.
- Valide requests/responses com Pydantic; erros devem ser previsíveis e úteis.
- Handlers HTTP devem orquestrar serviços, não executar CPU/GPU/FFmpeg/Whisper de longa duração diretamente.
- Use `pathlib`; nunca concatene paths não confiáveis manualmente.
- Descubra streams com ffprobe; não assuma uma única track de áudio.
- Use subprocess com lista de argumentos e `shell=False`.
- Nunca aceite comando shell ou filtergraph arbitrário vindo do frontend.
- Timestamps sugeridos por IA devem ser revalidados no backend contra duração/limites reais.
- Cache deve ser invalidado por dependência: mudar estilo de legenda não pode rerodar Whisper.

## 5. Frontend

- TypeScript estrito sempre que a configuração do projeto permitir.
- TanStack Query para estado remoto; estado do editor fica local e separado de cache do servidor.
- Não armazene API keys no frontend. A UI pode receber apenas capacidade/status de provider.
- Parâmetros Advanced devem aparecer somente quando suportados pelo backend/engine/provider.
- Não renderize via FFmpeg a cada `mousemove`; mantenha preview leve no browser e gere previews FFmpeg sob demanda.
- Diferencie claramente `loading`, progresso, sucesso, erro, cancelamento e estado vazio.
- Não use dados mockados para fazer funcionalidades incompletas parecerem prontas.

## 6. Banco e storage

- SQLite guarda metadata, não BLOBs de vídeo.
- Todo projeto usa UUID e diretório dedicado em `storage/projects/<uuid>/`.
- Antes de ler/escrever/remover arquivo, resolva o path e confirme que permanece dentro do root permitido.
- Migrações só entram quando a evolução real do schema justificar; se Alembic for introduzido, migrations tornam-se a fonte de verdade do schema.
- Queries devem preservar integridade e evitar N+1 desnecessário quando a escala do fluxo justificar.

## 7. Segurança e privacidade

- Secrets nunca entram no código/repositório; usar variáveis de ambiente e futuro `.env.example` sem valores reais.
- No MVP, **não adicionar autenticação/autorização**: a aplicação é local e sem contas.
- Não confiar em filename/extensão para validar mídia; inspecionar conteúdo/streams.
- Considerar path traversal, symlinks, arquivos enormes, vídeos malformados, timeouts, espaço em disco, concorrência e exaustão de CPU/GPU/RAM.
- Logs não devem conter secrets nem transcript completo por padrão.
- APIs externas são opt-in: enviar somente o mínimo necessário de transcript/context.
- Não enviar vídeo inteiro nem webcam para API multimodal automaticamente.

## 8. Windows + Linux

- Windows nativo é requisito do MVP; WSL não substitui validação Windows.
- Use `pathlib`, `subprocess` portátil e comandos independentes de shell quando possível.
- Não hardcode `/tmp`, `/usr/bin`, `C:\\...` ou separadores de path.
- Scripts/instruções devem fornecer PowerShell e Linux quando houver diferença relevante.
- Toda dependência externa (FFmpeg, Node, etc.) precisa de detecção/erro claro e documentação de instalação.

## 9. Testes e validação

- Backend: pytest, pytest-asyncio, httpx.
- Frontend: Vitest + React Testing Library.
- Playwright somente para E2E que realmente agregue valor.
- Fixtures de mídia devem ser pequenas e determinísticas.
- Testes padrão não podem exigir API paga ou GPU.
- Preferir testes focados primeiro; ampliar a suíte depois que o caminho crítico estiver verde.

> Um agente nunca deve considerar uma tarefa concluída apenas porque escreveu o código. Deve validar a implementação com testes, lint, type checking, build ou outra checagem adequada.

## 10. Git

- Faça mudanças pequenas e coesas dentro do roadmap ativo.
- Evite commits que misturem refactor amplo com feature.
- Antes de alterar arquivos compartilhados por outro agente, confirme que não há conflito de workstream.
- Mensagens de commit devem explicar a intenção, por exemplo `feat(media): add ffprobe inspection service`.
- Security e Code Reviewer são read-only por padrão; QA pode escrever testes; Documentation escreve docs.
- Não force-push, reescreva histórico ou descarte trabalho de outro agente sem ordem explícita.

## 11. Coordenação entre agentes

1. Tech Lead lê este arquivo, `.codex/README.md` e o roadmap ativo.
2. Tech Lead define contratos e divide o trabalho.
3. Explorer é usado somente quando existe uma incerteza bloqueadora.
4. Backend e Frontend podem rodar em paralelo **depois** que os contratos estejam claros e em arquivos distintos.
5. QA acompanha critérios de aceite e escreve/roda validações.
6. Security revisa superfícies sensíveis.
7. Code Reviewer faz a revisão final de correctness/arquitetura/regressão.
8. Documentation atualiza somente o que realmente mudou.

Evite mais de três write-heavy workstreams simultâneos e nunca coloque dois agentes editando o mesmo arquivo sem necessidade.

## 12. Economia de contexto/tokens

- Leia primeiro o roadmap ativo e arquivos diretamente relacionados.
- Use `rg`, busca por símbolos e leitura de trechos; não faça varredura narrativa do repositório inteiro.
- Reutilize contratos e decisões existentes; não redesenhe a arquitetura a cada tarefa.
- Não peça a vários agentes a mesma análise, exceto revisão deliberada.
- Resumos entre agentes devem conter decisões, interfaces, arquivos e blockers — não transcrições longas.
- Pare no critério de aceite do roadmap solicitado.
