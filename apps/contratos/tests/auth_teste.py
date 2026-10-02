"""Token JWT de teste para as rotas de /contratos, que exigem @jwt_required.

Par RSA gerado uma vez por processo, em memória: sem rede, sem segredo de
ambiente. O conftest deste diretório publica a pública como chave do IAM
(IAM_JWT_PUBLIC_KEY_BRIKZ_IAM) em todo teste.
"""
import time

import jwt as pyjwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import Client

ISSUER = "brikz-iam"
FINANCIADOR_TESTE = "12345678000199"

_CHAVE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVADA_PEM = _CHAVE.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()
PUBLICA_PEM = _CHAVE.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
).decode()


def token_de_acesso(financiador_id: str = FINANCIADOR_TESTE, **overrides) -> str:
    agora = int(time.time())
    claims = {
        "iss": ISSUER, "type": "access", "sub": "teste", "iat": agora,
        "exp": agora + 3600, "financiador_id": financiador_id,
    }
    claims.update(overrides)
    return pyjwt.encode(claims, PRIVADA_PEM, algorithm="RS256")


def cliente_autenticado(financiador_id: str = FINANCIADOR_TESTE) -> Client:
    """django.test.Client que manda o Bearer em toda requisição."""
    return Client(HTTP_AUTHORIZATION=f"Bearer {token_de_acesso(financiador_id)}")
