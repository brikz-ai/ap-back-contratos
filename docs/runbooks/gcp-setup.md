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
