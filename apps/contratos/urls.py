from django.urls import path, re_path
from . import views

# Autenticação: todas as rotas de /contratos/<financiador_id> (listar, criar,
# detalhar, eventos, inativar, baixar) exigem JWT do IAM (@jwt_required) e
# recusam com 403 financiador da URL diferente do claim. Antes só /eventos era
# protegida; as demais ficavam abertas com o serviço público no Cloud Run
# (pendência registrada em docs/superpowers/specs/2026-09-04-deploy-gcp-
# contratos-design.md §2.2). Seguem sem JWT, de propósito: health, o webhook
# da CERC (Basic Auth por tenant), o push do Pub/Sub e o job do Scheduler
# (OIDC do Google).
urlpatterns = [
    path("health", views.health),
    re_path(r"^webhooks/contrato/(?P<financiador_id>\d{14})$", views.webhook_contrato),
    path("webhooks/contrato/processar", views.processar_webhook_contrato),
    re_path(r"^contratos/(?P<financiador_id>\d{14})$", views.contratos),
    # Ordem não é estritamente necessária aqui — o padrão de detalhar_contrato
    # é ancorado em $ e [0-9a-f-] não casa "/", então não engoliria o sufixo
    # /eventos mesmo se viesse depois. Mantido antes por convenção (rota mais
    # específica primeiro), não por necessidade.
    re_path(r"^contratos/(?P<financiador_id>\d{14})/(?P<contrato_id>[0-9a-f-]{36})/eventos$", views.eventos_contrato),
    re_path(r"^contratos/(?P<financiador_id>\d{14})/(?P<contrato_id>[0-9a-f-]{36})$", views.detalhar_contrato),
    re_path(r"^contratos/(?P<financiador_id>\d{14})/inativar$", views.inativar_contrato),
    re_path(r"^contratos/(?P<financiador_id>\d{14})/baixar$", views.baixar_contrato),
    path("jobs/sincronizar-dominio-arranjo", views.sincronizar_dominio_arranjo),
]
