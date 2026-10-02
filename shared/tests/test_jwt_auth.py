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
    monkeypatch.setenv("IAM_JWT_PUBLIC_KEY_BRIKZ_IAM", publica)
    monkeypatch.setenv("IAM_JWT_ISSUER", ISSUER)
    return privada


def _token(privada, **overrides):
    agora = int(time.time())
    claims = {
        "iss": ISSUER, "type": "access", "sub": "teste", "iat": agora,
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
    monkeypatch.delenv("IAM_JWT_PUBLIC_KEY_BRIKZ_IAM", raising=False)

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
    assert resposta_sem_header.status_code# --- Chaves aceitas: IAM real, lista de rotação e homolog com opt-in ------
#
# IAM_JWT_PUBLIC_KEY_BRIKZ_IAM é a chave do IAM real (fixture base acima).
# IAM_JWT_PUBLIC_KEY é a chave LOCAL de homolog (gerar_jwt.py): só vale com
# IAM_JWT_ACEITAR_CHAVE_HOMOLOG=true e ENVIRONMENT != production.

def _novo_par_rsa():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


@pytest.fixture
def chave_homolog(monkeypatch, chaves):
    private_pem, public_pem = _novo_par_rsa()
    monkeypatch.setenv("IAM_JWT_PUBLIC_KEY", public_pem)
    monkeypatch.delenv("IAM_JWT_ACEITAR_CHAVE_HOMOLOG", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    return private_pem


def _view_ok():
    from shared.jwt_auth import jwt_required

    @jwt_required
    def view(request):
        return JsonResponse({"financiador_id": request.financiador_id})

    return view


def test_refresh_token_e_rejeitado(chaves):
    from shared.jwt_auth import JwtAuthError, validar_bearer_token

    with pytest.raises(JwtAuthError, match="access"):
        validar_bearer_token(f"Bearer {_token(chaves, type='refresh')}")


def test_token_sem_type_e_rejeitado(chaves):
    from shared.jwt_auth import JwtAuthError, validar_bearer_token

    claims = {k: v for k, v in pyjwt.decode(
        _token(chaves), options={"verify_signature": False}).items() if k != "type"}
    token = pyjwt.encode(claims, chaves, algorithm="RS256")
    with pytest.raises(JwtAuthError):
        validar_bearer_token(f"Bearer {token}")


def test_token_sem_sub_e_rejeitado(chaves):
    from shared.jwt_auth import JwtAuthError, validar_bearer_token

    claims = {k: v for k, v in pyjwt.decode(
        _token(chaves), options={"verify_signature": False}).items() if k != "sub"}
    token = pyjwt.encode(claims, chaves, algorithm="RS256")
    with pytest.raises(JwtAuthError):
        validar_bearer_token(f"Bearer {token}")


def test_token_com_sub_vazio_e_rejeitado(chaves):
    from shared.jwt_auth import JwtAuthError, validar_bearer_token

    with pytest.raises(JwtAuthError, match="sub"):
        validar_bearer_token(f"Bearer {_token(chaves, sub='  ')}")


def test_jwt_required_devolve_401_para_refresh_token(chaves):
    token = _token(chaves, type="refresh")
    response = _view_ok()(RequestFactory().get("/x", HTTP_AUTHORIZATION=f"Bearer {token}"))
    assert response.status_code == 401


def test_chave_homolog_rejeitada_sem_opt_in(chave_homolog):
    from shared.jwt_auth import JwtAuthError, validar_bearer_token

    with pytest.raises(JwtAuthError):
        validar_bearer_token(f"Bearer {_token(chave_homolog)}")


def test_chave_homolog_aceita_com_opt_in_fora_de_producao(chave_homolog, monkeypatch):
    from shared.jwt_auth import validar_bearer_token

    monkeypatch.setenv("IAM_JWT_ACEITAR_CHAVE_HOMOLOG", "true")
    monkeypatch.setenv("ENVIRONMENT", "homolog")
    claims = validar_bearer_token(f"Bearer {_token(chave_homolog)}")
    assert claims["financiador_id"] == FINANCIADOR


def test_chave_homolog_rejeitada_em_producao_mesmo_com_opt_in(chave_homolog, monkeypatch):
    monkeypatch.setenv("IAM_JWT_ACEITAR_CHAVE_HOMOLOG", "true")
    monkeypatch.setenv("ENVIRONMENT", "production")
    token = _token(chave_homolog)
    response = _view_ok()(RequestFactory().get("/x", HTTP_AUTHORIZATION=f"Bearer {token}"))
    assert response.status_code == 401


def test_chave_do_iam_continua_aceita_com_homolog_configurada(chave_homolog, chaves):
    from shared.jwt_auth import validar_bearer_token

    claims = validar_bearer_token(f"Bearer {_token(chaves)}")
    assert claims["financiador_id"] == FINANCIADOR


def test_lista_de_chaves_aceita_qualquer_uma_para_rotacao(monkeypatch, chaves):
    from shared.jwt_auth import validar_bearer_token

    antiga_priv, antiga_pub = _novo_par_rsa()
    nova_priv, nova_pub = _novo_par_rsa()
    # Formato do Secret Manager: PEM concatenadas numa linha, \n literais.
    monkeypatch.setenv("IAM_JWT_PUBLIC_KEYS", (antiga_pub + nova_pub).replace("\n", "\\n"))
    monkeypatch.delenv("IAM_JWT_PUBLIC_KEY_BRIKZ_IAM", raising=False)
    for privada in (antiga_priv, nova_priv):
        claims = validar_bearer_token(f"Bearer {_token(privada)}")
        assert claims["financiador_id"] == FINANCIADOR


def test_rejeita_token_assinado_por_chave_desconhecida(chaves):
    terceira_priv, _ = _novo_par_rsa()
    token = _token(terceira_priv)
    response = _view_ok()(RequestFactory().get("/x", HTTP_AUTHORIZATION=f"Bearer {token}"))
    assert response.status_code == 401


def test_token_expirado_de_outra_chave_da_lista_diz_expirado(monkeypatch, chaves):
    from shared.jwt_auth import JwtAuthError, validar_bearer_token

    nova_priv, nova_pub = _novo_par_rsa()
    monkeypatch.setenv("IAM_JWT_PUBLIC_KEYS", nova_pub)
    expirado = _token(nova_priv, exp=int(time.time()) - 10)
    with pytest.raises(JwtAuthError, match="expirado"):
        validar_bearer_token(f"Bearer {expirado}")


def test_sem_nenhuma_chave_configurada_levanta_keyerror(monkeypatch, chaves):
    from shared.jwt_auth import validar_bearer_token

    for nome in ("IAM_JWT_PUBLIC_KEY_BRIKZ_IAM", "IAM_JWT_PUBLIC_KEYS", "IAM_JWT_PUBLIC_KEY"):
        monkeypatch.delenv(nome, raising=False)
    with pytest.raises(KeyError):
        validar_bearer_token(f"Bearer {_token(chaves)}")
