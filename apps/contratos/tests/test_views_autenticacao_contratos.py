"""Autenticação das rotas de /contratos/<financiador_id>.

Nenhum destes testes toca o banco: a recusa (401/403) acontece antes de
qualquer get_db — é justamente isso que se quer garantir.
"""
import pytest
from django.test import Client

from apps.contratos.tests.auth_teste import FINANCIADOR_TESTE, cliente_autenticado, token_de_acesso

CONTRATO_ID = "00000000-0000-0000-0000-000000000000"
OUTRO_FINANCIADOR = "99999999000191"

ROTAS = [
    ("get", f"/api/v1/contratos/{FINANCIADOR_TESTE}"),
    ("post", f"/api/v1/contratos/{FINANCIADOR_TESTE}"),
    ("get", f"/api/v1/contratos/{FINANCIADOR_TESTE}/{CONTRATO_ID}"),
    ("get", f"/api/v1/contratos/{FINANCIADOR_TESTE}/{CONTRATO_ID}/eventos"),
    ("post", f"/api/v1/contratos/{FINANCIADOR_TESTE}/inativar"),
    ("post", f"/api/v1/contratos/{FINANCIADOR_TESTE}/baixar"),
]


def _chamar(cliente, metodo, url):
    if metodo == "post":
        return cliente.post(url, data="{}", content_type="application/json")
    return cliente.get(url)


@pytest.mark.parametrize("metodo,url", ROTAS)
def test_rota_sem_token_retorna_401(metodo, url):
    response = _chamar(Client(), metodo, url)
    assert response.status_code == 401
    assert response.json()["erro"] == "NAO_AUTENTICADO"


@pytest.mark.parametrize("metodo,url", ROTAS)
def test_rota_com_refresh_token_retorna_401(metodo, url):
    token = token_de_acesso(type="refresh")
    response = _chamar(Client(HTTP_AUTHORIZATION=f"Bearer {token}"), metodo, url)
    assert response.status_code == 401


@pytest.mark.parametrize("metodo,url", ROTAS)
def test_rota_com_token_de_outro_financiador_retorna_403(metodo, url):
    response = _chamar(cliente_autenticado(OUTRO_FINANCIADOR), metodo, url)
    assert response.status_code == 403
    assert response.json()["erro"] == "FINANCIADOR_DIVERGENTE"


@pytest.mark.parametrize("url", [
    f"/api/v1/contratos/{FINANCIADOR_TESTE}/inativar",
    f"/api/v1/contratos/{FINANCIADOR_TESTE}/baixar",
    f"/api/v1/contratos/{FINANCIADOR_TESTE}/{CONTRATO_ID}",
])
def test_metodo_errado_sem_token_retorna_401_e_nao_405(url):
    # A recusa por método não conta ao anônimo quais verbos a rota atende.
    response = Client().put(url, data="{}", content_type="application/json")
    assert response.status_code == 401


def test_health_e_webhook_continuam_sem_jwt():
    assert Client().get("/api/v1/health").status_code == 200
    # Webhook da CERC: autenticado por Basic Auth (não JWT) — sem credencial
    # é 401 do próprio Basic Auth, e não do jwt_auth.
    response = Client().post(
        f"/api/v1/webhooks/contrato/{FINANCIADOR_TESTE}", data="{}", content_type="application/json"
    )
    assert response.status_code == 401
    assert response.json().get("erro") != "NAO_AUTENTICADO"
