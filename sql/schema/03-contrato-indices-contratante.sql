-- A listagem passou a aceitar ?documentoContratante= (front monta a lista de
-- contratos dentro da ficha de um cliente). Sem este índice a consulta vira
-- scan da tabela inteira do tenant: os índices existentes são
-- (cnpj_participante, status) e (status), nenhum cobre o contratante.
CREATE INDEX ON contrato (cnpj_participante, documento_contratante);
