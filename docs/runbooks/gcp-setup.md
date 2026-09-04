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
