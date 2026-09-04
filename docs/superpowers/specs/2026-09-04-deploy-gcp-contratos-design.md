# Deploy do contratos-service no GCP — Design

> Status: aprovado em brainstorming, pronto para plano de implementação.
> Este documento cobre **infraestrutura de deploy** (Cloud Run, Cloud SQL, Secret Manager, Pub/Sub, Cloud Build) — não repete decisões de arquitetura de aplicação já cobertas em `2026-08-24-contratos-service-design.md`.

## 1. Contexto

O código do `contratos-service` está pronto e testado (270 testes), seguindo exatamente as convenções do `ap-back-optin` (mesmo `CloudSQLClient`, `tenant_config.py`, `secrets.py`). O que falta é a infraestrutura de deploy no GCP — o serviço nunca foi publicado.

O design original (`2026-08-24-contratos-service-design.md` §1.1/§3) previa uma instância Cloud SQL própria por tenant/serviço, num projeto GCP diferente (`registradora-506000`, instância `contratos-db`) do que o optin acabou usando em produção (`brikz-ap`, instância `optin-pg`). Esta sessão **revisa essa decisão**: o banco deve ser o mesmo (mesma instância) do optin, respeitando o isolamento multi-tenant já implementado no código.

## 2. Decisões

### 2.1 Cloud SQL — mesma instância, banco por tenant

Reaproveita a instância `optin-pg` (projeto `brikz-ap`, região `southamerica-east1`) em vez de provisionar uma instância nova. Um banco Postgres **novo por tenant, dedicado ao contratos**: `ap_<cnpj>_contratos`.

Nome de banco deliberadamente diferente de `ap_<cnpj>` (usado pelo optin para o mesmo tenant): os dois serviços têm tabelas com nomes iguais (`webhook_inbox`, `cerc_requisicao`, `dominio_arranjo`) — usar o mesmo banco colidiria. Bancos diferentes na mesma instância são namespaces Postgres totalmente isolados (sem colisão possível), então isto **não** é o mesmo risco que usar o mesmo banco/schema.

Novo usuário Postgres `contratos_app` (análogo a `optin_app`), único para todos os tenants do contratos, com privilégios apenas nos bancos `ap_*_contratos`.

**Sem instância nova, sem projeto novo.** A instância antiga (`registradora-506000`/`contratos-db`) referenciada no `.env` de dev local e no design de 2026-08-24 fica obsoleta para homolog/produção; dev local não é alterado por este documento (fica a critério de uma sessão futura apontar o dev também pra `optin-pg`, se fizer sentido).

### 2.2 Cloud Run — serviço público

`contratos-service`, `--allow-unauthenticated`. Não há alternativa: o webhook `POST /api/v1/webhooks/contrato/<financiador_id>` é chamado diretamente pela CERC pela internet, autenticado por HTTP Basic Auth **na aplicação** (`_autenticado` em `views.py`), não por IAM do GCP — e o Cloud Run só controla acesso no nível do serviço inteiro, não por rota.

**Pendência documentada, não corrigida nesta tarefa:** as rotas `/api/v1/contratos/<financiador_id>` (listar, detalhar, criar, inativar, baixar) não têm nenhuma autenticação de aplicação hoje. Com o serviço público, qualquer um que souber um `financiador_id` (CNPJ, não é segredo) consegue chamá-las. Diferente do optin, que tem JWT compensando a exposição pública. Fica registrado no runbook como risco conhecido a resolver numa sessão futura (ex.: reaproveitar `IAM_JWT`/JWT do optin, ou API key por tenant).

### 2.3 Deploy automático via Cloud Build

Trigger automático no push para `master`, mesmo padrão do `optin-deploy-master`. Repositório de destino: `github.com/brikzai/ap-back-contratos` (confirmado pelo usuário nesta sessão — o remote atual, pessoal, `rdelimasilva/ap-back-novo-contrato`, será trocado como parte da implementação).

`cloudbuild.yaml` mais simples que o do optin: **sem** os steps `deploy-jobs`/`migrate` — o contratos não tem (nem terá, por decisão YAGNI já registrada no design de 24/08) um management command de migration tipo `migrate_tenants`. Schema é aplicado manualmente via `scripts/apply_schema.py`, já implementado. Pipeline: `build → push → deploy service`.

## 3. Recursos GCP a criar

| Recurso | Nome | Observação |
|---|---|---|
| Artifact Registry (docker) | `contratos` | imagem `southamerica-east1-docker.pkg.dev/brikz-ap/contratos/contratos-service` |
| Cloud SQL — banco por tenant | `ap_<cnpj>_contratos` | na instância existente `optin-pg` |
| Cloud SQL — usuário | `contratos_app` | único, reaproveitado por todos os tenants do contratos |
| Secret Manager | `DJANGO_SECRET_KEY` | estático, um por ambiente |
| Secret Manager | `TENANT_<cnpj>_CONFIG_CONTRATOS` | um por tenant, formato já implementado em `shared/tenant_config.py` |
| Service account (runtime) | `contratos-run@brikz-ap.iam.gserviceaccount.com` | `roles/cloudsql.client`, `roles/secretmanager.secretAccessor` |
| Service account (build) | `contratos-build@brikz-ap.iam.gserviceaccount.com` | `roles/run.admin`, `roles/artifactregistry.writer`, `roles/logging.logWriter`, `roles/cloudbuild.builds.builder`, + `serviceAccountUser` sobre `contratos-run@` |
| Service account (push/scheduler OIDC) | `contratos-pubsub-push@brikz-ap.iam.gserviceaccount.com` | assina o OIDC verificado por `shared/pubsub_auth.py` — usada tanto pela push subscription quanto pelo Cloud Scheduler (o código só valida uma conta esperada, `PUBSUB_PUSH_INVOKER_SA`) |
| Pub/Sub — tópico | `contratos-webhook-inbox` | nome já é o default em `shared/pubsub_client.py` |
| Pub/Sub — push subscription | `contratos-webhook-inbox-push` | aponta pra `.../api/v1/webhooks/contrato/processar`, autenticada pela SA acima |
| Cloud Scheduler | `contratos-sincronizar-dominio-arranjo` | diário, `POST .../api/v1/jobs/sincronizar-dominio-arranjo`, mesma SA OIDC |
| Cloud Run service | `contratos-service` | público, `--allow-unauthenticated` |
| Cloud Build trigger | `contratos-deploy-master` | branch `master`, repo `brikzai/ap-back-contratos` |

## 4. Fluxo de dados

- **CERC → webhook:** `POST /webhooks/contrato/{financiador_id}` (Basic Auth) → grava em `webhook_inbox` (no banco do tenant, `ap_<cnpj>_contratos`) → publica ponteiro no tópico `contratos-webhook-inbox` → push subscription (OIDC) → `POST /webhooks/contrato/processar` → aplica a máquina de estados.
- **Front/backend → API interna:** `GET/POST /contratos/{financiador_id}` → sem auth de app (pendência §2.2) → lê/escreve direto no banco do tenant.
- **Cloud Scheduler → job diário:** `POST /jobs/sincronizar-dominio-arranjo` (OIDC) → itera `_TENANTS_JOBS_PERIODICOS` (lista hardcoded no código-fonte, hoje só o CNPJ de dev) → atualiza `dominio_arranjo` em cada tenant.

## 5. Onboarding de tenant (procedimento documentado no runbook)

1. `gcloud sql databases create ap_<cnpj>_contratos --instance=optin-pg`.
2. Se `contratos_app` ainda não existe: `gcloud sql users create contratos_app --instance=optin-pg --password=...`. Conceder privilégios no banco novo (`GRANT ALL PRIVILEGES ON DATABASE ap_<cnpj>_contratos TO contratos_app`, via conexão admin).
3. Aplicar schema localmente: `python scripts/apply_schema.py sql/schema/01-contratos-schema.sql` e depois `02-contratos-schema-fixes.sql`, com `.env`/env vars apontando pro tenant novo.
4. Criar segredo `TENANT_<cnpj>_CONFIG_CONTRATOS` no Secret Manager (JSON com `cloudsql_connection_name=brikz-ap:southamerica-east1:optin-pg`, `cloudsql_db_user=contratos_app`, `cloudsql_db_name=ap_<cnpj>_contratos`, `cerc_client_id`, `cerc_client_secret`, `webhook_basic_user`, `webhook_basic_password`).
5. **Solicitar à CERC** o cadastro do webhook `tipoEvento=contrato` para `https://<url-do-contratos-service>/api/v1/webhooks/contrato/<cnpj>` — pedido novo e específico deste serviço; **não** reaproveita o webhook de agenda/UR do `ap-back-consulta-agenda` (evento diferente, `tipoEvento=agenda`, payload e processamento diferentes).
6. Se o tenant participa do job diário de domínio de arranjo: adicionar o CNPJ em `_TENANTS_JOBS_PERIODICOS` (`apps/contratos/views.py`) e reimplantar — é código, não config.

## 6. Riscos e pendências

1. **Rotas `/contratos/*` sem autenticação de aplicação** (§2.2) — serviço público expõe leitura/escrita a qualquer um que souber um `financiador_id`. Não corrigido nesta tarefa; registrar como item de segurança pendente.
2. **`PUBSUB_PUSH_INVOKER_SA` única para Pub/Sub e Scheduler** — se um dia precisar de contas diferentes para os dois, exige mudança de código (`shared/pubsub_auth.py` hoje só compara contra uma conta esperada).
3. **`_TENANTS_JOBS_PERIODICOS` hardcoded** — onboarding de tenant pro job diário depende de redeploy, não é dado de config (mesmo padrão já aceito no `ap-back-consulta-agenda`, citado em `docs/PROXIMOS-PASSOS.md`).
4. **Repositório antigo** (`rdelimasilva/ap-back-novo-contrato`) fica obsoleto após o remote mudar para `brikzai/ap-back-contratos` — decidir com o usuário se arquiva ou apenas abandona.
5. **Convenção de nome de banco por tenant** (`ap_<cnpj>_contratos`) é nova, introduzida por este documento — não existe em nenhum código ou doc anterior.
6. **Dev local não migrado:** o `.env` local de desenvolvimento continua apontando pra instância antiga (`registradora-506000:contratos-db`); este documento não altera isso.

## 7. Verificação pós-deploy (smoke test)

Mesmo espírito do runbook do optin (`docs/runbooks/gcp-setup.md` §8 daquele repo):

```
URL=$(gcloud run services describe contratos-service --region southamerica-east1 --format="value(status.url)")
curl -s -w "\n%{http_code}\n" "$URL/api/v1/health"                                          # 200
curl -s -w "\n%{http_code}\n" -u "<webhook_basic_user>:<webhook_basic_password>" \
  -X POST "$URL/api/v1/webhooks/contrato/<cnpj>" -d '{...}'                                 # 202
curl -s -w "\n%{http_code}\n" "$URL/api/v1/contratos/<cnpj>"                                 # 200, {"dados": []}
```

Confirmar também: mensagem publicada no tópico `contratos-webhook-inbox` chega na push subscription e é processada (log `[Processor]` sem erro de OIDC); `schema_aplicado` populada no banco novo do tenant.

## 8. Fora de escopo (fases seguintes)

- Corrigir a falta de autenticação de aplicação nas rotas `/contratos/*` (risco 1 acima).
- Migrar o `.env` de dev local para a instância `optin-pg`.
- Qualquer automação de provisionamento de tenant (hoje é manual/scriptado, decisão YAGNI mantida do design original).
- Ambiente de produção (este documento cobre apenas homologação, mesmo escopo do runbook atual do optin).
