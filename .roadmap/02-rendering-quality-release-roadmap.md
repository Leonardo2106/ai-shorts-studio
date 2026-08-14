# Roadmap 02 — Rendering, Qualidade e Release

## Objetivo

Transformar `EditConfig` aprovado em previews e renders finais reproduzíveis, seguros e portáveis, e preparar o AI Shorts Studio para um primeiro release público gratuito.

**Pré-condição:** Roadmaps 00 e 01 concluídos.

## Milestones e tarefas

### M02-1 — Contrato de rendering

**Agentes:** Tech Lead + Backend + QA.

Implementar a cadeia:

```text
EditConfig
  ↓
RenderPlan
  ↓
FilterGraphBuilder
  ↓
FFmpegCommandBuilder
  ↓
Renderer
```

- `EditConfig` representa intenção do usuário;
- `RenderPlan` resolve dimensões, crops, tempos, streams e assets;
- `FilterGraphBuilder` gera somente operações suportadas e validadas;
- `FFmpegCommandBuilder` produz array de argumentos, não raw shell;
- `Renderer` executa, acompanha progresso, cancela e registra resultado.

### M02-2 — Composição de vídeo/áudio

**Agentes:** Backend + QA; Explorer para detalhes FFmpeg bloqueadores.

- layouts do editor devem corresponder ao render;
- aplicar sync offsets e trims validados;
- definir política de áudio: mic/desktop/mixed quando disponíveis, volume/mix simples e previsível;
- preservar A/V sync;
- tratar inputs com codecs/streams variados sem assumir uma track;
- usar codecs/defaults portáveis com fallback/documentação.

### M02-3 — Captions avançadas

**Agentes:** Backend + Frontend + QA.

- avaliar ASS como mecanismo preferencial para estilos avançados;
- mapear word timestamps para blocos/highlight com tolerância temporal clara;
- fontes devem ser resolvidas por configuração/asset do projeto, sem paths de OS hardcoded;
- fallback simples quando estilo avançado não estiver disponível.

### M02-4 — Preview pipeline

**Agentes:** Backend + Frontend + QA.

- preview leve continua no browser;
- preview FFmpeg sob demanda em baixa resolução/curta duração;
- cache de preview baseado em `EditConfig` + inputs relevantes;
- cancelar/invalidar previews obsoletos;
- não rerodar Whisper/vision/IA por mudança visual.

### M02-5 — Render final + jobs

**Agentes:** Backend + QA; Security revisa.

- render final em job com progresso;
- cancelamento e cleanup de temporários;
- estado final `COMPLETED/FAILED/CANCELLED` consistente;
- output nomeado/armazenado dentro do projeto;
- retry manual seguro e idempotência razoável;
- mensagens de erro incluem contexto técnico sem vazar secrets.

### M02-6 — Cache, performance e limites

**Agentes:** Backend + QA + Security.

- revisar chaves de cache de ffprobe/transcript/vision/candidates/AI/previews;
- invalidar somente dependências afetadas;
- limitar concorrência de CPU/GPU/render;
- limites configuráveis de import, duração, jobs e espaço temporário;
- proxies/sampling para evitar processamento desnecessário;
- medir caminhos lentos antes de otimizar.

### M02-7 — Validação Windows/Linux

**Agentes:** QA + Documentation + Backend/Frontend para fixes.

- smoke test Windows nativo;
- smoke test Linux;
- validar detecção/uso de FFmpeg e Python/Node;
- validar paths com espaços/unicode;
- validar cancelamento/subprocess nos dois OS;
- documentar limitações reais e troubleshooting.

### M02-8 — Security + code review final

**Agentes:** Security + Code Reviewer, preferencialmente read-only; Tech Lead coordena correções.

- storage/path traversal/symlinks;
- subprocess/filtergraph injection;
- arquivos não confiáveis e resource exhaustion;
- secrets/providers/logs;
- privacidade e opt-in de IA externa;
- regressões de arquitetura/cache/jobs;
- typing e testes faltantes;
- revisão de dependências desnecessárias.

### M02-9 — Release readiness

**Agentes:** Documentation + Tech Lead + QA.

- README completo e fiel ao estado real;
- `.env.example` sem secrets reais;
- passos Windows PowerShell e Linux;
- licenciamento/atribuições de dependências quando aplicável;
- exemplos de projeto/mídia somente se pequenos e redistribuíveis;
- comando de smoke test e checklist de release;
- limpar artefatos temporários e garantir `.gitignore` adequado quando esses arquivos existirem;
- preparar repositório público sem alegar suporte não validado.

## Dependências e paralelização

Sequencial crítico:

```text
M02-1 → M02-2 → M02-4/M02-5 → M02-6 → M02-7 → M02-8 → M02-9
          └→ M02-3 ────────────────┘
```

Pode paralelizar:
- captions avançadas com áudio/composição após `RenderPlan` estável;
- frontend de preview com backend de preview depois do contrato;
- documentação de setup com validação de OS, sem documentar resultado antes do teste.

## Critérios de aceite

- preview e render final correspondem de forma aceitável ao layout editado;
- FFmpeg recebe apenas comandos construídos internamente;
- cancelamento não deixa estado/temporários inconsistentes;
- mudança visual não dispara transcrição/análise sem relação;
- Windows nativo e Linux passam smoke tests documentados;
- aplicação continua útil sem API paga;
- nenhum finding bloqueador de Security/Code Reviewer permanece aberto.

## Testes esperados

- unitários para `RenderPlan`, filter graph e command builder;
- integração com fixtures curtas de vídeo/áudio;
- A/V sync e trims;
- captions timing;
- cache invalidation;
- cancelamento/cleanup;
- paths com espaços/unicode;
- falhas de FFmpeg e disco insuficiente simulável quando possível;
- build/test frontend;
- smoke tests manuais/automatizados Windows e Linux.

## Definição de pronto

Um usuário consegue abrir um projeto existente, escolher um clip/layout, gerar preview, renderizar um Short final e repetir o fluxo de forma documentada em Windows e Linux, com segurança e testes suficientes para um primeiro release público.
