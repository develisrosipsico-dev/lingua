"""Etapa 05: previsões morfológicas sobre uma segmentação preservada.

O modelo executa localmente sobre um Doc com a tokenização da etapa 04.
Textos, unidades e posições herdados são imutáveis nesta etapa. A prontidão
indica integridade técnica, não a correção linguística das previsões.
"""

from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache
import json
import re
from threading import Lock
from uuid import uuid4

from segmentacao import ErroSegmentacao, validar_segmentacao


SCHEMA_VERSION = "1.0.0"
ETAPA = "05_morfologia_vocabulario"
MODELO_NOME = "pt_core_news_sm"
MODELO_VERSAO = "3.8.0"
MAX_CARACTERES = 50_000
MAX_TOKENS = 10_000
_COMPONENTES = ["tok2vec", "morphologizer", "lemmatizer"]
_DESATIVADOS = ["parser", "senter", "attribute_ruler", "ner"]
_POS = frozenset({"ADJ", "ADP", "ADV", "AUX", "CCONJ", "DET", "INTJ", "NOUN",
                 "NUM", "PART", "PRON", "PROPN", "PUNCT", "SCONJ", "SPACE", "SYM", "VERB", "X"})
_CAMPOS = ("lema", "pos", "tag", "morfologia")
_MODELO_LOCK = Lock()
_VERIFICACOES = [
    "estrutura_json_identificadores_e_data", "segmentacao_textos_hashes_e_mapa",
    "coordenadas_e_processamento", "cobertura_ordem_e_vinculos_dos_tokens",
    "textos_e_intervalos_herdados", "campos_linguisticos_e_pendencias",
    "agrupamento_contagens_e_vinculos_do_vocabulario",
]


class ErroAnotacao(ValueError):
    """Entrada inválida ou anotação tecnicamente inconsistente."""


class ErroModelo(ErroAnotacao):
    """Modelo indisponível, incompatível ou com falha de processamento."""


class ErroAlinhamento(ErroAnotacao):
    """A tokenização ou o documento deixou de corresponder à etapa 04."""


class ErroLimite(ErroAnotacao):
    """Entrada excede os limites da inferência local, sem truncamento."""


def _exigir(condicao, mensagem):
    if not condicao:
        raise ErroAnotacao(mensagem)


def _objeto(valor, nome):
    _exigir(isinstance(valor, dict), f"{nome} deve ser um objeto JSON.")
    return valor


def _campo(objeto, chave, nome):
    _exigir(chave in objeto, f"Campo obrigatório ausente: {nome}.{chave}.")
    return objeto[chave]


def _id(valor, nome):
    _exigir(isinstance(valor, str) and bool(valor.strip()), f"{nome} deve ser uma string não vazia.")
    return valor


def _tipos_json(valor):
    if valor is None or isinstance(valor, (str, bool, int, float)):
        return
    if isinstance(valor, dict):
        for chave, item in valor.items():
            _exigir(isinstance(chave, str), "As chaves do JSON devem ser textuais.")
            _tipos_json(item)
    elif isinstance(valor, list):
        for item in valor:
            _tipos_json(item)
    else:
        raise ErroAnotacao("O registro contém um tipo que não pertence ao JSON.")


def _json_estrito(valor):
    try:
        _tipos_json(valor)
        json.dumps(valor, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except ErroAnotacao:
        raise
    except (TypeError, ValueError, OverflowError, RecursionError) as erro:
        raise ErroAnotacao("O registro deve ser serializável em JSON UTF-8 estrito.") from erro


def _data(valor, *, gerar=False):
    if gerar and valor is None:
        valor = datetime.now(timezone.utc).isoformat()
    if gerar and isinstance(valor, datetime):
        valor = valor.isoformat()
    _exigir(isinstance(valor, str), "registrado_em deve ser uma data ISO 8601 com fuso.")
    try:
        instante = datetime.fromisoformat(valor.replace("Z", "+00:00"))
        _exigir(instante.tzinfo is not None and instante.utcoffset() is not None,
                "registrado_em deve conter fuso horário.")
    except (TypeError, ValueError, OverflowError) as erro:
        if isinstance(erro, ErroAnotacao):
            raise
        raise ErroAnotacao("registrado_em deve ser uma data ISO 8601 válida com fuso.") from erro
    return valor


def _fonte(segmentacao):
    try:
        validar_segmentacao(segmentacao)
    except ErroSegmentacao as erro:
        raise ErroAnotacao(f"Segmentação inválida: {erro}") from erro
    texto = segmentacao["preparacao"]["trabalho"]["texto"]
    if len(texto) > MAX_CARACTERES:
        raise ErroLimite(f"A anotação aceita até {MAX_CARACTERES} pontos de código. Nenhum conteúdo foi truncado.")
    return texto


@lru_cache(maxsize=1)
def _carregar_modelo():
    try:
        import spacy
        from packaging.specifiers import SpecifierSet
        modelo = spacy.load(MODELO_NOME, disable=_DESATIVADOS)
        meta = modelo.meta
        if (
            meta.get("lang") != "pt" or meta.get("version") != MODELO_VERSAO
            or meta.get("name") != "core_news_sm"
            or spacy.__version__ not in SpecifierSet(meta.get("spacy_version", ""))
            or modelo.pipe_names != _COMPONENTES
            or modelo.get_pipe_meta("lemmatizer").factory != "trainable_lemmatizer"
            or any(modelo.analyze_pipes(pretty=False)["problems"].values())
        ):
            raise ErroModelo("O modelo português ou suas dependências não correspondem à configuração suportada.")
        modelo.max_length = MAX_CARACTERES
        return modelo, spacy.__version__
    except ErroModelo:
        raise
    except Exception as erro:
        raise ErroModelo(
            "Não foi possível carregar pt_core_news_sm 3.8.0 compatível. "
            "Instale as dependências com o Python do ambiente virtual: python -m pip install -r requirements.txt."
        ) from erro


def _configuracoes(versao_origem, versao_execucao):
    return {
        "tokenizador": {
            "identificacao": "spacy.blank(pt)", "versao_origem": versao_origem,
            "versao_execucao": versao_execucao, "ajustes": [],
        },
        "max_caracteres": MAX_CARACTERES, "max_tokens_internos": MAX_TOKENS,
        "fronteiras_periodos": "herdadas_da_etapa_04",
        "vocabulario": {"pos_excluidas": ["PUNCT", "SYM"], "remover_stopwords": False},
        "embeddings_exportados": False,
    }


def _processamento(modelo, versao, segmentacao):
    return {
        "ferramenta": "spacy", "versao": versao, "idioma": "pt",
        "modelo": {"nome": MODELO_NOME, "versao": modelo.meta["version"],
                   "compatibilidade_spacy": modelo.meta["spacy_version"]},
        "componentes_executados": list(modelo.pipe_names),
        "componentes_desativados": list(modelo.disabled),
        "componentes_disponiveis": list(modelo.component_names),
        "configuracoes": _configuracoes(segmentacao["processamento"]["versao"], versao),
    }


def _origem(processamento):
    return {
        "ferramenta": processamento["ferramenta"], "versao": processamento["versao"],
        "modelo": {"nome": processamento["modelo"]["nome"], "versao": processamento["modelo"]["versao"]},
        "componentes": list(processamento["componentes_executados"]),
    }


def _assinatura(documento):
    return [(token.text, token.idx, token.whitespace_) for token in documento]


def _conferir_alinhamento(documento, segmentacao, texto):
    recebidos = [(t.text, t.idx, t.idx + len(t.text)) for t in documento if not t.text.isspace()]
    esperados = [(t["texto"], t["trabalho"]["inicio"], t["trabalho"]["fim"]) for t in segmentacao["tokens"]]
    if documento.text != texto or recebidos != esperados:
        raise ErroAlinhamento("O texto ou as fronteiras dos tokens não correspondem à segmentação recebida.")


def _executar(segmentacao, texto):
    # Os listeners do tok2vec compartilham estado: serialize a execução por processo.
    with _MODELO_LOCK:
        modelo, versao = _carregar_modelo()
        try:
            from spacy.tokens import Doc
            from segmentacao import _tokenizador
            tokenizador, _ = _tokenizador()
            interno = tokenizador.make_doc(texto)
            _conferir_alinhamento(interno, segmentacao, texto)
            if len(interno) > MAX_TOKENS:
                raise ErroLimite(f"A anotação aceita até {MAX_TOKENS} tokens internos. Nenhum conteúdo foi truncado.")
            if any(t.whitespace_ not in ("", " ") for t in interno):
                raise ErroAlinhamento("O espaço interno do tokenizador não pode ser reconstruído literalmente.")
            documento = Doc(modelo.vocab, words=[t.text for t in interno], spaces=[t.whitespace_ == " " for t in interno])
            _conferir_alinhamento(documento, segmentacao, texto)
            inicios = {p["trabalho"]["inicio"] for p in segmentacao["periodos"]}
            for token in documento:
                token.is_sent_start = token.idx in inicios
            assinatura = _assinatura(documento)
            fronteiras = [t.is_sent_start for t in documento]
            if len(documento):
                documento = modelo(documento)
            _conferir_alinhamento(documento, segmentacao, texto)
            if _assinatura(documento) != assinatura or [t.is_sent_start for t in documento] != fronteiras:
                raise ErroAlinhamento("Um componente alterou os tokens, espaços ou fronteiras de períodos herdados.")
            processamento = _processamento(modelo, versao, segmentacao)
            origem = _origem(processamento)
            anotacoes = []
            for fonte, token in zip(segmentacao["tokens"], (t for t in documento if not t.text.isspace())):
                campos = {
                    "lema": token.lemma_ or None, "pos": token.pos_ or None,
                    "tag": token.tag_ or None,
                    "morfologia": token.morph.to_dict() if token.has_morph() else None,
                }
                anotacao = {campo: deepcopy(fonte[campo]) for campo in ("texto", "periodo_id", "paragrafo_id", "trabalho", "original")}
                anotacao.update(token_id=fonte["id"], **campos,
                                origem=deepcopy(origem), campos_nao_resolvidos=[c for c in _CAMPOS if campos[c] is None])
                anotacoes.append(anotacao)
            return anotacoes, processamento
        except ErroAnotacao:
            raise
        except Exception as erro:
            raise ErroModelo("O modelo falhou ao anotar o documento integral; nenhum resultado parcial foi entregue.") from erro


def _vocabulario(anotacoes, anotacao_id):
    entradas = {}
    formas_vistas = {}
    for token in anotacoes:
        if token["pos"] in {"PUNCT", "SYM"}:
            continue
        criterio = "lema_pos" if token["lema"] is not None else "forma_pos"
        forma = token["texto"] if criterio == "forma_pos" else None
        chave = (criterio, token["lema"] if forma is None else forma, token["pos"])
        if chave not in entradas:
            ordem = len(entradas)
            entradas[chave] = {
                "id": f"{anotacao_id}:vocabulario:{ordem}", "ordem": ordem,
                "lema": token["lema"], "pos": token["pos"], "criterio_agrupamento": criterio,
                "forma_agrupamento": forma, "formas": [], "quantidade": 0, "token_ids": [],
            }
            formas_vistas[chave] = set()
        entrada = entradas[chave]
        if token["texto"] not in formas_vistas[chave]:
            entrada["formas"].append(token["texto"])
            formas_vistas[chave].add(token["texto"])
        entrada["quantidade"] += 1
        entrada["token_ids"].append(token["token_id"])
    return list(entradas.values())


def _relatorio(anotacoes):
    excluidos = sum(t["pos"] in {"PUNCT", "SYM"} for t in anotacoes)
    return {
        "estado": "pronto_para_etapa_06", "pronto_para_etapa_06": True,
        "verificacoes": list(_VERIFICACOES),
        "cobertura": {
            "tokens_total": len(anotacoes), "tokens_anotados": len(anotacoes),
            "tokens_no_vocabulario": len(anotacoes) - excluidos, "tokens_excluidos_vocabulario": excluidos,
        },
        "pendencias": [
            {"token_id": token["token_id"], "campo": campo, "motivo": "atributo_nao_produzido"}
            for token in anotacoes for campo in token["campos_nao_resolvidos"]
        ],
    }


def anotar_segmentacao(segmentacao, *, anotacao_id=None, registrado_em=None):
    """Anote o contexto inteiro, preservando a fonte e os intervalos existentes."""
    try:
        fonte = deepcopy(segmentacao)
    except (TypeError, ValueError, RecursionError) as erro:
        raise ErroAnotacao("Não foi possível copiar a segmentação recebida.") from erro
    texto = _fonte(fonte)
    identificador = str(uuid4()) if anotacao_id is None else _id(anotacao_id, "anotacao_id")
    data = _data(registrado_em, gerar=True)
    anotacoes, processamento = _executar(fonte, texto)
    registro = {
        "schema_version": SCHEMA_VERSION, "etapa": ETAPA,
        **{c: fonte[c] for c in ("documento_id", "preparacao_id", "segmentacao_id")},
        "anotacao_id": identificador, "registrado_em": data,
        "segmentacao": fonte, "coordenadas": deepcopy(fonte["coordenadas"]),
        "processamento": processamento, "anotacoes_tokens": anotacoes,
        "vocabulario": _vocabulario(anotacoes, identificador),
    }
    registro["validacao"] = _validar(registro, conferir_relatorio=False, fonte_validada=True)
    return registro


def _processamento_valido(valor, fonte):
    _objeto(valor, "processamento")
    _exigir(_campo(valor, "ferramenta", "processamento") == "spacy"
            and _campo(valor, "idioma", "processamento") == "pt", "Ferramenta ou idioma inválido.")
    versao = _id(_campo(valor, "versao", "processamento"), "processamento.versao")
    _exigir(re.fullmatch(r"3\.8\.\d+", versao) is not None, "Versão do spaCy incompatível com o modelo registrado.")
    _exigir(_campo(valor, "modelo", "processamento") == {
        "nome": MODELO_NOME, "versao": MODELO_VERSAO, "compatibilidade_spacy": ">=3.8.0,<3.9.0",
    }, "Modelo ou compatibilidade não suportado.")
    _exigir(_campo(valor, "componentes_executados", "processamento") == _COMPONENTES,
            "Componentes executados incompatíveis com esta etapa.")
    _exigir(_campo(valor, "componentes_desativados", "processamento") == _DESATIVADOS,
            "Componentes desativados inconsistentes.")
    _exigir(_campo(valor, "componentes_disponiveis", "processamento") == [
        "tok2vec", "morphologizer", "parser", "lemmatizer", "senter", "attribute_ruler", "ner",
    ], "Componentes disponíveis inconsistentes com o modelo.")
    configuracoes = _objeto(_campo(valor, "configuracoes", "processamento"), "processamento.configuracoes")
    _exigir(configuracoes == _configuracoes(fonte["processamento"]["versao"], versao),
            "Configurações inconsistentes com a política de anotação.")
    _exigir(type(configuracoes["max_caracteres"]) is int and type(configuracoes["max_tokens_internos"]) is int
            and configuracoes["embeddings_exportados"] is False
            and configuracoes["vocabulario"]["remover_stopwords"] is False,
            "Os tipos dos limites e opções de processamento são inconsistentes.")


def _validar(registro, *, conferir_relatorio, fonte_validada=False):
    _objeto(registro, "registro")
    _json_estrito(registro)
    _exigir(_campo(registro, "schema_version", "registro") == SCHEMA_VERSION
            and _campo(registro, "etapa", "registro") == ETAPA, "Formato ou etapa não suportado.")
    identificador = _id(_campo(registro, "anotacao_id", "registro"), "anotacao_id")
    _data(_campo(registro, "registrado_em", "registro"))
    fonte = _campo(registro, "segmentacao", "registro")
    if not fonte_validada:
        _fonte(fonte)
    for campo in ("documento_id", "preparacao_id", "segmentacao_id"):
        valor = _campo(registro, campo, "registro")
        _exigir(type(valor) is type(fonte[campo]) and valor == fonte[campo], f"{campo} diverge da fonte preservada.")
    coordenadas = _objeto(_campo(registro, "coordenadas", "registro"), "coordenadas")
    _exigir(coordenadas == fonte["coordenadas"] and type(coordenadas.get("base")) is int
            and coordenadas.get("fim_exclusivo") is True, "Coordenadas divergem da etapa 04.")
    processamento = _campo(registro, "processamento", "registro")
    _processamento_valido(processamento, fonte)
    origem = _origem(processamento)
    anotacoes = _campo(registro, "anotacoes_tokens", "registro")
    _exigir(isinstance(anotacoes, list) and len(anotacoes) == len(fonte["tokens"]),
            "Cada token da fonte deve possuir exatamente uma anotação.")
    for numero, (anotacao, token) in enumerate(zip(anotacoes, fonte["tokens"])):
        nome = f"anotacoes_tokens[{numero}]"
        _objeto(anotacao, nome)
        _exigir(_campo(anotacao, "token_id", nome) == token["id"], f"{nome}: token ausente, repetido ou fora de ordem.")
        for campo in ("periodo_id", "paragrafo_id", "texto", "trabalho", "original"):
            recebido = _campo(anotacao, campo, nome)
            _exigir(recebido == token[campo], f"{nome}.{campo} diverge do token original.")
            if campo in ("trabalho", "original"):
                _objeto(recebido, f"{nome}.{campo}")
                _exigir(type(recebido.get("inicio")) is int and type(recebido.get("fim")) is int,
                        f"{nome}.{campo} deve utilizar índices inteiros.")
        for campo in ("lema", "pos", "tag"):
            valor = _campo(anotacao, campo, nome)
            _exigir(valor is None or isinstance(valor, str) and bool(valor), f"{nome}.{campo} deve ser textual ou null.")
        _exigir(anotacao["pos"] is None or anotacao["pos"] in _POS, f"{nome}.pos não pertence ao esquema universal.")
        morfologia = _campo(anotacao, "morfologia", nome)
        _exigir(morfologia is None or isinstance(morfologia, dict) and all(
            isinstance(k, str) and bool(k) and isinstance(v, str) and bool(v) for k, v in morfologia.items()
        ), f"{nome}.morfologia deve ser um objeto de atributos textuais ou null.")
        _exigir(_campo(anotacao, "origem", nome) == origem, f"{nome}.origem diverge do processamento.")
        _exigir(_campo(anotacao, "campos_nao_resolvidos", nome) == [c for c in _CAMPOS if anotacao[c] is None],
                f"{nome}: os campos não resolvidos são inconsistentes.")
    vocabulario = _campo(registro, "vocabulario", "registro")
    _exigir(isinstance(vocabulario, list), "vocabulario deve ser uma lista.")
    _exigir(vocabulario == _vocabulario(anotacoes, identificador),
            "O vocabulário possui agrupamentos, contagens, formas ou vínculos inconsistentes.")
    for entrada in vocabulario:
        _exigir(type(entrada["ordem"]) is int and type(entrada["quantidade"]) is int,
                "Ordem e quantidade do vocabulário devem ser inteiros.")
    relatorio = _relatorio(anotacoes)
    if conferir_relatorio:
        informado = _objeto(_campo(registro, "validacao", "registro"), "validacao")
        _exigir(informado == relatorio and informado.get("pronto_para_etapa_06") is True,
                "O relatório informado diverge das verificações e pendências reais.")
        _exigir(all(type(v) is int for v in informado["cobertura"].values()), "Cobertura deve usar contagens inteiras.")
    return relatorio


def validar_anotacao(registro):
    """Confira a estrutura sem carregar o modelo nem refazer suas previsões."""
    return _validar(registro, conferir_relatorio=True)
