# Roadmap 01 — IA, Visão, Scoring e Editor

## Objetivo

Transformar transcripts e sinais locais em candidatos de Shorts explicáveis e editáveis, mantendo um modo econômico totalmente local e providers externos opcionais.

**Pré-condição:** Roadmap 00 concluído e validado. Não iniciar rendering final do Roadmap 02.

## Funcionalidades

- geração local de candidatos;
- chunking/cache orientado a timestamps;
- adapters OpenAI/Gemini/Groq;
- configurações compatíveis de modelo/tokens;
- análise semântica estruturada;
- análise visual local por sampling;
- score engine configurável;
- ranking/deduplicação/overlap;
- ajuste manual de cortes;
- editor visual 9:16 com presets;
- captions e banner configuráveis.

## Milestones e tarefas

### M01-1 — Candidate generation local

**Agentes:** Tech Lead + Backend + QA.

- normalizar transcript sem perder timestamps/source;
- agrupar frases e detectar pausas;
- criar candidatos por heurísticas: frase completa, exclamação, surpresa, risada quando inferível do transcript/áudio, energia, pausas e continuidade;
- duração configurável com defaults aproximadamente 12s mínimo, 25–45s ideal, 60s máximo;
- pre/post-roll configuráveis;
- preservar explicação de por que cada candidato existe.

### M01-2 — Chunking + provider abstraction

**Agentes:** Backend; Security revisa secrets/privacidade; Explorer verifica APIs somente se necessário.

- criar interface comum e schemas internos para análise semântica;
- adapters independentes OpenAI/Gemini/Groq;
- chaves somente backend via env;
- enviar somente candidatos/chunks, nunca transcript inteiro por padrão;
- reutilizar cache por provider/modelo/prompt schema/chunk;
- retries/timeout/fallback controlados;
- capabilities endpoint expõe somente provider configurado + parâmetros suportados, nunca chave.

### M01-3 — Model/token settings + análise estruturada

**Agentes:** Backend + Frontend + QA.

- suportar, quando compatível: provider, model, max output tokens, temperature, top_p, reasoning/effort, timeout, retries, fallback e limite de chunk;
- UI mostra somente parâmetros suportados;
- estimar volume de chamadas/tokens quando possível sem inventar preço monetário;
- schema de métricas: hook, humor, novelty, context completeness, standalone quality, information value, narrative progression, dead-air penalty e recommended start/end;
- revalidar timestamps sugeridos no backend.

### M01-4 — Visão local econômica

**Agentes:** Backend + Explorer + QA; Security revisa privacidade/recursos.

- usar OpenCV e validar MediaPipe antes de depender dele;
- trabalhar apenas com sinais observáveis, não “emoções internas”;
- sampling temporal + resize/proxy para vídeo longo;
- métricas potenciais: face presente, boca abrindo, sorriso/sinal equivalente quando tecnicamente suportado, head/pose motion, body motion, motion intensity;
- análise mais densa somente ao redor de candidatos promissores;
- cache por mídia + parâmetros de sampling/versão do analisador.

### M01-5 — Score engine explicável

**Agentes:** Backend + Frontend + QA.

- conceitos `ScoreRule`, `ScoreProfile`, `ScoreBreakdown`;
- regras ligáveis/desligáveis, peso editável, presets persistíveis e restore default;
- scoring desacoplado de provider externo;
- breakdown mostra contribuição positiva/negativa por regra;
- defaults iniciais coerentes com o briefing, mas fáceis de recalibrar.

### M01-6 — Ranking

**Agentes:** Backend + QA.

- ordenar por score;
- remover quase duplicados;
- limitar overlap;
- respeitar duração;
- preservar diversidade básica;
- Top N configurável;
- usuário pode sempre alterar start/end manualmente.

### M01-7 — Editor visual 9:16

**Agentes:** Frontend + Backend para schemas; QA.

- canvas lógico `1080x1920`;
- elementos: webcam, tela, legenda, banner, background, texto opcional, imagem/logo opcional;
- mover/redimensionar/ocultar/z-index/crop/contain/cover/borda/radius/padding/opacidade quando aplicável;
- estado do editor serializável em `EditConfig`;
- não chamar FFmpeg a cada interação;
- presets obrigatórios:
  1. webcam em cima + tela embaixo;
  2. webcam em cima + tela no meio + banner embaixo;
  3. tela full-screen + webcam overlay;
  4. webcam full-screen + tela picture-in-picture.

### M01-8 — Captions + banner

**Agentes:** Frontend + Backend + QA.

- captions baseadas em word timestamps quando disponíveis;
- fonte, tamanho, cor, peso, uppercase, outline, shadow, box, posição, largura, palavras por linha/bloco, active word highlight e highlight importante quando suportado;
- banner com texto, imagem/logo, background, transparência, posição e intervalo;
- armazenar intenção de estilo sem gerar render final ainda.

## Dependências

Sequencial crítico:

```text
Candidate generation → chunk/provider analysis ─┐
Vision local ────────────────────────────────────┼→ Score engine → Ranking → Candidate review/editor
Transcript/word timestamps ─────────────────────┘
```

Pode paralelizar:
- M01-2 e spike de M01-4 após schema de candidato;
- frontend de score profiles enquanto backend finaliza persistência, se contrato estiver estável;
- captions/banner UI em paralelo com editor core após `EditConfig` definido.

## Critérios de aceite

- modo 100% local gera e ranqueia candidatos sem API externa;
- providers externos podem enriquecer score sem serem requisito;
- usuário entende por que um clip foi ranqueado;
- nenhuma métrica visual afirma emoção interna como fato;
- editor produz `EditConfig` persistível e reabrível;
- parâmetros incompatíveis de provider não aparecem/causam chamadas inválidas;
- IA não recebe vídeo/webcam automaticamente.

## Testes esperados

- chunking preserva timestamps/source;
- provider contract/fallback/cache com doubles locais;
- validação de timestamps de IA;
- vision sampling não escala frame-a-frame em full resolution;
- score breakdown e profiles;
- ranking/overlap/dedup;
- serialização/migração de `EditConfig` se necessária;
- editor interactions e presets;
- captions geradas a partir de timestamps.

## Definição de pronto

Usuário importa um projeto já transcrito, gera candidatos locais, opcionalmente analisa por provider, vê score explicável, escolhe/ajusta um trecho e monta um layout 9:16 persistível.

**Stop condition:** não implementar FFmpeg final/render release do Roadmap 02.
