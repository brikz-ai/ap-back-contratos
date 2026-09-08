from django.urls import path, re_path
from . import views

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
