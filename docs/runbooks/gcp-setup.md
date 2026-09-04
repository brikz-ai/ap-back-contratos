# Runbook — infra GCP do contratos-service

Projeto de homologação: `brikz-ap` (mesmo projeto do optin-service), região `southamerica-east1`.
Spec: `docs/superpowers/specs/2026-09-04-deploy-gcp-contratos-design.md`.

Cada seção é idempotente ou verificável — rode o `describe` antes de recriar.

## 0. Sessão

    gcloud config set project brikz-ap
    gcloud config set account ricardo@brikz.ai

## 1. APIs e Artifact Registry

    gcloud services enable run.googleapis.com sqladmin.googleapis.com secretmanager.googleapis.com \
      cloudbuild.googleapis.com artifactregistry.googleapis.com iam.googleapis.com \
      cloudresourcemanager.googleapis.com compute.googleapis.com pubsub.googleapis.com \
      cloudscheduler.googleapis.com
    gcloud artifacts repositories create contratos --repository-format=docker --location=southamerica-east1 \
      --description="Imagens do contratos-service"

Verificar: `gcloud artifacts repositories describe contratos --location=southamerica-east1`

Feito em 2026-09-04: APIs habilitadas, repositório `contratos` criado
(`projects/brikz-ap/locations/southamerica-east1/repositories/contratos`, formato DOCKER).

## 2. Service accounts e IAM

    gcloud iam service-accounts create contratos-run --display-name="contratos-service runtime (Cloud Run)"
    gcloud iam service-accounts create contratos-build --display-name="contratos-service Cloud Build"
    gcloud iam service-accounts create contratos-pubsub-push --display-name="contratos-service Pub/Sub push + Scheduler OIDC"
    for r in roles/cloudsql.client roles/secretmanager.secretAccessor; do
      gcloud projects add-iam-policy-binding brikz-ap --member=serviceAccount:contratos-run@brikz-ap.iam.gserviceaccount.com --role=$r --condition=None
    done
    for r in roles/run.admin roles/artifactregistry.writer roles/logging.logWriter roles/cloudbuild.builds.builder; do
      gcloud projects add-iam-policy-binding brikz-ap --member=serviceAccount:contratos-build@brikz-ap.iam.gserviceaccount.com --role=$r --condition=None
    done
    gcloud iam service-accounts add-iam-policy-binding contratos-run@brikz-ap.iam.gserviceaccount.com \
      --member=serviceAccount:contratos-build@brikz-ap.iam.gserviceaccount.com --role=roles/iam.serviceAccountUser

`contratos-run@` não tem nada além de Cloud SQL client e leitura de segredos.
`contratos-pubsub-push@` não recebe nenhum papel de projeto aqui — ela só
precisa existir para assinar tokens OIDC (seções 7/8 concedem os papéis
necessários, se algum dia precisar de `run.invoker` explícito).

Verificar papéis reais (a saída de `add-iam-policy-binding` mostra a policy
inteira do projeto, compartilhada com o optin — não confiar nela sozinha):

    gcloud projects get-iam-policy brikz-ap --flatten="bindings[].members" \
      --filter="bindings.members:<conta>@brikz-ap.iam.gserviceaccount.com" --format="value(bindings.role)"

Feito em 2026-09-04: as 3 contas criadas; `contratos-run@` com
`roles/cloudsql.client` + `roles/secretmanager.secretAccessor`;
`contratos-build@` com `roles/run.admin`, `roles/artifactregistry.writer`,
`roles/logging.logWriter`, `roles/cloudbuild.builds.builder` +
`roles/iam.serviceAccountUser` sobre `contratos-run@` — confirmado via
`get-iam-policy` (não pela saída do `add-iam-policy-binding`, que mostra a
policy inteira do projeto e não isola por membro).

## 3. Banco do tenant (reaproveitando a instância `optin-pg`)

Sem instância nova — `contratos` usa a mesma instância Cloud SQL do
`optin-service` (`optin-pg`, `brikz-ap:southamerica-east1:optin-pg`), com um
usuário e bancos próprios (design 2026-09-04 §2.1): um banco novo por
tenant, `ap_<cnpj>_contratos` — nome deliberadamente diferente de
`ap_<cnpj>` (usado pelo optin para o mesmo tenant) porque as tabelas
`webhook_inbox`, `cerc_requisicao`, `dominio_arranjo` existem nos dois
serviços com o mesmo nome; bancos diferentes na mesma instância são
namespaces Postgres isolados, então não há colisão.

    gcloud sql users create contratos_app --instance=optin-pg --password="<gerada, guardada só na sessão>"
    gcloud sql databases create ap_<cnpj>_contratos --instance=optin-pg

Conceder privilégios (não dá pra fazer só com `gcloud` — precisa de uma
conexão SQL como admin): pelo **Cloud SQL Studio** do Console
(`https://console.cloud.google.com/sql/instances/optin-pg/studio?project=brikz-ap`,
conectado com uma conta com IAM no projeto, banco `ap_<cnpj>_contratos`):

    GRANT ALL PRIVILEGES ON DATABASE ap_<cnpj>_contratos TO contratos_app;
    GRANT ALL ON SCHEMA public TO contratos_app;

`contratos_app` é único e reaproveitado por todos os tenants do contratos —
só o banco muda por tenant, mesmo padrão de usuário único do `optin_app`.
Onboarding de um novo tenant repete `gcloud sql databases create` + os dois
`GRANT` acima (o usuário já existe depois da primeira vez).

**Nota:** diferente do `optin_app` (que tem `CREATEDB` e por isso cria e é
dono de cada banco de tenant sozinho, via `apps/tenants/provisioning.py`),
`contratos_app` **não** tem `CREATEDB` — o banco é criado via
`gcloud sql databases create` (API do Cloud SQL, não precisa de conexão
SQL) e os privilégios são concedidos à parte. Privilégio mais restrito
(só nos bancos que já existem), ao custo de precisar do Cloud SQL Studio
(ou de outra conexão admin) a cada novo tenant.

Feito em 2026-09-04: usuário `contratos_app` criado; banco
`ap_38138785000136_contratos` criado; `GRANT ALL PRIVILEGES ON DATABASE` e
`GRANT ALL ON SCHEMA public` aplicados via Cloud SQL Studio.

## 4. Segredos estáticos e por tenant

    python -c 'import secrets; print(secrets.token_urlsafe(50))' \
      | gcloud secrets create DJANGO_SECRET_KEY_CONTRATOS --data-file=- --replication-policy=user-managed --locations=southamerica-east1

**Nome com sufixo `_CONTRATOS`:** o optin já tem um segredo `DJANGO_SECRET_KEY`
sem sufixo neste mesmo projeto — reaproveitar o nome faria os dois serviços
compartilharem a mesma chave de assinatura Django sem essa ser a intenção.
Descoberto na prática: a primeira tentativa de criar `DJANGO_SECRET_KEY`
falhou com "already exists" (segredo do optin). O env var dentro do
container continua `DJANGO_SECRET_KEY` (nome que `config/settings.py` já
lê) — só o nome do segredo no Secret Manager tem o sufixo; o mapeamento
fica no `--set-secrets` do `cloudbuild.yaml`
(`DJANGO_SECRET_KEY=DJANGO_SECRET_KEY_CONTRATOS:latest`).

Segredo por tenant (`TENANT_<cnpj>_CONFIG_CONTRATOS`, JSON) — monte num
arquivo temporário local (nunca em pipe triplo — confirme sempre com
`gcloud secrets versions list <nome>` depois), suba com `--data-file=<arquivo>`
e apague o arquivo em seguida:

    gcloud secrets create TENANT_<cnpj>_CONFIG_CONTRATOS --data-file=<arquivo> \
      --replication-policy=user-managed --locations=southamerica-east1

Chaves do JSON: `cloudsql_connection_name` (sempre
`brikz-ap:southamerica-east1:optin-pg`), `cloudsql_db_user`
(`contratos_app`), `cloudsql_db_password`, `cloudsql_db_name`
(`ap_<cnpj>_contratos`), `cloudsql_ip_type` (`PUBLIC`), `cerc_client_id`,
`cerc_client_secret`, `webhook_basic_user`, `webhook_basic_password` —
formato já implementado em `shared/tenant_config.py`, sem mudança de código.

Rotação de senha do `contratos_app`: `gcloud sql users set-password
contratos_app --instance=optin-pg --password=...` e nova versão de **cada**
`TENANT_<cnpj>_CONFIG_CONTRATOS` (o usuário é compartilhado entre tenants,
mas cada segredo guarda a senha atual); reiniciar o service (cache por
processo em `shared/cloudsql_client.py`).

Feito em 2026-09-04: `DJANGO_SECRET_KEY_CONTRATOS` criado (versão 1);
`TENANT_38138785000136_CONFIG_CONTRATOS` criado com os campos CERC/webhook
reaproveitados do `.env` local (mesmas credenciais de homologação já usadas
pelo optin — CNPJ participante `38138785000136`) e os campos `cloudsql_*`
apontando pra `optin-pg`/`contratos_app`/`ap_38138785000136_contratos`.
Versão 1 acabou com uma senha que a autenticação Postgres rejeitou (ver
seção 5 — motivo não identificado, resolvido resetando a senha); versão 1
desabilitada, versão 2 (com a senha corrigida) é a `:latest` em uso.

## 5. Schema do tenant

Sem migration runner (decisão YAGNI, design original) — aplica direto via
`scripts/apply_schema.py`, apontando temporariamente as env vars pro
tenant (nunca editar o `.env` versionado):

    export CLOUDSQL_CONNECTION_NAME="brikz-ap:southamerica-east1:optin-pg"
    export CLOUDSQL_DB_USER="contratos_app"
    export CLOUDSQL_DB_PASSWORD="<senha do contratos_app>"
    export CLOUDSQL_DB_NAME="ap_<cnpj>_contratos"
    python scripts/apply_schema.py sql/schema/01-contratos-schema.sql
    python scripts/apply_schema.py sql/schema/02-contratos-schema-fixes.sql

Idempotente: reaplicar um arquivo já aplicado (mesmo checksum) é um no-op
(confirmado rodando o segundo arquivo duas vezes).

Conexão via Cloud SQL Python Connector usa Application Default Credentials
(ADC) da sua conta gcloud — se expiradas, `apply_schema.py` falha com
`RefreshError: Reauthentication is needed`; resolver com
`gcloud auth application-default login --account=ricardo@brikz.ai`
(login interativo, abre o navegador).

Onboarding de um tenant novo repete estes dois comandos com o
`CLOUDSQL_DB_NAME` novo (depois de criar o banco e conceder privilégios,
seção 3).

Feito em 2026-09-04: schema aplicado em `ap_38138785000136_contratos` — 12
tabelas confirmadas (`contrato`, `garantia`, `garantia_ur`, `webhook_inbox`,
`cerc_requisicao`, `dominio_arranjo`, `schema_aplicado`, entre outras).

## 6. Primeiro deploy (cloudbuild.yaml)

    gcloud builds submit --config cloudbuild.yaml --substitutions=_TAG=$(git rev-parse --short HEAD)

O que acontece: build → push → `gcloud run deploy contratos-service`. Sem
jobs de migration (diferente do optin) — o schema já foi aplicado
manualmente na seção 5 antes deste primeiro deploy.

URL do serviço: `gcloud run services describe contratos-service --region southamerica-east1 --format="value(status.url)"`

Se `/api/v1/health` voltar `403` do Google Frontend (não da aplicação): é a
mesma Domain Restricted Sharing já documentada no runbook do optin — a
exceção de organização é por projeto (`brikz-ap`), vale automaticamente
aqui (confirmado: não precisou repetir nada).

**Achado nesta implementação:** a primeira tentativa de deploy falhou —
`--set-env-vars` usava `^@^` como separador (copiado do optin, pra escapar
as vírgulas de `CORS_ALLOWED_ORIGINS`), mas `PUBSUB_PUSH_INVOKER_SA` é um
e-mail de service account que também contém `@`, quebrando o parser
(`Bad syntax for dict arg`). Corrigido trocando o separador pra `|` no
`cloudbuild.yaml`.

Feito em 2026-09-04: `gcloud builds submit --substitutions=_TAG=e8780b8` →
`SUCCESS` (~2min, build+push+deploy, sem jobs). Serviço `contratos-service`
no ar, revisão `contratos-service-00001-fzk`, URL
`https://contratos-service-6sy5bhymwq-rj.a.run.app`. `/api/v1/health` → 200
de primeira (sem precisar do binding manual de IAM que o optin precisou).

## 7. Pub/Sub — processamento assíncrono do webhook

    gcloud pubsub topics create contratos-webhook-inbox
    SERVICE_URL="$(gcloud run services describe contratos-service --region southamerica-east1 --format='value(status.url)')"
    gcloud pubsub subscriptions create contratos-webhook-inbox-push \
      --topic=contratos-webhook-inbox \
      --push-endpoint="${SERVICE_URL}/api/v1/webhooks/contrato/processar" \
      --push-auth-service-account=contratos-pubsub-push@brikz-ap.iam.gserviceaccount.com \
      --push-auth-token-audience="https://contratos-service.internal/webhooks/contrato/processar" \
      --ack-deadline=30

A audiência (`--push-auth-token-audience`) é uma string fixa acordada com o
env var `PUBSUB_PUSH_AUDIENCE` do Cloud Run (`cloudbuild.yaml`) — **não** é
a URL real do serviço, decisão deliberada pra não precisar redeployar toda
vez que a URL mudasse. `shared/pubsub_auth.py` valida essa audiência e o
e-mail da service account contra `PUBSUB_PUSH_INVOKER_SA`.

Teste manual sem esperar um webhook real da CERC:

    gcloud pubsub topics publish contratos-webhook-inbox \
      --message='{"webhook_inbox_id": "id-de-teste", "financiador_id": "<cnpj>"}'
    gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="contratos-service"' --limit 20 --freshness=5m

Feito em 2026-09-04: tópico e subscription criados, publish de teste
confirmado nos logs — `[Processor] webhook_inbox_id=... não encontrado ...
condição permanentemente irrecuperável, confirmando entrega` (204), sem
nenhum erro de OIDC. Confirma que a autenticação da push subscription
está correta ponta a ponta.

## 8. Cloud Scheduler — job diário de domínio de arranjo

    SERVICE_URL="$(gcloud run services describe contratos-service --region southamerica-east1 --format='value(status.url)')"
    gcloud scheduler jobs create http contratos-sincronizar-dominio-arranjo \
      --location=southamerica-east1 \
      --schedule="0 6 * * *" \
      --uri="${SERVICE_URL}/api/v1/jobs/sincronizar-dominio-arranjo" \
      --http-method=POST \
      --oidc-service-account-email=contratos-pubsub-push@brikz-ap.iam.gserviceaccount.com \
      --oidc-token-audience="https://contratos-service.internal/webhooks/contrato/processar"

Mesma service account e mesma audiência da subscription do Pub/Sub (seção
7) — `shared/pubsub_auth.py` só valida um par (audiência, e-mail) global,
não diferencia a origem da chamada.

**Pendência:** `_TENANTS_JOBS_PERIODICOS` (`apps/contratos/views.py`) é uma
lista hardcoded no código-fonte — hoje só `12345678000199` (CNPJ de dev).
Esse tenant **não** foi provisionado nesta infra (só `38138785000136`, o
tenant real de homolog) — rodar o job hoje falha com `Secret
TENANT_12345678000199_CONFIG_CONTRATOS not found`, um erro esperado dado
esse descompasso, não um problema de infra. Onboardar um tenant real pro
job diário exige adicionar o CNPJ nessa lista (código) e reimplantar.

Testar manualmente: `gcloud scheduler jobs run contratos-sincronizar-dominio-arranjo --location=southamerica-east1`

Feito em 2026-09-04: job criado, `state: ENABLED`. Disparo manual
confirmou OIDC correto (chegou até a lógica de negócio, sem 401) — falhou
depois por causa da pendência acima (`12345678000199` não provisionado),
comportamento esperado e não bloqueante pra este runbook.

## 6b. Deploy automático (Cloud Build trigger)

    gcloud builds connections describe optin-github --region=southamerica-east1
    gcloud builds repositories create contratos-back \
      --connection=optin-github --region=southamerica-east1 \
      --remote-uri=https://github.com/brikzai/ap-back-contratos.git
    gcloud builds triggers create github \
      --name=contratos-deploy-master \
      --region=southamerica-east1 \
      --repository=projects/brikz-ap/locations/southamerica-east1/connections/optin-github/repositories/contratos-back \
      --branch-pattern='^master$' \
      --build-config=cloudbuild.yaml \
      --substitutions=_TAG='$SHORT_SHA' \
      --service-account=projects/brikz-ap/serviceAccounts/contratos-build@brikz-ap.iam.gserviceaccount.com

Reaproveita a conexão GitHub `optin-github` (já existente pro optin) — o
GitHub App já cobre a org `brikzai` inteira, então registrar o repo
`ap-back-contratos` não exigiu nenhuma autorização manual adicional (setup
mais simples do que o previsto originalmente). Sem aprovação manual de
build (sem `approvalConfig`), mesmo padrão do optin: qualquer push na
`master` vai para o ar sozinho.

Acompanhar: `gcloud builds list --region=southamerica-east1 --limit=5`

Feito em 2026-09-04: conexão `optin-github` reaproveitada, repositório
`contratos-back` registrado, trigger `contratos-deploy-master` criado —
verificado sem `approvalConfig` (aprovação automática).
