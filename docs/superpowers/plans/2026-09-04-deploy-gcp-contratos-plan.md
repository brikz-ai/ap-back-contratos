# Deploy do contratos-service no GCP — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publicar o `contratos-service` no Cloud Run do projeto `brikz-ap`, reaproveitando a instância Cloud SQL `optin-pg` já existente (um banco novo por tenant, `ap_<cnpj>_contratos`), com deploy automático via Cloud Build a cada push em `master`.

**Architecture:** Espelha a infra já validada do `ap-back-optin` (Cloud Run público, Cloud SQL Python Connector, Secret Manager por tenant, Cloud Build), com duas diferenças deliberadas: reaproveita a instância Cloud SQL do optin (banco novo por tenant) em vez de criar uma instância própria, e o pipeline de deploy não tem jobs de migration (schema é aplicado manualmente via `scripts/apply_schema.py`, já implementado).

**Tech Stack:** gcloud CLI, Cloud Run, Cloud SQL (Postgres 17, via Cloud SQL Python Connector), Secret Manager, Pub/Sub, Cloud Scheduler, Cloud Build, Artifact Registry, GitHub.

**Spec:** `docs/superpowers/specs/2026-09-04-deploy-gcp-contratos-design.md`

## Global Constraints

- Projeto GCP: `brikz-ap`. Região: `southamerica-east1`. Nenhum projeto/instância Cloud SQL novo.
- Instância Cloud SQL reaproveitada: `optin-pg`. Banco novo por tenant: `ap_<cnpj>_contratos` (nunca `ap_<cnpj>` sozinho — colide com tabelas do optin).
- Usuário Postgres do serviço: `contratos_app` (único, reaproveitado por todos os tenants do contratos).
- Cloud Run service `contratos-service` **deve** ser `--allow-unauthenticated` (webhook da CERC é chamado de fora do GCP, autenticado por Basic Auth na aplicação, não por IAM).
- `PUBSUB_PUSH_AUDIENCE` e `PUBSUB_PUSH_INVOKER_SA` são únicos e globais no processo — a mesma string de audiência e a mesma service account autenticam tanto o push subscription do Pub/Sub quanto o Cloud Scheduler (`shared/pubsub_auth.py` só compara contra um valor esperado de cada).
- Nenhum segredo real (senhas, client secrets) é escrito em arquivos versionados neste plano ou no repositório — sempre via `gcloud secrets` a partir de arquivo temporário local, apagado depois.
- Repositório de destino do trigger: `github.com/brikzai/ap-back-contratos` (já existe, confirmado pelo usuário).
- Tenant de referência usado nos exemplos deste plano: `38138785000136` (mesmo CNPJ participante já usado pelo optin em homolog, já com credenciais CERC configuradas no `.env` local do contratos).

---

## Task 1: Pipeline de build/deploy (`cloudbuild.yaml`, `.gcloudignore`)

Arquivos locais, sem tocar em nada no GCP ainda.

**Files:**
- Create: `cloudbuild.yaml`
- Create: `.gcloudignore`

- [ ] **Step 1: Criar `cloudbuild.yaml`**

```yaml
# Deploy do contratos-service em Cloud Run (spec 2026-09-04 §2.3/§3).
# Pipeline simples: build → push → deploy — sem jobs de migration, o schema
# é aplicado manualmente via scripts/apply_schema.py (decisão YAGNI do
# design original, 2026-08-24-contratos-service-design.md §1).
#
# Uso manual (fora do trigger):
#   gcloud builds submit --config cloudbuild.yaml --substitutions=_TAG=$(git rev-parse --short HEAD)
#
# Também roda automático: trigger "contratos-deploy-master" a cada push na
# master de github.com/brikzai/ap-back-contratos (docs/runbooks/gcp-setup.md
# seção 5b).
#
# Pré-requisitos: docs/runbooks/gcp-setup.md seções 1-4 e os segredos do(s)
# tenant(s) (seção 6).
steps:
  - id: build
    name: gcr.io/cloud-builders/docker
    args: ['build', '-t', '${_IMAGE}:${_TAG}', '.']

  - id: push
    name: gcr.io/cloud-builders/docker
    args: ['push', '${_IMAGE}:${_TAG}']

  - id: deploy-service
    name: gcr.io/google.com/cloudsdktool/cloud-sdk
    entrypoint: gcloud
    args:
      - run
      - deploy
      - ${_SERVICE}
      - --image=${_IMAGE}:${_TAG}
      - --region=${_REGION}
      - --platform=managed
      # Público: o webhook da CERC chama de fora do GCP; a autenticação é
      # Basic Auth por tenant na aplicação (apps/contratos/views.py::_autenticado).
      - --allow-unauthenticated
      - --port=8080
      - --cpu=1
      - --memory=512Mi
      - --concurrency=20
      - --min-instances=0
      - --max-instances=3
      - --timeout=60
      - --service-account=${_RUNTIME_SA}
      # ^@^ troca o separador para '@' porque CORS_ALLOWED_ORIGINS pode ter vírgulas.
      - --set-env-vars=^@^ENVIRONMENT=${_ENVIRONMENT}@GOOGLE_CLOUD_PROJECT=$PROJECT_ID@ALLOWED_HOSTS=*@CORS_ALLOWED_ORIGINS=${_CORS_ALLOWED_ORIGINS}@CERC_AUTH_URL=${_CERC_AUTH_URL}@CERC_API_BASE_URL=${_CERC_API_BASE_URL}@PUBSUB_PUSH_AUDIENCE=${_PUBSUB_PUSH_AUDIENCE}@PUBSUB_PUSH_INVOKER_SA=${_PUBSUB_PUSH_INVOKER_SA}@WEB_CONCURRENCY=2
      - --set-secrets=DJANGO_SECRET_KEY=DJANGO_SECRET_KEY:latest

substitutions:
  _TAG: manual                     # passe _TAG=$(git rev-parse --short HEAD) no submit
  _REGION: southamerica-east1
  _SERVICE: contratos-service
  _IMAGE: southamerica-east1-docker.pkg.dev/brikz-ap/contratos/contratos-service
  _RUNTIME_SA: contratos-run@brikz-ap.iam.gserviceaccount.com
  _ENVIRONMENT: homolog
  _CORS_ALLOWED_ORIGINS: http://localhost:5173
  _CERC_AUTH_URL: https://api.int.cerc.com/oauth/token
  _CERC_API_BASE_URL: https://ap-homolog.cerc.inf.br
  # String fixa acordada entre este env var e a audiência configurada na
  # push subscription (Task 9) e no Cloud Scheduler (Task 10) — não precisa
  # ser a URL real do Cloud Run, só precisa ser idêntica nos três lugares.
  _PUBSUB_PUSH_AUDIENCE: https://contratos-service.internal/webhooks/contrato/processar
  _PUBSUB_PUSH_INVOKER_SA: contratos-pubsub-push@brikz-ap.iam.gserviceaccount.com

serviceAccount: projects/brikz-ap/serviceAccounts/contratos-build@brikz-ap.iam.gserviceaccount.com
timeout: 1200s
options:
  logging: CLOUD_LOGGING_ONLY
  machineType: E2_HIGHCPU_8
```

- [ ] **Step 2: Validar sintaxe YAML**

Run: `python -c "import yaml; yaml.safe_load(open('cloudbuild.yaml', encoding='utf-8'))" && echo OK`
Expected: `OK` (sem exceção). Isto só valida sintaxe YAML — não pega erros de substituição do Cloud Build, que só aparecem no `gcloud builds submit` real (Task 8).

- [ ] **Step 3: Criar `.gcloudignore`**

```
# Controla o que `gcloud builds submit` envia como fonte do build
# (independente do .dockerignore, que só controla o contexto do `docker
# build` já enviado). Explícito em vez de depender do fallback
# (#!include:.gitignore).
.git
.gitignore
.claude
.superpowers
.venv
venv
__pycache__
*.pyc
.pytest_cache
.env
.env.*
!.env.example
```

- [ ] **Step 4: Conferir que `.env` real não seria enviado**

Run: `git check-ignore -v .env` (confirma que o `.gitignore` já protege o `.env` local) e revisar visualmente `.gcloudignore` pra garantir que a linha `.env.*` e `.env` cobrem qualquer variante.
Expected: `git check-ignore` imprime uma linha (match) — `.env` está ignorado.

- [ ] **Step 5: Commit**

```bash
git add cloudbuild.yaml .gcloudignore
git commit -m "feat: add Cloud Build deploy pipeline for contratos-service"
```

---

## Task 2: Migrar o repositório remoto para `brikzai/ap-back-contratos`

**Files:** nenhum arquivo de código — só configuração de remote git.

- [ ] **Step 1: Confirmar que a working tree está limpa**

Run: `git status --short`
Expected: saída vazia (nada pendente). Se houver algo, parar e perguntar ao usuário antes de prosseguir — não descartar mudanças não commitadas.

- [ ] **Step 2: Trocar a URL do remote `origin`**

Run: `git remote set-url origin https://github.com/brikzai/ap-back-contratos.git`

- [ ] **Step 3: Confirmar a troca**

Run: `git remote -v`
Expected: `origin` aponta para `https://github.com/brikzai/ap-back-contratos.git` (fetch e push).

- [ ] **Step 4: Push do histórico completo**

Run: `git push -u origin master`
Expected: push aceito, branch `master` local rastreando `origin/master` no repo novo. Se o repo novo já tiver commits (não deveria, mas confirme antes de rodar `push --force` — nunca usar `--force` aqui sem confirmar com o usuário primeiro).

Nenhum commit — este task só muda configuração local de remote, não arquivos versionados.

---

## Task 3: APIs do GCP e Artifact Registry

**Files:**
- Create: `docs/runbooks/gcp-setup.md` (seções 0-1, novo arquivo)

- [ ] **Step 1: Confirmar sessão gcloud correta**

Run: `gcloud config get-value project` e `gcloud config get-value account`
Expected: `brikz-ap` e a conta correta (mesma usada para o optin).

- [ ] **Step 2: Habilitar APIs (idempotente — pode já estarem habilitadas pelo optin)**

Run:
```bash
gcloud services enable run.googleapis.com sqladmin.googleapis.com secretmanager.googleapis.com \
  cloudbuild.googleapis.com artifactregistry.googleapis.com iam.googleapis.com \
  cloudresourcemanager.googleapis.com compute.googleapis.com pubsub.googleapis.com \
  cloudscheduler.googleapis.com
```
Expected: comando termina sem erro (silencioso se já habilitadas).

- [ ] **Step 3: Criar repositório Artifact Registry `contratos`**

Run:
```bash
gcloud artifacts repositories create contratos --repository-format=docker --location=southamerica-east1 \
  --description="Imagens do contratos-service"
```

- [ ] **Step 4: Verificar**

Run: `gcloud artifacts repositories describe contratos --location=southamerica-east1`
Expected: repositório `contratos`, formato `DOCKER`, estado ativo.

- [ ] **Step 5: Criar `docs/runbooks/gcp-setup.md` com as seções 0-1**

```markdown
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
```

(Preencher a linha "Feito em ..." com a data real depois de rodar os comandos acima.)

- [ ] **Step 6: Commit**

```bash
git add docs/runbooks/gcp-setup.md
git commit -m "docs: start contratos-service GCP runbook (APIs + Artifact Registry)"
```

---

## Task 4: Service accounts e IAM

**Files:**
- Modify: `docs/runbooks/gcp-setup.md` (adicionar seção 2)

- [ ] **Step 1: Criar as 3 service accounts**

Run:
```bash
gcloud iam service-accounts create contratos-run --display-name="contratos-service runtime (Cloud Run)"
gcloud iam service-accounts create contratos-build --display-name="contratos-service Cloud Build"
gcloud iam service-accounts create contratos-pubsub-push --display-name="contratos-service Pub/Sub push + Scheduler OIDC"
```

- [ ] **Step 2: IAM da service account de runtime**

Run:
```bash
for r in roles/cloudsql.client roles/secretmanager.secretAccessor; do
  gcloud projects add-iam-policy-binding brikz-ap --member=serviceAccount:contratos-run@brikz-ap.iam.gserviceaccount.com --role=$r --condition=None
done
```

- [ ] **Step 3: IAM da service account de build**

Run:
```bash
for r in roles/run.admin roles/artifactregistry.writer roles/logging.logWriter roles/cloudbuild.builds.builder; do
  gcloud projects add-iam-policy-binding brikz-ap --member=serviceAccount:contratos-build@brikz-ap.iam.gserviceaccount.com --role=$r --condition=None
done
gcloud iam service-accounts add-iam-policy-binding contratos-run@brikz-ap.iam.gserviceaccount.com \
  --member=serviceAccount:contratos-build@brikz-ap.iam.gserviceaccount.com --role=roles/iam.serviceAccountUser
```

- [ ] **Step 4: Verificar as 3 contas**

Run: `gcloud iam service-accounts list --filter="email:contratos-*"`
Expected: 3 linhas — `contratos-run@`, `contratos-build@`, `contratos-pubsub-push@`, todas `brikz-ap.iam.gserviceaccount.com`.

- [ ] **Step 5: Adicionar seção 2 ao runbook**

```markdown
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
precisa existir para assinar tokens OIDC (Task 9/10 concedem `run.invoker`
especificamente sobre `contratos-service`, se necessário).
```

- [ ] **Step 6: Commit**

```bash
git add docs/runbooks/gcp-setup.md
git commit -m "docs: add service accounts + IAM section to contratos runbook"
```

---

## Task 5: Banco do tenant na instância `optin-pg`

**Files:**
- Modify: `docs/runbooks/gcp-setup.md` (adicionar seção 3)

**Interfaces:**
- Consumes: instância Cloud SQL `optin-pg` já existente (nenhuma mudança nela).
- Produces: banco `ap_38138785000136_contratos` + usuário `contratos_app`, consumidos pela Task 6 (segredo do tenant) e Task 7 (apply_schema.py).

- [ ] **Step 1: Confirmar a instância existe e pegar o connection name**

Run: `gcloud sql instances describe optin-pg --format="value(connectionName,state)"`
Expected: `brikz-ap:southamerica-east1:optin-pg` e estado `RUNNABLE`.

- [ ] **Step 2: Criar o usuário `contratos_app` (senha gerada localmente, nunca no plano)**

Run:
```bash
PW_CONTRATOS_APP="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
gcloud sql users create contratos_app --instance=optin-pg --password="$PW_CONTRATOS_APP"
```
Guardar `$PW_CONTRATOS_APP` só na memória do terminal — ela vai para o segredo do tenant na Task 6, na mesma sessão de shell (não escrever em arquivo).

- [ ] **Step 3: Criar o banco do primeiro tenant**

Run: `gcloud sql databases create ap_38138785000136_contratos --instance=optin-pg`

- [ ] **Step 4: Conceder privilégios ao usuário no banco novo**

Cloud SQL não expõe `GRANT` via `gcloud` diretamente — conecta via Cloud SQL Proxy/Auth Proxy ou `gcloud sql connect` como usuário admin (`postgres`) e roda o `GRANT` manualmente:

Run: `gcloud sql connect optin-pg --user=postgres --database=ap_38138785000136_contratos`

Dentro do prompt `psql` que abrir:
```sql
GRANT ALL PRIVILEGES ON DATABASE ap_38138785000136_contratos TO contratos_app;
GRANT ALL ON SCHEMA public TO contratos_app;
\q
```

- [ ] **Step 5: Verificar**

Run: `gcloud sql databases list --instance=optin-pg --filter="name:ap_38138785000136_contratos"`
Expected: uma linha, `ap_38138785000136_contratos`.

Run: `gcloud sql users list --instance=optin-pg --filter="name:contratos_app"`
Expected: uma linha, `contratos_app`.

- [ ] **Step 6: Adicionar seção 3 ao runbook**

```markdown
## 3. Banco do tenant (reaproveitando a instância `optin-pg`)

Sem instância nova — `contratos` usa a mesma instância Cloud SQL do
`optin-service` (`optin-pg`, `brikz-ap:southamerica-east1:optin-pg`), com um
usuário e bancos próprios (design 2026-09-04 §2.1): um banco novo por
tenant, `ap_<cnpj>_contratos` — nome deliberadamente diferente de
`ap_<cnpj>` (usado pelo optin para o mesmo tenant) porque as tabelas
`webhook_inbox`, `cerc_requisicao`, `dominio_arranjo` existem nos dois
serviços com o mesmo nome; bancos diferentes na mesma instância são
namespaces Postgres isolados, então não há colisão.

    PW_CONTRATOS_APP="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
    gcloud sql users create contratos_app --instance=optin-pg --password="$PW_CONTRATOS_APP"
    gcloud sql databases create ap_<cnpj>_contratos --instance=optin-pg
    gcloud sql connect optin-pg --user=postgres --database=ap_<cnpj>_contratos
    # dentro do psql:
    #   GRANT ALL PRIVILEGES ON DATABASE ap_<cnpj>_contratos TO contratos_app;
    #   GRANT ALL ON SCHEMA public TO contratos_app;

`contratos_app` é único e reaproveitado por todos os tenants do contratos —
só o banco muda por tenant, mesmo padrão de usuário único do `optin_app`.
Onboarding de um novo tenant repete só `gcloud sql databases create` +
`GRANT` (o usuário já existe depois da primeira vez).
```

- [ ] **Step 7: Commit**

```bash
git add docs/runbooks/gcp-setup.md
git commit -m "docs: add Cloud SQL tenant provisioning section to contratos runbook"
```

---

## Task 6: Segredos estáticos e do primeiro tenant

**Files:**
- Modify: `docs/runbooks/gcp-setup.md` (adicionar seção 4)

**Interfaces:**
- Consumes: `PW_CONTRATOS_APP` da Task 5 (mesma sessão de shell, ou peça pro usuário digitar de novo se a sessão fechou); credenciais CERC já existentes localmente em `.env` (`TENANT_38138785000136_CONFIG_CONTRATOS` — `cerc_client_id`, `cerc_client_secret`, `webhook_basic_user`, `webhook_basic_password`), lidas mas **nunca coladas neste arquivo de plano ou commitadas**.

- [ ] **Step 1: Criar `DJANGO_SECRET_KEY`**

Run:
```bash
python -c 'import secrets; print(secrets.token_urlsafe(50))' \
  | gcloud secrets create DJANGO_SECRET_KEY --data-file=- --replication-policy=user-managed --locations=southamerica-east1
```

- [ ] **Step 2: Montar o JSON do segredo do tenant num arquivo temporário local**

Ler os valores de `cerc_client_id`, `cerc_client_secret`, `webhook_basic_user`, `webhook_basic_password` do `.env` local (chave `TENANT_38138785000136_CONFIG_CONTRATOS`) e montar um arquivo `/tmp/tenant-config.json` (ou no diretório de scratchpad) com:

```json
{
  "cloudsql_connection_name": "brikz-ap:southamerica-east1:optin-pg",
  "cloudsql_db_user": "contratos_app",
  "cloudsql_db_password": "<PW_CONTRATOS_APP da Task 5>",
  "cloudsql_db_name": "ap_38138785000136_contratos",
  "cloudsql_ip_type": "PUBLIC",
  "cerc_client_id": "<valor existente do .env local>",
  "cerc_client_secret": "<valor existente do .env local>",
  "webhook_basic_user": "<valor existente do .env local>",
  "webhook_basic_password": "<valor existente do .env local>"
}
```

Nunca em pipe triplo (`gcloud | python | gcloud`) — no Git Bash/Windows um pipe encadeado pode quebrar silenciosamente e criar um segredo com zero versões, sem erro visível (mesmo aviso do runbook do optin).

- [ ] **Step 3: Criar o segredo a partir do arquivo**

Run:
```bash
gcloud secrets create TENANT_38138785000136_CONFIG_CONTRATOS --data-file=/tmp/tenant-config.json \
  --replication-policy=user-managed --locations=southamerica-east1
rm /tmp/tenant-config.json
```

- [ ] **Step 4: Verificar**

Run: `gcloud secrets versions list TENANT_38138785000136_CONFIG_CONTRATOS`
Expected: versão 1, estado `ENABLED`. Confirmar explicitamente — um segredo com zero versões não dá erro na criação, só falha depois, em runtime.

Run: `gcloud secrets versions list DJANGO_SECRET_KEY`
Expected: versão 1, `ENABLED`.

- [ ] **Step 5: Adicionar seção 4 ao runbook**

```markdown
## 4. Segredos estáticos e por tenant

    python -c 'import secrets; print(secrets.token_urlsafe(50))' \
      | gcloud secrets create DJANGO_SECRET_KEY --data-file=- --replication-policy=user-managed --locations=southamerica-east1

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
```

- [ ] **Step 6: Commit**

```bash
git add docs/runbooks/gcp-setup.md
git commit -m "docs: add secrets provisioning section to contratos runbook"
```

---

## Task 7: Aplicar o schema no banco do tenant

**Files:**
- Nenhum arquivo novo — usa `scripts/apply_schema.py` já existente.
- Modify: `docs/runbooks/gcp-setup.md` (adicionar seção 5)

**Interfaces:**
- Consumes: `scripts/apply_schema.py` (já implementado, lê `CLOUDSQL_CONNECTION_NAME`/`CLOUDSQL_DB_USER`/`CLOUDSQL_DB_PASSWORD`/`CLOUDSQL_DB_NAME` do `.env`), banco criado na Task 5.

- [ ] **Step 1: Apontar temporariamente as env vars pro tenant novo**

Sem editar o `.env` versionado do projeto — exportar as env vars só nesta sessão de shell:
```bash
export CLOUDSQL_CONNECTION_NAME="brikz-ap:southamerica-east1:optin-pg"
export CLOUDSQL_DB_USER="contratos_app"
export CLOUDSQL_DB_PASSWORD="<PW_CONTRATOS_APP da Task 5>"
export CLOUDSQL_DB_NAME="ap_38138785000136_contratos"
```

- [ ] **Step 2: Aplicar o schema base**

Run: `python scripts/apply_schema.py sql/schema/01-contratos-schema.sql`
Expected: saída `Aplicado sql/schema/01-contratos-schema.sql: N statement(s).` (N = número de statements do arquivo).

- [ ] **Step 3: Aplicar as correções**

Run: `python scripts/apply_schema.py sql/schema/02-contratos-schema-fixes.sql`
Expected: saída `Aplicado sql/schema/02-contratos-schema-fixes.sql: N statement(s).` — este arquivo cria a tabela `schema_aplicado`.

- [ ] **Step 4: Verificar idempotência (reaplicar não deve fazer nada)**

Run: `python scripts/apply_schema.py sql/schema/02-contratos-schema-fixes.sql`
Expected: saída `sql/schema/02-contratos-schema-fixes.sql: já aplicado (checksum igual), pulando.`

- [ ] **Step 5: Verificar as tabelas no banco**

Run: `gcloud sql connect optin-pg --user=contratos_app --database=ap_38138785000136_contratos` e, no `psql`: `\dt`
Expected: lista incluindo `contrato`, `garantia`, `webhook_inbox`, `cerc_requisicao`, `dominio_arranjo`, `schema_aplicado`, entre outras do `01-contratos-schema.sql`.

- [ ] **Step 6: Adicionar seção 5 ao runbook**

```markdown
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

Idempotente: reaplicar um arquivo já aplicado (mesmo checksum) é um no-op.
Onboarding de um tenant novo repete estes dois comandos com o `CLOUDSQL_DB_NAME` novo.
```

- [ ] **Step 7: Commit**

```bash
git add docs/runbooks/gcp-setup.md
git commit -m "docs: add schema application section to contratos runbook"
```

---

## Task 8: Primeiro deploy manual (Cloud Run)

**Files:**
- Modify: `docs/runbooks/gcp-setup.md` (adicionar seção 6)

**Interfaces:**
- Consumes: `cloudbuild.yaml` (Task 1), service accounts (Task 4), segredos (Task 6).
- Produces: URL real do `contratos-service`, usada nas Tasks 9-10 (Pub/Sub, Scheduler) e no smoke test (Task 12).

- [ ] **Step 1: Rodar o build manual**

Run: `gcloud builds submit --config cloudbuild.yaml --substitutions=_TAG=$(git rev-parse --short HEAD)`
Expected: `SUCCESS` ao final, ~2-4 minutos (build + push + deploy, sem jobs de migration).

- [ ] **Step 2: Confirmar o serviço no ar**

Run: `gcloud run services describe contratos-service --region southamerica-east1 --format="value(status.url,status.latestReadyRevisionName)"`
Expected: uma URL `https://contratos-service-*.run.app` e o nome da revisão mais recente.

- [ ] **Step 3: Smoke test mínimo (health, sem tenant)**

Run: `curl -s -w "\n%{http_code}\n" "$(gcloud run services describe contratos-service --region southamerica-east1 --format='value(status.url)')/api/v1/health"`
Expected: `200`.

- [ ] **Step 4: Se `/health` retornar 403 do Google Frontend (não da aplicação)**

Isso já aconteceu no optin por causa de Domain Restricted Sharing (`constraints/iam.allowedPolicyMemberDomains`) herdado da organização. Verificar:

Run: `gcloud run services get-iam-policy contratos-service --region southamerica-east1`

Se a policy estiver vazia e `--allow-unauthenticated` não tiver efeito, a exceção de organização já foi criada no projeto pelo optin (`docs/runbooks/gcp-setup.md` do `ap-back-optin`, seção 7) — ela é por **projeto**, não por serviço, então deve valer automaticamente aqui também. Se ainda bloquear, reexecutar o binding manualmente:

Run: `gcloud run services add-iam-policy-binding contratos-service --region southamerica-east1 --member=allUsers --role=roles/run.invoker`

- [ ] **Step 5: Adicionar seção 6 ao runbook**

```markdown
## 6. Primeiro deploy (cloudbuild.yaml)

    gcloud builds submit --config cloudbuild.yaml --substitutions=_TAG=$(git rev-parse --short HEAD)

O que acontece: build → push → `gcloud run deploy contratos-service`. Sem
jobs de migration (diferente do optin) — o schema já foi aplicado
manualmente na Task 5 do plano de implementação antes deste primeiro deploy.

URL do serviço: `gcloud run services describe contratos-service --region southamerica-east1 --format="value(status.url)"`

Se `/api/v1/health` voltar `403` do Google Frontend (não da aplicação): é a
mesma Domain Restricted Sharing já documentada no runbook do optin — a
exceção de organização é por projeto (`brikz-ap`), deve valer automaticamente
aqui. Se não valer, reexecutar:

    gcloud run services add-iam-policy-binding contratos-service --region southamerica-east1 \
      --member=allUsers --role=roles/run.invoker
```

(Preencher com o resultado real — URL, revisão, se precisou do binding manual — depois de rodar.)

- [ ] **Step 6: Commit**

```bash
git add docs/runbooks/gcp-setup.md
git commit -m "docs: record first contratos-service Cloud Run deploy in runbook"
```

---

## Task 9: Tópico e push subscription do Pub/Sub

**Files:**
- Modify: `docs/runbooks/gcp-setup.md` (adicionar seção 7)

**Interfaces:**
- Consumes: URL do `contratos-service` (Task 8), `contratos-pubsub-push@` (Task 4), string de audiência fixa `https://contratos-service.internal/webhooks/contrato/processar` (definida na Task 1, `_PUBSUB_PUSH_AUDIENCE`).
- Produces: tópico `contratos-webhook-inbox` que `shared/pubsub_client.py` já publica por padrão (nenhuma mudança de código necessária).

- [ ] **Step 1: Criar o tópico**

Run: `gcloud pubsub topics create contratos-webhook-inbox`

- [ ] **Step 2: Criar a push subscription**

Run:
```bash
SERVICE_URL="$(gcloud run services describe contratos-service --region southamerica-east1 --format='value(status.url)')"
gcloud pubsub subscriptions create contratos-webhook-inbox-push \
  --topic=contratos-webhook-inbox \
  --push-endpoint="${SERVICE_URL}/api/v1/webhooks/contrato/processar" \
  --push-auth-service-account=contratos-pubsub-push@brikz-ap.iam.gserviceaccount.com \
  --push-auth-token-audience="https://contratos-service.internal/webhooks/contrato/processar" \
  --ack-deadline=30
```

Nota: `--push-auth-token-audience` é fixado como uma string própria (não a URL real do Cloud Run) — precisa bater exatamente com `PUBSUB_PUSH_AUDIENCE` do `cloudbuild.yaml` (Task 1); `--push-endpoint` sim é a URL real, pra onde o Pub/Sub efetivamente manda a requisição HTTP.

- [ ] **Step 3: Verificar**

Run: `gcloud pubsub subscriptions describe contratos-webhook-inbox-push`
Expected: `pushConfig.pushEndpoint` = a URL do serviço + `/api/v1/webhooks/contrato/processar`; `oidcToken.serviceAccountEmail` = `contratos-pubsub-push@brikz-ap.iam.gserviceaccount.com`; `oidcToken.audience` = a string fixa.

- [ ] **Step 4: Teste ponta a ponta do publish (sem depender de um webhook real da CERC)**

Run:
```bash
gcloud pubsub topics publish contratos-webhook-inbox \
  --message='{"webhook_inbox_id": "id-inexistente-de-teste", "financiador_id": "38138785000136"}'
```

Run (alguns segundos depois): `gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="contratos-service"' --limit 20 --format="value(textPayload)"`
Expected: uma linha de log do `[Processor]` — como `webhook_inbox_id` não existe de verdade, o processamento deve logar/retornar sem erro de OIDC (o 401 de OIDC seria o sinal de problema; um erro de "linha não encontrada" é esperado e aceitável neste teste, confirma que a autenticação passou).

- [ ] **Step 5: Adicionar seção 7 ao runbook**

```markdown
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
    gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="contratos-service"' --limit 20
```

- [ ] **Step 6: Commit**

```bash
git add docs/runbooks/gcp-setup.md
git commit -m "docs: add Pub/Sub webhook processing section to contratos runbook"
```

---

## Task 10: Cloud Scheduler — sincronizar-dominio-arranjo

**Files:**
- Modify: `docs/runbooks/gcp-setup.md` (adicionar seção 8)

**Interfaces:**
- Consumes: mesma service account `contratos-pubsub-push@` e mesma audiência fixa da Task 9 (constraint global: `PUBSUB_PUSH_INVOKER_SA`/`PUBSUB_PUSH_AUDIENCE` são valores únicos, compartilhados por Pub/Sub e Scheduler).

- [ ] **Step 1: Criar o job diário**

Run:
```bash
SERVICE_URL="$(gcloud run services describe contratos-service --region southamerica-east1 --format='value(status.url)')"
gcloud scheduler jobs create http contratos-sincronizar-dominio-arranjo \
  --location=southamerica-east1 \
  --schedule="0 6 * * *" \
  --uri="${SERVICE_URL}/api/v1/jobs/sincronizar-dominio-arranjo" \
  --http-method=POST \
  --oidc-service-account-email=contratos-pubsub-push@brikz-ap.iam.gserviceaccount.com \
  --oidc-token-audience="https://contratos-service.internal/webhooks/contrato/processar"
```

`--schedule="0 6 * * *"` = todo dia às 06:00 (fuso do App Engine do projeto, confirmar com `gcloud app describe --format="value(locationId)"` se o horário vier diferente do esperado).

- [ ] **Step 2: Verificar**

Run: `gcloud scheduler jobs describe contratos-sincronizar-dominio-arranjo --location=southamerica-east1`
Expected: `state: ENABLED`, `httpTarget.oidcToken.serviceAccountEmail` = `contratos-pubsub-push@...`, `httpTarget.oidcToken.audience` = a mesma string fixa da Task 9.

- [ ] **Step 3: Disparar manualmente pra testar**

Run: `gcloud scheduler jobs run contratos-sincronizar-dominio-arranjo --location=southamerica-east1`

Run (alguns segundos depois): `gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="contratos-service"' --limit 20 --format="value(textPayload)"`
Expected: log de `sincronizar_arranjos` para o tenant `12345678000199` (único em `_TENANTS_JOBS_PERIODICOS` hoje) — sem `401`/erro de OIDC.

- [ ] **Step 4: Adicionar seção 8 ao runbook**

```markdown
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
lista hardcoded no código-fonte — hoje só o CNPJ de dev. Onboardar um tenant
real pro job diário exige adicionar o CNPJ nessa lista e reimplantar, não é
configuração via secret/env var.

Testar manualmente: `gcloud scheduler jobs run contratos-sincronizar-dominio-arranjo --location=southamerica-east1`
```

- [ ] **Step 5: Commit**

```bash
git add docs/runbooks/gcp-setup.md
git commit -m "docs: add Cloud Scheduler section to contratos runbook"
```

---

## Task 11: Trigger automático do Cloud Build

**Files:**
- Modify: `docs/runbooks/gcp-setup.md` (adicionar seção 5b)

**Interfaces:**
- Consumes: repositório `github.com/brikzai/ap-back-contratos` (Task 2), `contratos-build@` (Task 4).

- [ ] **Step 1: Verificar se já existe uma conexão GitHub reaproveitável do optin**

Run: `gcloud builds connections list --region=southamerica-east1`
Expected: possivelmente já existe `optin-github` (2ª geração) — se o GitHub App já está instalado na org `brikzai` e cobre o repo `ap-back-contratos`, pode reaproveitar a mesma conexão em vez de criar uma nova. Confirmar cobertura:

Run: `gcloud builds connections describe optin-github --region=southamerica-east1`

- [ ] **Step 2: Se necessário, autorizar o GitHub App a acessar `ap-back-contratos`**

Isso é feito pela interface do GitHub (Settings → Installed GitHub Apps → o app do Cloud Build → Repository access → adicionar `ap-back-contratos`), não via `gcloud` — é uma ação manual do usuário, não automatizável por este plano. Pausar aqui e pedir confirmação de que o acesso foi concedido antes do próximo passo.

- [ ] **Step 3: Registrar o repositório no Cloud Build (se ainda não estiver)**

Run:
```bash
gcloud builds repositories create contratos-back \
  --connection=optin-github --region=southamerica-east1 \
  --remote-uri=https://github.com/brikzai/ap-back-contratos.git
```

- [ ] **Step 4: Criar o trigger**

Run:
```bash
gcloud builds triggers create github \
  --name=contratos-deploy-master \
  --region=southamerica-east1 \
  --repository=projects/brikz-ap/locations/southamerica-east1/connections/optin-github/repositories/contratos-back \
  --branch-pattern='^master$' \
  --build-config=cloudbuild.yaml \
  --substitutions=_TAG='$SHORT_SHA' \
  --service-account=projects/brikz-ap/serviceAccounts/contratos-build@brikz-ap.iam.gserviceaccount.com
```

- [ ] **Step 5: Verificar**

Run: `gcloud builds triggers describe contratos-deploy-master --region=southamerica-east1`
Expected: `github.push.branch: ^master$`, `filename: cloudbuild.yaml`, `approvalConfig: {}` (sem aprovação manual, mesmo padrão do optin).

- [ ] **Step 6: Testar com um push real (pode ser este próprio commit da documentação)**

Run: `git push origin master` (depois de commitar a seção 5b abaixo)

Run (após alguns segundos): `gcloud builds list --region=southamerica-east1 --limit=3`
Expected: um build novo, disparado pelo trigger, `STATUS: SUCCESS` (ou `WORKING` se ainda rodando).

- [ ] **Step 7: Adicionar seção 5b ao runbook**

```markdown
## 5b. Deploy automático (Cloud Build trigger)

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
GitHub App precisou ser autorizado manualmente (pela interface do GitHub)
a acessar também o repositório `brikzai/ap-back-contratos`, além do
`ap-optin-back`. Sem aprovação manual de build (`approvalConfig: {}`), mesmo
padrão do optin: qualquer push na `master` vai para o ar sozinho.

Acompanhar: `gcloud builds list --region=southamerica-east1 --limit=5`
```

- [ ] **Step 8: Commit e push (dispara o próprio trigger recém-criado)**

```bash
git add docs/runbooks/gcp-setup.md
git commit -m "docs: add automatic Cloud Build trigger section to contratos runbook"
git push origin master
```

---

## Task 12: Smoke test completo e fechamento do runbook

**Files:**
- Modify: `docs/runbooks/gcp-setup.md` (adicionar seção 9)

**Interfaces:**
- Consumes: tudo das Tasks 1-11.

- [ ] **Step 1: Health check**

Run: `curl -s -w "\n%{http_code}\n" "$(gcloud run services describe contratos-service --region southamerica-east1 --format='value(status.url)')/api/v1/health"`
Expected: `200`.

- [ ] **Step 2: Listar contratos do tenant recém-provisionado (banco vazio)**

Run: `curl -s -w "\n%{http_code}\n" "$(gcloud run services describe contratos-service --region southamerica-east1 --format='value(status.url)')/api/v1/contratos/38138785000136"`
Expected: `200`, corpo `{"dados": []}`.

- [ ] **Step 3: Webhook da CERC com Basic Auth válido (simulado, evento mínimo)**

```bash
SERVICE_URL="$(gcloud run services describe contratos-service --region southamerica-east1 --format='value(status.url)')"
WEBHOOK_USER="<webhook_basic_user do tenant, do .env local>"
WEBHOOK_PASS="<webhook_basic_password do tenant, do .env local>"
curl -s -w "\n%{http_code}\n" -u "$WEBHOOK_USER:$WEBHOOK_PASS" \
  -X POST "$SERVICE_URL/api/v1/webhooks/contrato/38138785000136" \
  -H "Content-Type: application/json" \
  -d '{"tipoEvento": "contrato", "dataHoraEvento": "2026-09-04T12:00:00.000Z", "evento": {"referenciaExterna": "smoke-test-001", "protocolo": "smoke-001", "status": "REGISTRADO"}}'
```
Expected: `202` (aceito — grava em `webhook_inbox` e publica no Pub/Sub, mesmo sem um `contrato` correspondente já existir).

- [ ] **Step 4: Webhook sem Basic Auth (deve rejeitar)**

Run: `curl -s -w "\n%{http_code}\n" -X POST "$SERVICE_URL/api/v1/webhooks/contrato/38138785000136" -d '{}'`
Expected: `401` — confirma que a autenticação da aplicação está ativa (não é o Google Frontend bloqueando; a mensagem de erro no corpo deve vir da aplicação, `{"erro": "autenticação inválida"}`).

- [ ] **Step 5: Adicionar seção 9 (smoke test) e fechar o runbook**

```markdown
## 9. Smoke test pós-deploy

    URL=$(gcloud run services describe contratos-service --region southamerica-east1 --format="value(status.url)")
    curl -s -w "\n%{http_code}\n" "$URL/api/v1/health"                                              # 200
    curl -s -w "\n%{http_code}\n" "$URL/api/v1/contratos/<cnpj>"                                     # 200, {"dados": []}
    curl -s -w "\n%{http_code}\n" -u "<webhook_basic_user>:<webhook_basic_password>" \
      -X POST "$URL/api/v1/webhooks/contrato/<cnpj>" -H "Content-Type: application/json" \
      -d '{"tipoEvento": "contrato", "dataHoraEvento": "...", "evento": {...}}'                      # 202
    curl -s -w "\n%{http_code}\n" -X POST "$URL/api/v1/webhooks/contrato/<cnpj>" -d '{}'              # 401 (da aplicação)

## 10. Pendências conhecidas (não resolvidas por este runbook)

- **Rotas `/api/v1/contratos/<financiador_id>` sem autenticação de aplicação.**
  O serviço é público (`--allow-unauthenticated`, necessário pro webhook da
  CERC) e essas rotas não checam nada — qualquer um que souber um
  `financiador_id` lê/escreve contratos. Ver design 2026-09-04 §2.2/§6.
- **Registro do webhook na CERC:** pedir à CERC o cadastro de
  `tipoEvento=contrato` apontando pra
  `<URL do contratos-service>/api/v1/webhooks/contrato/<cnpj>`, por tenant —
  não reaproveita o webhook de agenda/UR do `ap-back-consulta-agenda`
  (evento diferente). Sem isso, o serviço nunca recebe webhooks reais da
  CERC, mesmo com toda a infra no ar.
- **`_TENANTS_JOBS_PERIODICOS` hardcoded** — onboarding de tenant pro job
  diário exige mudança de código + redeploy.
- **Dev local não migrado** — `.env` local continua na instância antiga
  (`registradora-506000:contratos-db`); fora do escopo deste plano.
```

- [ ] **Step 6: Commit final**

```bash
git add docs/runbooks/gcp-setup.md
git commit -m "docs: complete contratos-service GCP runbook (smoke test + known pendencies)"
git push origin master
```

---

## Self-Review

**Cobertura da spec:** §2.1 (Cloud SQL) → Task 5; §2.2 (Cloud Run público) → Task 1/8; §2.3 (deploy automático) → Task 1/2/11; §3 (tabela de recursos) → Tasks 3-11 cobrem cada linha; §4 (fluxo de dados) → validado no smoke test (Task 12); §5 (onboarding de tenant) → Tasks 5-7 executam o procedimento pro primeiro tenant e o runbook generaliza pros próximos; §6 (riscos) → todos os 6 itens aparecem documentados na seção 10 do runbook ou nas notas de cada task; §7 (verificação) → Task 12.

**Placeholders:** nenhum "TBD"/"implementar depois" — os únicos `<valores>` entre `<>` no plano são dados que só existem em tempo de execução (senha gerada, URL do Cloud Run) ou segredos que já existem localmente e não devem ser embutidos no plano (CERC client secret, webhook basic auth) — isso é deliberado, não um placeholder de conteúdo faltando.

**Consistência:** nomes de recursos (`contratos-run@`, `contratos-build@`, `contratos-pubsub-push@`, `contratos-service`, `contratos-webhook-inbox`, `ap_38138785000136_contratos`) usados de forma idêntica em todas as tasks que os referenciam; a string de audiência OIDC (`https://contratos-service.internal/webhooks/contrato/processar`) é a mesma no `cloudbuild.yaml` (Task 1), na subscription (Task 9) e no Scheduler (Task 10).
