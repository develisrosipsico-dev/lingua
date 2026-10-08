"""Gabaritos da etapa 08, sem previsões de um modelo linguístico.

Os limites, textos, janelas e hashes abaixo são referências anotadas antes do
construtor de contexto. O tokenizador existente fornece somente IDs de origem.
"""

from copy import deepcopy
import json
from unittest.mock import patch

from fixtures_regras import construir_analise, infinitivo, ponto, token, verbo
from regras_linguisticas import aplicar_regras_linguisticas
from sintaxe_entidades import analisar_sintaxe_entidades


INSTANTE = "2026-10-08T09:30:00-03:00"
TEXTO = "  Árvore\tverde.  Casa azul! \t Céu.\r\n\r\nOutro.  João saiu.  "
LINHAS = [
    token("Árvore", "árvore", "NOUN", 0, "ROOT"),
    token("verde", "verde", "ADJ", 0, "amod"), ponto(0),
    token("Casa", "casa", "NOUN", 3, "ROOT"),
    token("azul", "azul", "ADJ", 3, "amod"), ponto(3, "!"),
    token("Céu", "céu", "NOUN", 6, "ROOT"), ponto(6),
    token("Outro", "outro", "PRON", 8, "ROOT"), ponto(8),
    token("João", "João", "PROPN", 11, "nsubj"),
    verbo("saiu", "sair", 11, tempo="Past"), ponto(11),
]

PERIODOS = [(2, 15), (17, 27), (30, 34), (38, 44), (46, 56)]
PARAGRAFOS = [(2, 34), (38, 56)]
TOKENS_FOCO = [[0, 1, 2], [3, 4, 5], [6, 7], [8, 9], [10, 11, 12]]
JANELAS_PADRAO = [[0, 1], [0, 1, 2], [1, 2], [3, 4], [3, 4]]
INTERVALOS_PADRAO = [(2, 27), (2, 34), (17, 34), (38, 56), (38, 56)]
JANELAS_CRUZADAS = [[0, 1], [0, 1, 2], [1, 2, 3], [2, 3, 4], [3, 4]]
JANELAS_AMPLAS = [[0, 1, 2], [0, 1, 2], [0, 1, 2], [3, 4], [3, 4]]
JANELAS_ASSIMETRICAS = [[0], [0, 1], [0, 1, 2], [3], [3, 4]]
LIMITES_PADRAO = [
    ("inicio_documento", "raio_atendido"),
    ("raio_atendido", "raio_atendido"),
    ("raio_atendido", "fronteira_paragrafo"),
    ("fronteira_paragrafo", "raio_atendido"),
    ("raio_atendido", "fim_documento"),
]
HASHES = {
    (2, 15): "b5b08dd2d8ba18d1e90ff038b068cff6c17d3a1a569da5d215229d8258cca0cb",
    (17, 27): "f0366efa6753ec5972d096762635d3657c39b6c1e02227e890888da855d07177",
    (30, 34): "98ef1d2413be1866d1c89b707599a608002d70e032695a6f4a33be6d31c8bc11",
    (38, 44): "910e4147767c1dd969bdea9315ca3ba4e97dc170271f92f8998b02f12685f3dc",
    (46, 56): "c82c7f03373e72a20638026bbb9659db9799b247e64d63942aec1331696d76ce",
    (2, 27): "08646bb34d7e6823b6ac6773de50885680102764ade5d9117df3bf45cfdbc297",
    (2, 34): "2bcf8f9f7ce2603fac143337dcd9d2a554fa316046b34a6780b62920a9dd561a",
    (17, 34): "acdcfde748eca098403e34b808f3894f4fd01c87ae6a0d1687f4546e84fccd99",
    (38, 56): "13eb131664c853d3507cf6809993f06032c4ce3baf0576fd39a62f331cdf74c5",
    (17, 44): "f6f14b960aa3cef2b637f807354c3090c77a7bf5866a3a04e1cdb39ed4bcfc1e",
    (30, 56): "82dcf8d6aecf524a417d7fe3802c2a82f8b0b4d0234b2458e20541b2002fc69f",
}

TEXTO_PENDENCIAS = "Ela pode sair.  O estudo.\r\n\r\nAmanhã João sairá."
LINHAS_PENDENCIAS = [
    token("Ela", "ela", "PRON", 1, "nsubj"), verbo("pode", "poder", 1),
    infinitivo("sair", "sair", 1), ponto(1),
    token("O", "o", "DET", 5, "det"),
    token("estudo", "estudo", "NOUN", 5, "ROOT"), ponto(5),
    token("Amanhã", "amanhã", "ADV", 9, "advmod"),
    token("João", "João", "PROPN", 9, "nsubj"),
    verbo("sairá", "sair", 9, tempo="Fut"), ponto(9),
]


def construir_regras(*, normalizar=False, texto=TEXTO, linhas=None,
                     regras_habilitadas=None, execucao_id="regras-fixture-08"):
    """Origem válida com sintaxe/morfologia manuais e nenhuma execução do modelo."""
    analise = construir_analise(texto=texto, normalizar=normalizar,
                               linhas=LINHAS if linhas is None else linhas)
    return aplicar_regras_linguisticas(
        analise, execucao_id=execucao_id, registrado_em=INSTANTE,
        regras_habilitadas=regras_habilitadas,
    )


def construir_pendencias():
    return construir_regras(texto=TEXTO_PENDENCIAS, linhas=LINHAS_PENDENCIAS)


def construir_regras_entidade():
    """João é uma entidade PER anotada manualmente no quinto período."""
    analise = construir_analise(texto=TEXTO, linhas=LINHAS)
    fonte = analise["anotacao"]["segmentacao"]["tokens"][10]
    entidade = {c: deepcopy(fonte[c]) for c in ("texto", "periodo_id", "paragrafo_id", "trabalho", "original")}
    entidade.update(rotulo="PER", token_ids=[fonte["id"]], origem=deepcopy(analise["sintaxe"][10]["origem"]))
    with patch("sintaxe_entidades._executar", return_value=(analise["sintaxe"], [entidade], analise["processamento"])):
        analise = analisar_sintaxe_entidades(analise["anotacao"], analise_id="analise-entidade-manual-08",
                                            registrado_em=INSTANTE)
    return aplicar_regras_linguisticas(analise, execucao_id="regras-entidade-manual-08", registrado_em=INSTANTE)


def detector_vinculo_documental(regra_id, analise, **opcoes):
    """Fixture de contrato: vínculo explícito permitido, ausente no catálogo atual.

    A referência não interpreta causa ou antecedente. Os dois predicados e
    seus tokens/escopos são manuais no texto Choveu. Por isso fiquei.
    """
    assert regra_id == "oracao_predicado"
    seg = analise["anotacao"]["segmentacao"]
    tokens = seg["tokens"]
    ocorrencias = []
    for raiz, alcance in ((0, [0, 1]), (4, [2, 3, 4, 5])):
        ocorrencias.append({
            "familia": "oracoes", "categoria": "predicado_verbal",
            "marcador_token_ids": [tokens[raiz]["id"]], "nucleo_token_ids": [tokens[raiz]["id"]],
            "alcance_token_ids": [tokens[i]["id"] for i in alcance],
            "vinculos": [], "explicacao": "Referência documental fornecida pela fixture manual.",
            "ambiguidades": [], "alternativas": [], "necessidade_contexto": [],
        })
    ocorrencias[0]["vinculos"] = [
        {"tipo": "referencia_documental", "alvo_tipo": "periodo", "alvo_id": seg["periodos"][1]["id"]},
        {"tipo": "referencia_documental", "alvo_tipo": "token", "alvo_id": tokens[4]["id"]},
    ]
    ocorrencias[0]["necessidade_contexto"] = ["Examinar a relação documental sem inferir significado."]
    return {"ocorrencias": ocorrencias, "pendencias": []}


def renomear_processamentos(registro, sufixo="outra-execucao"):
    """Reexecutar a cadeia com outros IDs, preservando documento e coordenadas.

    IDs determinísticos das etapas 04–07 incluem o respectivo ID de execução;
    a troca dos prefixos preserva todas as referências, sem alterar documento.
    """
    serializado = json.dumps(deepcopy(registro), ensure_ascii=False)
    for prefixo in ("preparacao", "segmentacao", "anotacao", "analise"):
        serializado = serializado.replace(f"{prefixo}-fixture-07", f"{prefixo}-{sufixo}")
    serializado = serializado.replace(registro["execucao_id"], f"regras-{sufixo}")
    return json.loads(serializado)
