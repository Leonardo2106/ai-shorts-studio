# Roadmap 00 — Foundation, Media e Whisper

## Objetivo

Entregar a primeira vertical slice real e utilizável do AI Shorts Studio:

> Importar `screen.mp4` + `webcam.mp4`, detectar propriedades reais, persistir um `Project`, visualizar as mídias e realizar uma transcrição local inicial com timestamps.

**Não implementar IA avançada, visão, scoring, editor 9:16 completo ou render final nesta etapa.**

## Contexto arquitetural

- monólito modular local-first;
- FastAPI + SQLite para metadata;
- filesystem em `storage/projects/<uuid>/` para mídia/cache;
- React/Vite/TS/Tailwind para UI;
- JobRunner local para operações pesadas;
- ffprobe como fonte de verdade de streams;
- faster-whisper local;
- Windows nativo + Linux obrigatórios.

## Milestones e tarefas

### M00-1 — Bootstrap e contratos fundamentais

**Responsável:** Tech Lead, com Backend/Frontend/QA depois dos contratos.

- definir estrutura inicial `backend/` e `frontend/` sem overengineering;
- bootstrap FastAPI, settings e `/health`;
- bootstrap React/Vite/TypeScript/Tailwind;
- definir `Project`, estados mínimos e DTOs/contratos HTTP;
- definir root de storage configurável e naming seguro por UUID;
- definir endpoint/capability para indicar presença/versão de FFmpeg/ffprobe/Whisper quando possível;
- preparar comandos equivalentes de desenvolvimento Windows/Linux.

**Aceite**
- backend e frontend iniciam localmente;
- `/health` responde;
- projeto vazio pode ser criado e persistido;
- nenhuma autenticação foi adicionada.

### M00-2 — Project + storage seguro

**Responsável:** Backend + QA; Security revisa.

- persistir metadata de `Project` em SQLite;
- criar `storage/projects/<uuid>/` sob root configurável;
- implementar utilitário central de path containment;
- impedir escapes via `..`, paths absolutos indevidos e symlinks quando aplicável;
- definir lifecycle mínimo sem apagar mídia arbitrariamente;
- expor endpoints mínimos para criar/listar/abrir projeto.

**Aceite**
- dois projetos possuem diretórios isolados;
- paths inválidos são rejeitados;
- mídia não é persistida como BLOB;
- testes cobrem containment e lifecycle básico.

### M00-3 — Importação e ffprobe

**Responsável:** Backend + QA; Explorer apenas se a saída ffprobe trouxer dúvida bloqueadora.

- importar inicialmente `screen.mp4` e `webcam.mp4` por fluxo seguro;
- não confiar apenas na extensão;
- executar ffprobe via subprocess arguments e `shell=False`;
- persistir metadata útil: duração, width/height, FPS, codecs, bitrate quando disponível, audio streams, sample rate, canais e metadata relevante;
- modelar zero, uma ou múltiplas tracks de áudio;
- cachear resultado de probe por identidade/versão do arquivo;
- retornar erros claros para arquivo inválido ou ffprobe ausente.

**Aceite**
- fixtures válidas retornam metadata determinística;
- arquivo sem áudio não quebra;
- múltiplas tracks são representadas;
- subprocess não aceita comando arbitrário do cliente.

### M00-4 — UI de projeto e mídia + sync manual

**Responsável:** Frontend; Backend fornece contrato estável; QA testa fluxos críticos.

- wizard/tela simples para criar/abrir projeto;
- importar/associar screen e webcam;
- exibir metadata principal;
- visualizar ambas as mídias no browser quando o codec for suportado;
- permitir offset manual em milissegundos e persistir configuração;
- deixar claro quando preview direto do navegador não é compatível.

**Aceite**
- usuário abre um projeto e vê screen/webcam associadas;
- offset manual pode ser alterado e reaberto;
- nenhuma UI de editor avançado ou IA fake é criada.

### M00-5 — JobRunner local

**Responsável:** Backend + Frontend + QA.

- definir estados `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`;
- criar interface de job e implementação local simples;
- mover transcrição para job; probe pode permanecer síncrono apenas se comprovadamente rápido, ou usar job se o design ficar mais consistente;
- expor progresso/estado por polling inicialmente;
- suportar erros e cancelamento quando tecnicamente seguro;
- garantir que restart não faça job `RUNNING` eterno sem estratégia de reconciliação.

**Aceite**
- UI não bloqueia durante transcrição;
- erro de job é observável;
- estado final é persistível/consultável conforme design escolhido.

### M00-6 — faster-whisper + transcript

**Responsável:** Backend + QA; Frontend para UI de configuração/transcript; Explorer só para comportamento real da engine instalada.

- integrar `faster-whisper` atrás de interface interna;
- criar presets Simple: Econômico, Balanceado, Qualidade, Máxima qualidade;
- criar base para Advanced somente com opções comprovadamente suportadas;
- preservar `source`, segment start/end, idioma, texto e word timestamps quando ativados;
- selecionar explicitamente fonte/track de áudio quando houver mais de uma;
- armazenar transcript/cache por arquivo + parâmetros relevantes;
- não exigir GPU; CPU deve ser caminho suportado;
- UI inicia job, acompanha progresso e mostra transcript com timestamps.

**Aceite**
- uma mídia pequena pode ser transcrita localmente com timestamps;
- cache evita rerun idêntico;
- alteração irrelevante de UI não invalida transcript;
- configuração inválida retorna erro útil;
- testes padrão não dependem de baixar modelo enorme nem de GPU.

### M00-7 — Hardening, portabilidade e documentação

**Responsável:** QA + Security + Code Reviewer + Documentation.

- validar setup em Linux;
- validar setup Windows nativo ou registrar claramente o que ainda exige validação física;
- revisar subprocess, storage, logs e limites de arquivo/job;
- revisar contratos backend/frontend e handling de erros;
- atualizar README somente com o que foi realmente implementado;
- documentar instalação de FFmpeg e dependências.

## Dependências e ordem

Obrigatoriamente sequencial:

```text
M00-1 → M00-2 → contrato de Project/Media estável
                    ├→ M00-3 → M00-6
                    └→ M00-4
M00-5 deve existir antes da transcrição longa de M00-6
M00-7 fecha a etapa
```

Pode paralelizar:
- após M00-1: bootstrap backend e frontend em diretórios separados;
- após contratos de mídia: M00-3 backend e base de M00-4 frontend;
- QA pode preparar fixtures/test harness enquanto Backend implementa, desde que não editem os mesmos arquivos.

## Arquivos/módulos provavelmente afetados

Ainda não criados; nomes podem ser ajustados pelo Tech Lead:
- `backend/app/main.py`, `core/settings.py`, `db/`, `projects/`, `media/`, `transcription/`, `jobs/`, testes;
- `frontend/src/features/projects/`, `media/`, `transcription/`, `api/`, testes;
- `.env.example`, scripts/README de setup quando o código exigir.

## Testes esperados

- criação/persistência de Project;
- path containment e entradas maliciosas;
- ffprobe parser: vídeo com áudio, sem áudio, múltiplas tracks, saída incompleta/erro;
- serialização de metadata;
- sync offset;
- state machine de jobs;
- transcript schema/cache;
- endpoints principais;
- componentes/fluxos frontend críticos;
- smoke de build/type-check.

## Definição de pronto

Roadmap 00 termina somente quando o milestone principal pode ser demonstrado ponta a ponta, os testes relevantes passam e Security/Code Reviewer não mantêm finding bloqueador.

**Stop condition:** não iniciar qualquer tarefa de `.roadmap/01-ai-vision-scoring-editor-roadmap.md`.
