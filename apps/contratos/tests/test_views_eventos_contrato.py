"""Testes do GET /contratos/<fin>/<id>/eventos. Diferente das outras rotas
de leitura, esta exige JWT: o corpo carrega request/response crus da CERC,
que incluem ISPB, agência e conta do domicílio de pagamento."""
import time
import uuid

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import Client

from apps.contratos import state_machine
from apps.contratos.contrato_repository import inserir_contrato_criado
from shared.cloudsql_client import get_db

FINANCIADOR_TESTE = "12345678000199"
OUTRO_FINANCIADOR = "98765432000188"
ISSUER = "brikz-iam"
UUID_INEXISTENTE = "00000000-0000-0000-0000-000000000000"


@pytest.fixture
def chave_privada(monkeypatch):
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


def _auth(privada, financiador_id=FINANCIADOR_TESTE):
    agora = int(time.time())
    token = pyjwt.encode(
        {"iss": ISSUER, "type": "access", "sub": "teste", "iat": agora,
         "exp": agora + 3600, "financiador_id": financiador_id},
        privada, algorithm="RS256",
    )
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def _payload_minimo(referencia_externa):
    return {
        "referenciaExterna": referencia_externa,
        "identificadorContrato": "OP-TESTE-EVENTOS",
        "documentoContratante": "22751826000125",
        "cnpjDetentor": FINANCIADOR_TESTE,
        "tipoEfeito": "2",
        "saldoDevedor": 150000.00,
        "limiteOperacaoGarantida": 200000.00,
        "valorMantido": 180000.00,
        "dataAssinatura": "2026-08-15",
        "dataVencimento": "2027-08-15",
        "identificacaoGestaoEntidadeRegistradora": "2",
        "modalidadeOperacao": "1",
        "repactuacao": "0",
        "garantias": [], "identificacaoContratosAnteriores": [], "parcelas": [],
    }


def _limpar(referencia_externa):
    db = get_db(FINANCIADOR_TESTE)
    db.table("cerc_requisicao").delete().eq("correlacao_id", referencia_externa).execute()
    for row in db.table("contrato").select("id").eq("referencia_externa", referencia_externa).execute().data:
        db.table("contrato_evento").delete().eq("contrato_id", row["id"]).execute()
        db.table("contrato").delete().eq("id", row["id"]).execute()


def _criar(referencia):
    _limpar(referencia)
    return inserir_contrato_criado(
        FINANCIADOR_TESTE, _payload_minimo(referencia),
        status=state_machine.AGUARDANDO_WEBHOOK,
        protocolo=f"proto-{referencia}", id_contrato_cerc=f"cerc-{referencia}",
    )


def test_sem_jwt_devolve_401(chave_privada):
    url = f"/api/v1/contratos/{FINANCIADOR_TESTE}/{UUID_INEXISTENTE}/eventos"
    assert Client().get(url).status_code == 401


def test_financiador_do_claim_divergente_devolve_403(chave_privada):
    url = f"/api/v1/contratos/{FINANCIADOR_TESTE}/{UUID_INEXISTENTE}/eventos"
    resposta = Client().get(url, **_auth(chave_privada, financiador_id=OUTRO_FINANCIADOR))
    assert resposta.status_code == 403
    # Formato das recusas de shared/jwt_auth.py: `erro` é código curto (o front
    # o usa como código), `mensagem` é o texto legível.
    assert resposta.json()["erro"] == "FINANCIADOR_DIVERGENTE"
    assert resposta.json()["mensagem"]


def test_contrato_inexistente_devolve_404(chave_privada):
    url = f"/api/v1/contratos/{FINANCIADOR_TESTE}/{UUID_INEXISTENTE}/eventos"
    resposta = Client().get(url, **_auth(chave_privada))
    assert resposta.status_code == 404
    # A view tem dois caminhos para 404 (financiador não resolvível vs.
    # contrato inexistente) — sem asserir o corpo, este teste passaria pelo
    # caminho errado (o except genérico) e continuaria verde mesmo que o
    # tratamento de `eventos is None` fosse removido.
    assert resposta.json()["erro"] == "contrato não encontrado"


def test_devolve_timeline_em_camelcase(chave_privada):
    referencia = "CTR-EVENTOS-VIEW"
    contrato = _criar(referencia)
    db = get_db(FINANCIADOR_TESTE)
    try:
        db.table("contrato_evento").insert({
            "contrato_id": contrato["id"], "tipo": "rejeicao_estrutural",
            "payload": {"erros": [{"codigo": "C07", "mensagem": "detentor inválido"}]},
            "ocorrido_em": "2026-09-01T12:01:00+00:00",
        }).execute()

        url = f"/api/v1/contratos/{FINANCIADOR_TESTE}/{contrato['id']}/eventos"
        resposta = Client().get(url, **_auth(chave_privada))

        assert resposta.status_code == 200
        dados = resposta.json()["dados"]
        assert dados[0]["tipo"] == "rejeicao_estrutural"
        assert "ocorridoEm" in dados[0]
        assert dados[0]["payload"]["erros"][0]["codigo"] == "C07"
        assert dados[0]["requisicoes"] == []
    finally:
        _limpar(referencia)


def test_devolve_requisicoes_cerc_em_camelcase(chave_privada):
    """Único teste que exercita _requisicao_para_dto: os seis campos aqui
    (requestBody/responseBody em particular) são os que carregam ISPB,
    agência e conta do domicílio de pagamento — a razão desta rota ser
    autenticada. Sem contrato_evento algum, a requisição vira "órfã" em
    listar_eventos_do_contrato e ganha sua própria entrada sintética na
    timeline (tipo="requisicao_cerc")."""
    referencia = "CTR-EVENTOS-REQ"
    contrato = _criar(referencia)
    db = get_db(FINANCIADOR_TESTE)
    try:
        db.table("cerc_requisicao").insert({
            "id": str(uuid.uuid4()),
            "recurso": "/v15/contratos",
            "correlacao_id": referencia,
            "http_status": 207,
            "request_body": {"referenciaExterna": referencia},
            "response_body": {"status": "0"},
            "tentativa": 1,
        }).execute()

        url = f"/api/v1/contratos/{FINANCIADOR_TESTE}/{contrato['id']}/eventos"
        resposta = Client().get(url, **_auth(chave_privada))

        assert resposta.status_code == 200
        dados = resposta.json()["dados"]
        assert len(dados) == 1
        requisicoes = dados[0]["requisicoes"]
        assert len(requisicoes) == 1
        req = requisicoes[0]
        assert req["recurso"] == "/v15/contratos"
        assert req["httpStatus"] == 207
        assert req["tentativa"] == 1
        assert req["requestBody"] == {"referenciaExterna": referencia}
        assert req["responseBody"] == {"status": "0"}
        assert "criadoEm" in req
    finally:
        _limpar(referencia)


def test_contrato_sem_eventos_devolve_lista_vazia(chave_privada):
    referencia = "CTR-EVENTOS-VAZIO"
    contrato = _criar(referencia)
    try:
        url = f"/api/v1/contratos/{FINANCIADOR_TESTE}/{contrato['id']}/eventos"
        resposta = Client().get(url, **_auth(chave_privada))
        assert resposta.status_code == 200
        assert resposta.json()["dados"] == []
    finally:
        _limpar(referencia)
