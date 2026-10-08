"""Referências manuais da etapa 07, independentes do modelo e dos detectores.

As linhas de cada caso foram anotadas previamente: forma, lema, POS, cabeça,
dependência e morfologia. O tokenizador da etapa 04 é usado somente para obter
IDs e coordenadas literais; nenhum modelo determina os atributos linguísticos
ou o gabarito. Metadados técnicos vêm de um exemplo validado da etapa 06.
"""

from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch

from anotacao import anotar_segmentacao
from preparacao import preparar_texto
from segmentacao import segmentar_preparacao
from sintaxe_entidades import analisar_sintaxe_entidades


INSTANTE = "2026-10-07T18:20:00-03:00"
_EXEMPLO = json.loads((Path(__file__).resolve().parents[1] /
                      "examples/sintaxe_entidades_literal.json").read_text(encoding="utf-8"))
_PROCESSAMENTO_ANOTACAO = _EXEMPLO["anotacao"]["processamento"]
_PROCESSAMENTO_SINTAXE = _EXEMPLO["processamento"]
_ORIGEM_ANOTACAO = _EXEMPLO["anotacao"]["anotacoes_tokens"][0]["origem"]
_ORIGEM_SINTAXE = _EXEMPLO["sintaxe"][0]["origem"]


def token(forma, lema, pos, cabeca, dependencia, morfologia=None):
    """Uma anotação manual; {} significa atributos examinados e ausentes."""
    return (forma, lema, pos, cabeca, dependencia,
            {} if morfologia is None else morfologia)


def verbo(forma, lema, cabeca, dependencia="ROOT", *, tempo="Pres", pos="VERB"):
    morfologia = {"VerbForm": "Fin", "Mood": "Ind", "Tense": tempo}
    return token(forma, lema, pos, cabeca, dependencia, morfologia)


def infinitivo(forma, lema, cabeca, dependencia="xcomp"):
    return token(forma, lema, "VERB", cabeca, dependencia, {"VerbForm": "Inf"})


def ponto(cabeca, forma="."):
    return token(forma, forma, "PUNCT", cabeca, "punct")


# Gabaritos de tokens são índices na sequência literal, nunca saídas do motor.
CASOS = {
    "auxiliar": {
        "texto": "Ela está estudando.",
        "tokens": [token("Ela", "ela", "PRON", 2, "nsubj"),
                   verbo("está", "estar", 2, "aux", pos="AUX"),
                   token("estudando", "estudar", "VERB", 2, "ROOT", {"VerbForm": "Ger"}),
                   ponto(2)],
        "oracoes": [{"marcador": [1, 2], "nucleo": [2], "alcance": [0, 1, 2, 3]}],
        "evidencia_literal": [("está estudando", 4, 18)],
    },
    "copula": {
        "texto": "Ela está feliz.",
        "tokens": [token("Ela", "ela", "PRON", 2, "nsubj"),
                   verbo("está", "estar", 2, "cop", pos="AUX"),
                   token("feliz", "feliz", "ADJ", 2, "ROOT"), ponto(2)],
        "oracoes": [{"marcador": [1, 2], "nucleo": [2], "alcance": [0, 1, 2, 3]}],
    },
    "desejo": {
        "texto": "Ela quer sair.",
        "tokens": [token("Ela", "ela", "PRON", 1, "nsubj"),
                   verbo("quer", "querer", 1), infinitivo("sair", "sair", 1), ponto(1)],
        "oracoes": [{"marcador": [1], "nucleo": [1], "alcance": [0, 1, 3]},
                    {"marcador": [2], "nucleo": [2], "alcance": [2]}],
        "modal": {"marcador": [1], "categoria": "desejo"},
    },
    "coordenacao": {
        "texto": "Ela estudou e passou.",
        "tokens": [token("Ela", "ela", "PRON", 1, "nsubj"),
                   verbo("estudou", "estudar", 1, tempo="Past"),
                   token("e", "e", "CCONJ", 3, "cc"),
                   verbo("passou", "passar", 1, "conj", tempo="Past"), ponto(1)],
        "oracoes": [{"marcador": [1], "nucleo": [1], "alcance": [0, 1, 4]},
                    {"marcador": [3], "nucleo": [3], "alcance": [2, 3]}],
    },
    "condicao": {
        "texto": "Se chover, ficarei.",
        "tokens": [token("Se", "se", "SCONJ", 1, "mark"),
                   token("chover", "chover", "VERB", 3, "advcl", {"VerbForm": "Fin", "Mood": "Sub", "Tense": "Fut"}),
                   ponto(1, ","), verbo("ficarei", "ficar", 3, tempo="Fut"), ponto(3)],
        "conector": {"marcador": [0], "nucleo": [1], "categoria": "condicao"},
    },
    "nominal": {
        "texto": "O estudo.",
        "tokens": [token("O", "o", "DET", 1, "det"),
                   token("estudo", "estudo", "NOUN", 1, "ROOT"), ponto(1)],
        "oracoes": [],
    },
    "negacao_principal": {
        "texto": "Não pensei em sair.",
        "tokens": [token("Não", "não", "ADV", 1, "advmod"),
                   verbo("pensei", "pensar", 1, tempo="Past"),
                   token("em", "em", "ADP", 3, "case"),
                   infinitivo("sair", "sair", 1), ponto(1)],
        "negacao": {"marcador": [0], "nucleo": [1], "alcance": [0, 1, 4]},
        "evidencia_literal": [("Não pensei", 0, 10)],
    },
    "negacao_subordinada": {
        "texto": "Pensei em não sair.",
        "tokens": [verbo("Pensei", "pensar", 0, tempo="Past"),
                   token("em", "em", "ADP", 3, "case"),
                   token("não", "não", "ADV", 3, "advmod"),
                   infinitivo("sair", "sair", 0), ponto(0)],
        "negacao": {"marcador": [2], "nucleo": [3], "alcance": [1, 2, 3]},
    },
    "nao_so": {
        "texto": "Ela não só estudou, mas também passou.",
        "tokens": [token("Ela", "ela", "PRON", 3, "nsubj"),
                   token("não", "não", "ADV", 3, "advmod"),
                   token("só", "só", "ADV", 3, "advmod"),
                   verbo("estudou", "estudar", 3, tempo="Past"), ponto(3, ","),
                   token("mas", "mas", "CCONJ", 7, "cc"),
                   token("também", "também", "ADV", 7, "advmod"),
                   verbo("passou", "passar", 3, "conj", tempo="Past"), ponto(3)],
        "correlativa": {"marcador": [1, 2, 5, 6]},
    },
    "nao_so_outro_nao": {
        "texto": "Não só não estudou, mas também faltou.",
        "tokens": [token("Não", "não", "ADV", 3, "advmod"),
                   token("só", "só", "ADV", 3, "advmod"),
                   token("não", "não", "ADV", 3, "advmod"),
                   verbo("estudou", "estudar", 3, tempo="Past"), ponto(3, ","),
                   token("mas", "mas", "CCONJ", 7, "cc"),
                   token("também", "também", "ADV", 7, "advmod"),
                   verbo("faltou", "faltar", 3, "conj", tempo="Past"), ponto(3)],
        "correlativa": {"marcador": [0, 1, 5, 6]},
        "negacao": {"marcador": [2], "nucleo": [3]},
    },
    "nao_so_incompleta": {
        "texto": "Ela não só estudou.",
        "tokens": [token("Ela", "ela", "PRON", 3, "nsubj"),
                   token("não", "não", "ADV", 3, "advmod"),
                   token("só", "só", "ADV", 3, "advmod"),
                   verbo("estudou", "estudar", 3, tempo="Past"), ponto(3)],
    },
    "nunca": {
        "texto": "Ela nunca saiu.",
        "tokens": [token("Ela", "ela", "PRON", 2, "nsubj"),
                   token("nunca", "nunca", "ADV", 2, "advmod"),
                   verbo("saiu", "sair", 2, tempo="Past"), ponto(2)],
        "negacao": {"marcador": [1], "nucleo": [2]},
    },
    "nem": {
        "texto": "Ela nem saiu.",
        "tokens": [token("Ela", "ela", "PRON", 2, "nsubj"),
                   token("nem", "nem", "ADV", 2, "advmod"),
                   verbo("saiu", "sair", 2, tempo="Past"), ponto(2)],
        "negacao": {"marcador": [1], "nucleo": [2]},
    },
    "multiplos": {
        "texto": "Ela não saiu nunca.",
        "tokens": [token("Ela", "ela", "PRON", 2, "nsubj"),
                   token("não", "não", "ADV", 2, "advmod"),
                   verbo("saiu", "sair", 2, tempo="Past"),
                   token("nunca", "nunca", "ADV", 2, "advmod"), ponto(2)],
        "multiplos": {"marcador": [1, 3], "nucleo": [2]},
    },
    "negacao_metalinguistica": {
        "texto": "O não é uma palavra.",
        "tokens": [token("O", "o", "DET", 1, "det"),
                   token("não", "não", "NOUN", 4, "nsubj"),
                   verbo("é", "ser", 4, "cop", pos="AUX"),
                   token("uma", "um", "DET", 4, "det"),
                   token("palavra", "palavra", "NOUN", 4, "ROOT"), ponto(4)],
    },
    "marcador_sem_nucleo": {
        "texto": "Não.",
        "tokens": [token("Não", "não", "ADV", 0, "ROOT"), ponto(0)],
        "negacao": {"marcador": [0], "nucleo": [], "alcance": None},
    },
    "citacao": {
        "texto": 'Ela disse: "não saio".',
        "tokens": [token("Ela", "ela", "PRON", 1, "nsubj"),
                   verbo("disse", "dizer", 1, tempo="Past"), ponto(1, ":"),
                   ponto(5, '"'), token("não", "não", "ADV", 5, "advmod"),
                   verbo("saio", "sair", 1, "ccomp"), ponto(5, '"'), ponto(1)],
        "negacao": {"marcador": [4], "nucleo": [5]},
    },
    "discurso_relatado": {
        "texto": "Ela disse que não sairia.",
        "tokens": [token("Ela", "ela", "PRON", 1, "nsubj"),
                   verbo("disse", "dizer", 1, tempo="Past"),
                   token("que", "que", "SCONJ", 4, "mark"),
                   token("não", "não", "ADV", 4, "advmod"),
                   token("sairia", "sair", "VERB", 1, "ccomp", {"VerbForm": "Fin", "Mood": "Cnd"}),
                   ponto(1)],
        "negacao": {"marcador": [3], "nucleo": [4], "alcance": [2, 3, 4]},
    },
    "pode": {
        "texto": "Ela pode sair.",
        "tokens": [token("Ela", "ela", "PRON", 1, "nsubj"),
                   verbo("pode", "poder", 1), infinitivo("sair", "sair", 1), ponto(1)],
        "modal": {"marcador": [1], "categoria": "possibilidade_indeterminada"},
        "evidencia_literal": [("pode", 4, 8)],
    },
    "pode_nominal": {
        "texto": "O pode é uma forma.",
        "tokens": [token("O", "o", "DET", 1, "det"),
                   token("pode", "pode", "NOUN", 4, "nsubj"),
                   verbo("é", "ser", 4, "cop", pos="AUX"),
                   token("uma", "um", "DET", 4, "det"),
                   token("forma", "forma", "NOUN", 4, "ROOT"), ponto(4)],
    },
    "necessidade": {
        "texto": "Ela precisa sair.",
        "tokens": [token("Ela", "ela", "PRON", 1, "nsubj"),
                   verbo("precisa", "precisar", 1), infinitivo("sair", "sair", 1), ponto(1)],
        "modal": {"marcador": [1], "categoria": "necessidade"},
    },
    "obrigacao": {
        "texto": "Ela deve sair.",
        "tokens": [token("Ela", "ela", "PRON", 1, "nsubj"),
                   verbo("deve", "dever", 1), infinitivo("sair", "sair", 1), ponto(1)],
        "modal": {"marcador": [1], "categoria": "obrigacao"},
    },
    "capacidade": {
        "texto": "Ela consegue sair.",
        "tokens": [token("Ela", "ela", "PRON", 1, "nsubj"),
                   verbo("consegue", "conseguir", 1), infinitivo("sair", "sair", 1), ponto(1)],
        "modal": {"marcador": [1], "categoria": "capacidade"},
    },
    "saber_capacidade": {
        "texto": "Ela sabe nadar.",
        "tokens": [token("Ela", "ela", "PRON", 1, "nsubj"),
                   verbo("sabe", "saber", 1), infinitivo("nadar", "nadar", 1), ponto(1)],
        "modal": {"marcador": [1], "categoria": "capacidade"},
    },
    "saber_proposicao": {
        "texto": "Sei que ela saiu.",
        "tokens": [verbo("Sei", "saber", 0), token("que", "que", "SCONJ", 3, "mark"),
                   token("ela", "ela", "PRON", 3, "nsubj"),
                   verbo("saiu", "sair", 0, "ccomp", tempo="Past"), ponto(0)],
    },
    "duvida": {
        "texto": "Talvez ela saia.",
        "tokens": [token("Talvez", "talvez", "ADV", 2, "advmod"),
                   token("ela", "ela", "PRON", 2, "nsubj"),
                   token("saia", "sair", "VERB", 2, "ROOT", {"VerbForm": "Fin", "Mood": "Sub", "Tense": "Pres"}),
                   ponto(2)],
        "modal": {"marcador": [0], "categoria": "duvida"},
    },
    "necessidade_predicativa": {
        "texto": "São necessárias saídas.",
        "tokens": [verbo("São", "ser", 1, "cop", pos="AUX"),
                   token("necessárias", "necessário", "ADJ", 1, "ROOT", {"Gender": "Fem", "Number": "Plur"}),
                   token("saídas", "saída", "NOUN", 1, "nsubj"), ponto(1)],
        "modal": {"marcador": [1], "categoria": "necessidade"},
    },
    "desejo_negado": {
        "texto": "Ela não quer sair.",
        "tokens": [token("Ela", "ela", "PRON", 2, "nsubj"),
                   token("não", "não", "ADV", 2, "advmod"),
                   verbo("quer", "querer", 2), infinitivo("sair", "sair", 2), ponto(2)],
        "modal": {"marcador": [2], "categoria": "desejo"},
        "negacao": {"marcador": [1], "nucleo": [2]},
    },
    "tempo_relativo": {
        "texto": "Ontem fiquei. Hoje fico. Amanhã ficarei.",
        "tokens": [token("Ontem", "ontem", "ADV", 1, "advmod"),
                   verbo("fiquei", "ficar", 1, tempo="Past"), ponto(1),
                   token("Hoje", "hoje", "ADV", 4, "advmod"), verbo("fico", "ficar", 4), ponto(4),
                   token("Amanhã", "amanhã", "ADV", 7, "advmod"),
                   verbo("ficarei", "ficar", 7, tempo="Fut"), ponto(7)],
        "tempos": [{"marcador": [0], "nucleo": [1]}, {"marcador": [3], "nucleo": [4]},
                   {"marcador": [6], "nucleo": [7]}],
    },
    "tempo_nominal": {
        "texto": "O amanhã é uma palavra.",
        "tokens": [token("O", "o", "DET", 1, "det"),
                   token("amanhã", "amanhã", "NOUN", 4, "nsubj"),
                   verbo("é", "ser", 4, "cop", pos="AUX"),
                   token("uma", "um", "DET", 4, "det"),
                   token("palavra", "palavra", "NOUN", 4, "ROOT"), ponto(4)],
    },
    "quando": {
        "texto": "Quando chegar, avise.",
        "tokens": [token("Quando", "quando", "SCONJ", 1, "mark"),
                   token("chegar", "chegar", "VERB", 3, "advcl", {"VerbForm": "Fin", "Mood": "Sub", "Tense": "Fut"}),
                   ponto(1, ","), token("avise", "avisar", "VERB", 3, "ROOT", {"VerbForm": "Fin", "Mood": "Imp"}),
                   ponto(3)],
        "conector": {"marcador": [0], "nucleo": [1], "categoria": "tempo"},
        "evidencia_literal": [("Quando chegar", 0, 13)],
    },
    "se_reflexivo": {
        "texto": "Ela se penteou.",
        "tokens": [token("Ela", "ela", "PRON", 2, "nsubj"),
                   token("se", "se", "PRON", 2, "expl"),
                   verbo("penteou", "pentear", 2, tempo="Past"), ponto(2)],
    },
    "se_interrogativo": {
        "texto": "Não sei se ela saiu.",
        "tokens": [token("Não", "não", "ADV", 1, "advmod"), verbo("sei", "saber", 1),
                   token("se", "se", "SCONJ", 4, "mark"), token("ela", "ela", "PRON", 4, "nsubj"),
                   verbo("saiu", "sair", 1, "ccomp", tempo="Past"), ponto(1)],
    },
    "contraste": {
        "texto": "Ela ficou, mas ele saiu.",
        "tokens": [token("Ela", "ela", "PRON", 1, "nsubj"),
                   verbo("ficou", "ficar", 1, tempo="Past"), ponto(1, ","),
                   token("mas", "mas", "CCONJ", 5, "cc"),
                   token("ele", "ele", "PRON", 5, "nsubj"),
                   verbo("saiu", "sair", 1, "conj", tempo="Past"), ponto(1)],
        "conector": {"marcador": [3], "nucleo": [5], "categoria": "contraste"},
        "evidencia_literal": [("mas", 11, 14), ("saiu", 19, 23)],
    },
    "causa": {
        "texto": "Ela ficou porque choveu.",
        "tokens": [token("Ela", "ela", "PRON", 1, "nsubj"),
                   verbo("ficou", "ficar", 1, tempo="Past"), token("porque", "porque", "SCONJ", 3, "mark"),
                   verbo("choveu", "chover", 1, "advcl", tempo="Past"), ponto(1)],
        "conector": {"marcador": [2], "nucleo": [3], "categoria": "causa"},
    },
    "por_isso": {
        "texto": "Choveu. Por isso fiquei.",
        "tokens": [verbo("Choveu", "chover", 0, tempo="Past"), ponto(0),
                   token("Por", "por", "ADP", 3, "case"), token("isso", "isso", "PRON", 4, "obl"),
                   verbo("fiquei", "ficar", 4, tempo="Past"), ponto(4)],
        "conector": {"marcador": [2, 3], "nucleo": [4], "categoria": "consequencia"},
    },
    "por_isso_argumento": {
        "texto": "Passei por isso.",
        "tokens": [verbo("Passei", "passar", 0, tempo="Past"),
                   token("por", "por", "ADP", 2, "case"), token("isso", "isso", "PRON", 0, "obl"), ponto(0)],
    },
    "conector_nominal": {
        "texto": "O mas é uma palavra.",
        "tokens": [token("O", "o", "DET", 1, "det"), token("mas", "mas", "NOUN", 4, "nsubj"),
                   verbo("é", "ser", 4, "cop", pos="AUX"), token("uma", "um", "DET", 4, "det"),
                   token("palavra", "palavra", "NOUN", 4, "ROOT"), ponto(4)],
    },
    "contraste_nominal": {
        "texto": "João mas não Pedro.",
        "tokens": [token("João", "João", "PROPN", 0, "ROOT"),
                   token("mas", "mas", "CCONJ", 3, "cc"),
                   token("não", "não", "ADV", 3, "advmod"),
                   token("Pedro", "Pedro", "PROPN", 0, "conj"), ponto(0)],
    },
    "repeticao": {
        "texto": "Ela não saiu. Ela não saiu.",
        "tokens": [token("Ela", "ela", "PRON", 2, "nsubj"), token("não", "não", "ADV", 2, "advmod"),
                   verbo("saiu", "sair", 2, tempo="Past"), ponto(2),
                   token("Ela", "ela", "PRON", 6, "nsubj"), token("não", "não", "ADV", 6, "advmod"),
                   verbo("saiu", "sair", 6, tempo="Past"), ponto(6)],
        "negacoes": [{"marcador": [1], "nucleo": [2]}, {"marcador": [5], "nucleo": [6]}],
    },
    "unicode_crlf": {
        "texto": "  Ela\t\tnão saiu.\r\n\r\nAmanhã  João e\u0301 😀 ficará.  ",
        "tokens": [token("Ela", "ela", "PRON", 2, "nsubj"), token("não", "não", "ADV", 2, "advmod"),
                   verbo("saiu", "sair", 2, tempo="Past"), ponto(2),
                   token("Amanhã", "amanhã", "ADV", 8, "advmod"), token("João", "João", "PROPN", 8, "nsubj"),
                   token("e\u0301", "e\u0301", "X", 8, "dep"), token("😀", "😀", "SYM", 8, "dep"),
                   verbo("ficará", "ficar", 8, tempo="Fut"), ponto(8)],
    },
}


def construir_analise(nome="negacao_principal", *, texto=None, normalizar=False, linhas=None):
    """Construa etapa 06 válida com previsões estritamente controladas."""
    caso = CASOS[nome]
    texto = caso["texto"] if texto is None else texto
    linhas = caso["tokens"] if linhas is None else linhas
    preparacao = preparar_texto(texto, "documento-fixture-07", normalizar_crlf=normalizar,
                               preparacao_id="preparacao-fixture-07", registrado_em=INSTANTE,
                               metadados={"referencia": "anotacao_manual", "rotulos": ["independente"]})
    segmentacao = segmentar_preparacao(preparacao, segmentacao_id="segmentacao-fixture-07",
                                      registrado_em=INSTANTE)
    tokens = segmentacao["tokens"]
    formas = [linha[0] for linha in linhas]
    if [t["texto"] for t in tokens] != formas:
        raise AssertionError(f"Tokenização da referência {nome} divergiu: "
                             f"{[t['texto'] for t in tokens]!r} != {formas!r}")
    anotacoes, sintaxe = [], []
    for fonte, linha in zip(tokens, linhas):
        forma, lema, pos, cabeca, dependencia, morfologia = linha
        herdados = {c: deepcopy(fonte[c]) for c in ("texto", "periodo_id", "paragrafo_id", "trabalho", "original")}
        anotacoes.append(dict(herdados, token_id=fonte["id"], lema=lema, pos=pos, tag=None,
                             morfologia=deepcopy(morfologia), origem=deepcopy(_ORIGEM_ANOTACAO),
                             campos_nao_resolvidos=[c for c, v in (("lema", lema), ("pos", pos),
                                                      ("tag", None), ("morfologia", morfologia)) if v is None]))
        sintaxe.append(dict(herdados, token_id=fonte["id"], dependencia=dependencia,
                            cabeca_token_id=tokens[cabeca]["id"], origem=deepcopy(_ORIGEM_SINTAXE)))
    processamento_morfologia = deepcopy(_PROCESSAMENTO_ANOTACAO)
    processamento_sintaxe = deepcopy(_PROCESSAMENTO_SINTAXE)
    versao = segmentacao["processamento"]["versao"]
    for processamento in (processamento_morfologia, processamento_sintaxe):
        processamento["configuracoes"]["tokenizador"]["versao_origem"] = versao
    with patch("anotacao._executar", return_value=(anotacoes, processamento_morfologia)):
        anotacao = anotar_segmentacao(segmentacao, anotacao_id="anotacao-fixture-07", registrado_em=INSTANTE)
    with patch("sintaxe_entidades._executar", return_value=(sintaxe, [], processamento_sintaxe)):
        return analisar_sintaxe_entidades(anotacao, analise_id="analise-fixture-07", registrado_em=INSTANTE)


def ids(analise, indices):
    return [analise["anotacao"]["segmentacao"]["tokens"][i]["id"] for i in indices]
