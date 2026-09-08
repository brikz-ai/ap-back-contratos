"""Autenticação Bearer JWT do IdP corporativo, portado do agenda-service.

Chave pública RS256 fixa (IAM_JWT_PUBLIC_KEY) e emissor esperado
(IAM_JWT_ISSUER) — sem JWKS/rede, mesmo padrão de shared/secrets.py para
segredos estáticos. Rotas isentas (health, push do Pub/Sub, webhook da
CERC) simplesmente não usam @jwt_required — não há middleware global com
exceção por path.

Multi-tenancy: exige o claim `financiador_id` (CNPJ, 14 dígitos) em todo
JWT válido e o expõe em `request.financiador_id`, além de
`request.jwt_claims`. Nas rotas que também trazem o financiador no path,
a view compara os dois e recusa a divergência — o claim é a autoridade.
"""
import functools
import os
import re

import jwt
from django.http import JsonResponse


class JwtAuthError(Exception):
    def __init__(self, mensagem: str):
        self.mensagem = mensagem
        super().__init__(mensagem)


def _public_key() -> str:
    return os.environ["IAM_JWT_PUBLIC_KEY"].replace("\\n", "\n")


def validar_bearer_token(authorization_header: str) -> dict:
    if not authorization_header or not authorization_header.startswith("Bearer "):
        raise JwtAuthError("header Authorization ausente ou sem esquema Bearer")

    token = authorization_header[len("Bearer "):].strip()
    if not token:
        raise JwtAuthError("token vazio")

    try:
        return jwt.decode(
            token,
            _public_key(),
            algorithms=["RS256"],
            issuer=os.environ["IAM_JWT_ISSUER"],
            options={"require": ["exp", "iss"]},
        )
    except jwt.ExpiredSignatureError:
        raise JwtAuthError("token expirado")
    except jwt.InvalidTokenError as exc:
        raise JwtAuthError(f"token inválido: {exc}")


def jwt_required(view_func):
    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        try:
            claims = validar_bearer_token(request.headers.get("Authorization", ""))
        except JwtAuthError as exc:
            return JsonResponse({"erro": "NAO_AUTENTICADO", "mensagem": exc.mensagem}, status=401)
        except KeyError:
            # IAM_JWT_PUBLIC_KEY/IAM_JWT_ISSUER ausentes do ambiente (deploy sem
            # as variáveis configuradas no Cloud Run) chegam aqui como KeyError
            # cru, não como JwtAuthError — sem este except, escapariam deste
            # wrapper e virariam um 500 do Django. Com DEBUG=True (estado atual
            # de homolog), isso é a página de debug completa devolvida a um
            # chamador não autenticado. É má configuração do serviço, não erro
            # do cliente nem informação que ele deva receber — por isso 503
            # com corpo curto, sem detalhe interno.
            return JsonResponse({"erro": "SERVICO_MAL_CONFIGURADO"}, status=503)

        financiador_id = claims.get("financiador_id")
        if not financiador_id or not re.fullmatch(r"\d{14}", str(financiador_id)):
            return JsonResponse(
                {"erro": "NAO_AUTENTICADO", "mensagem": "claim financiador_id ausente ou inválido"}, status=401
            )

        request.jwt_claims = claims
        # str() de propósito: um IdP que emita o claim como número JSON (não
        # string) passa no fullmatch acima (que já compara contra str()), mas
        # sem esta conversão o valor cru (int) ficaria em request.financiador_id
        # — e toda comparação de tenant feita por uma view (`==` contra o
        # financiador_id em string vindo da URL) falharia sempre, com um 403
        # inexplicável.
        request.financiador_id = str(financiador_id)
        return view_func(request, *args, **kwargs)

    return wrapper
