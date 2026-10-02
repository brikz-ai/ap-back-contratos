import pytest

from apps.contratos.tests.auth_teste import ISSUER, PUBLICA_PEM


@pytest.fixture(autouse=True)
def _chave_do_iam_de_teste(monkeypatch):
    # As rotas de /contratos exigem JWT: todo teste deste app enxerga a chave
    # de teste como a do IAM. Testes que precisam de outro par (ex.: eventos)
    # sobrescrevem a env var na própria fixture.
    monkeypatch.setenv("IAM_JWT_PUBLIC_KEY_BRIKZ_IAM", PUBLICA_PEM)
    monkeypatch.setenv("IAM_JWT_ISSUER", ISSUER)
