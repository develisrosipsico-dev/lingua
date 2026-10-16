"""Perfis gramaticais locais suportados, sem importar spaCy na leitura.

A versão do perfil identifica a política das etapas 05–06. Os registros
históricos do perfil small não continham esse campo e continuam válidos.
"""

MODELO_PADRAO = "pt_core_news_sm"
MODELOS_SUPORTADOS = (MODELO_PADRAO, "pt_core_news_md")
MODELO_VERSAO = "3.8.0"
COMPATIBILIDADE_SPACY = ">=3.8.0,<3.9.0"
PERFIL_VERSAO = "1.0.0"


def normalizar_modelo(nome=None):
    """Aceite apenas nomes oficiais cuja configuração foi validada."""
    if nome is None:
        return MODELO_PADRAO
    if not isinstance(nome, str) or nome not in MODELOS_SUPORTADOS:
        raise ValueError(
            "Modelo gramatical não suportado. Use pt_core_news_sm ou pt_core_news_md 3.8.0."
        )
    return nome


def especificacao_modelo(nome=None):
    return {
        "nome": normalizar_modelo(nome), "versao": MODELO_VERSAO,
        "compatibilidade_spacy": COMPATIBILIDADE_SPACY,
    }


def perfil_gramatical(nome):
    return {"id": normalizar_modelo(nome), "versao": PERFIL_VERSAO}


def perfil_registrado_valido(processamento):
    """Valide a versão sem exigir o campo inexistente nos registros antigos."""
    nome = normalizar_modelo(processamento["modelo"]["nome"])
    informado = processamento.get("perfil_gramatical")
    if "perfil_gramatical" not in processamento:
        return nome == MODELO_PADRAO
    return informado == perfil_gramatical(nome)
