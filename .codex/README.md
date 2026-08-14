# Codex no AI Shorts Studio

Este diretório configura a equipe de subagentes do projeto. O objetivo é ganhar paralelismo útil sem transformar cada tarefa em oito conversas caras.

## Equipe

| Agente | Modelo / reasoning | Modo padrão | Use quando |
|---|---|---|---|
| `tech_lead` | GPT-5.6 Sol / High | workspace-write | arquitetura, contratos, decomposição, integração, ADRs |
| `security` | GPT-5.6 Sol / High | read-only | filesystem, FFmpeg/subprocess, secrets, privacidade, resource exhaustion |
| `code_reviewer` | GPT-5.6 Sol / High | read-only | revisão final de correctness, regressões, arquitetura, typing e portabilidade |
| `backend` | GPT-5.6 Sol / Medium | workspace-write | FastAPI, SQLite, mídia, Whisper, jobs, IA, visão, scoring, rendering |
| `frontend` | GPT-5.6 Sol / Medium | workspace-write | React/Vite/TS/Tailwind, fluxos, editor, preview, progresso |
| `qa` | GPT-5.6 Terra / Medium | workspace-write | unit/integration/E2E seletivo, fixtures e regressão |
| `explorer` | GPT-5.6 Terra / Low | read-only | spikes rápidos e incertezas bloqueadoras |
| `documentation` | GPT-5.6 Luna / Low | workspace-write | README, setup, Windows/Linux, providers e troubleshooting |

## Por que essa combinação

- Sol fica concentrado em decisões de alto impacto e implementação principal.
- Terra cobre QA/exploração com boa capacidade e menor custo.
- Luna cuida de documentação previsível.
- `max_concurrent_threads_per_session = 4` é um teto, não uma meta. Em uma conta Plus, prefira 2–3 subagentes ativos na maior parte do tempo.

## Fluxo recomendado

```text
Tech Lead
  ├─ Explorer (somente se existir incerteza bloqueadora)
  ├─ Backend ─┐
  ├─ Frontend ├─ paralelos quando contratos/arquivos não colidem
  └─ QA ──────┘
        ↓
     Security (quando houver superfície sensível)
        ↓
   Code Reviewer
        ↓
   Documentation
```

Security/Code Reviewer podem acontecer em paralelo em uma revisão grande, mas a entrega só fecha depois que findings bloqueadores forem resolvidos.

## Quando NÃO paralelizar

- frontend depende de schema/endpoint ainda indefinido;
- dois agentes precisariam editar o mesmo arquivo;
- uma decisão de storage/job model ainda está em aberto;
- o problema ainda não foi reproduzido;
- a próxima tarefa pertence a outro roadmap.

## Economia de tokens

1. Comece sempre por `AGENTS.md` + roadmap ativo.
2. Leia somente arquivos relacionados ao task slice.
3. Use Explorer para investigar, não para repetir análise já feita pelo Tech Lead.
4. Passe ao subagente um objetivo, arquivos prováveis, critérios de aceite e o que ele não deve tocar.
5. Rode testes focados antes de suítes completas.
6. Retorne ao Tech Lead um handoff curto: mudanças, contratos, testes, riscos e blockers.
7. Não convoque Security/Reviewer para cada mudança trivial; use-os em superfícies sensíveis ou milestones.

## Exemplos de prompts internos

### Backend

```text
Use o agente backend. Execute apenas a tarefa M00-T03 do roadmap 00. Leia AGENTS.md e os módulos de project/storage já existentes. Implemente import seguro e inspeção via ffprobe sem tocar no frontend. Adicione testes focados e reporte contratos expostos.
```

### Frontend

```text
Use o agente frontend. Com base no contrato de Project/Media já definido, implemente somente a tela de importação e visualização básica do roadmap 00. Não crie editor 9:16 nem mocks de IA. Rode type-check/testes relevantes.
```

### Explorer

```text
Use o agente explorer em read-only. Descubra como o código atual representa audio streams do ffprobe e se existe alguma hipótese de single-track. Retorne arquivos/símbolos e riscos; não faça mudanças.
```

### Review

```text
Use security para revisar path containment e subprocess/ffprobe; em seguida use code_reviewer para correctness, portabilidade e testes. Não implemente automaticamente findings sem devolver ao Tech Lead.
```

## Configuração Codex

A configuração é project-scoped em `.codex/config.toml`. Os arquivos em `.codex/agents/` usam o schema oficial de custom agents (`name`, `description`, `developer_instructions`) e overrides suportados de `model`, `model_reasoning_effort` e `sandbox_mode`.

Referências oficiais consultadas na criação deste bootstrap:
- https://developers.openai.com/codex/subagents
- https://developers.openai.com/codex/config-reference
- https://developers.openai.com/codex/models
- https://developers.openai.com/codex/agent-configuration/agents-md

A disponibilidade de modelos e limites de plano pode mudar; revise essas páginas antes de fazer alterações futuras na configuração.
