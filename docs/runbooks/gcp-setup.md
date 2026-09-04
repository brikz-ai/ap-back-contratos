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
