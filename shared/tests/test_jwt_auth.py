"""Testes do jwt_auth portado do agenda-service. Par RSA gerado em memória:
sem rede, sem banco, sem segredo de ambiente."""
import json
import time

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.http import JsonResponse
from django.test import RequestFactory

from shared.jwt_auth import JwtAuthError, jwt_required, validar_bearer_token

ISSUER = "brikz-iam"
FINANCIADOR = "12345678000199"


@pytest.fixture
def chaves(monkeypatch):
    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    privada = chave.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    publica = chave.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    monkeypatch.setenv("IAM_JWT_PUBLIC_KEY", publica)
    monkeypatch.setenv("IAM_JWT_ISSUER", ISSUER)
    return privada


def _token(privada, **overrides):
    agora = int(time.time())
    claims = {
        "iss": ISSUER, "sub": "teste", "iat": agora,
        "exp": agora + 3600, "financiador_id": FINANCIADOR,
    }
    claims.update(overrides)
    return pyjwt.encode(claims, privada, algorithm="RS256")


def test_token_valido_devolve_claims(chaves):
    claims = validar_bearer_token(f"Bearer {_token(chaves)}")
    assert claims["financiador_id"] == FINANCIADOR


def test_header_ausente_levanta(chaves):
    with pytest.raises(JwtAuthError):
        validar_bearer_token("")


def test_token_expirado_levanta(chaves):
    with pytest.raises(JwtAuthError, match="expirado"):
        validar_bearer_token(f"Bearer {_token(chaves, exp=int(time.time()) - 10)}")


def test_emissor_errado_levanta(chaves):
    with pytest.raises(JwtAuthError):
        validar_bearer_token(f"Bearer {_token(chaves, iss='outro-idp')}")


def test_decorador_recusa_sem_header(chaves):
    @jwt_required
    def view(request):
        return JsonResponse({"ok": True})

    resposta = view(RequestFactory().get("/x"))
    assert resposta.status_code == 401


def test_decorador_recusa_financiador_id_malformado(chaves):
    @jwt_required
    def view(request):
        return JsonResponse({"ok": True})

    token = _token(chaves, financiador_id="123")
    resposta = view(RequestFactory().get("/x", HTTP_AUTHORIZATION=f"Bearer {token}"))
    assert resposta.status_code == 401


def test_decorador_expoe_financiador_id_na_request(chaves):
    capturado = {}

    @jwt_required
    def view(request):
        capturado["financiador_id"] = request.financiador_id
        return JsonResponse({"ok": True})

    token = _token(chaves)
    resposta = view(RequestFactory().get("/x", HTTP_AUTHORIZATION=f"Bearer {token}"))
    assert resposta.status_code == 200
    assert capturado["financiador_id"] == FINANCIADOR


def test_decorador_converte_financiador_id_numerico_para_str(chaves):
    # Um IdP que emita o claim como número JSON (sem aspas) ainda precisa
    # resultar num request.financiador_id comparável por `==` com o valor
    # (sempre string) vindo da URL — só checar o valor sem checar o tipo não
    # fecharia o caso: `12345678000199 == "12345678000199"` é False em Python.
    capturado = {}

    @jwt_required
    def view(request):
        capturado["financiador_id"] = request.financiador_id
        return JsonResponse({"ok": True})

    token = _token(chaves, financiador_id=int(FINANCIADOR))
    resposta = view(RequestFactory().get("/x", HTTP_AUTHORIZATION=f"Bearer {token}"))
    assert resposta.status_code == 200
    assert capturado["financiador_id"] == FINANCIADOR
    assert isinstance(capturado["financiador_id"], str)


def test_decorador_devolve_503_sem_vazar_detalhe_quando_public_key_ausente(chaves, monkeypatch):
    monkeypatch.delenv("IAM_JWT_PUBLIC_KEY", raising=False)

    @jwt_required
    def view(request):
        return JsonResponse({"ok": True})

    resposta = view(RequestFactory().get("/x", HTTP_AUTHORIZATION="Bearer qualquer-coisa"))
    assert resposta.status_code == 503
    # Corpo exato — sem stack trace, sem nome de variável de ambiente, sem
    # detalhe interno. O serviço está mal configurado; isso não é erro do
    # cliente nem informação que ele deva receber.
    assert json.loads(resposta.content) == {"erro": "SERVICO_MAL_CONFIGURADO"}

    # Sem header, a ausência de CREDENCIAL DO CLIENTE é o que pesa: 401, não
    # 503 — a validação do header acontece antes de qualquer leitura de
    # variável de ambiente, então a má configuração do servidor nem chega a
    # ser alcançada.
    resposta_sem_header = view(RequestFactory().get("/x"))
    assert resposta_sem_header.status_code == 401


def test_decorador_devolve_503_sem_vazar_detalhe_quando_issuer_ausente(chaves, monkeypatch):
    monkeypatch.delenv("IAM_JWT_ISSUER", raising=False)

    @jwt_required
    def view(request):
        return JsonResponse({"ok": True})

    resposta = view(RequestFactory().get("/x", HTTP_AUTHORIZATION="Bearer qualquer-coisa"))
    assert resposta.status_code == 503
    assert json.loads(resposta.content) == {"erro": "SERVICO_MAL_CONFIGURADO"}

    resposta_sem_header = view(RequestFactory().get("/x"))
    assert resposta_sem_header.status_code == 401
