"""Etapa 06: dependências e entidades previstas sobre uma anotação preservada.

O parser e o NER recebem a tokenização literal da etapa 04 e os atributos da
etapa 05. Separadores internos não são unidades linguísticas exportadas.
Prontidão certifica o contrato, não a correção linguística de uma previsão.
"""

from copy import deepcopy
from functools import lru_cache
import re
from threading import Lock
from uuid import uuid4

from anotacao import (
    ErroAnotacao, MAX_CARACTERES, MAX_TOKENS, MODELO_NOME, MODELO_VERSAO,
    _data, _json_estrito, validar_anotacao,
)
from preparacao import ErroPreparacao, mapear_intervalos


SCHEMA_VERSION = "1.0.0"
MODULO_VERSION = "1.0.0"
ETAPA = "06_sintaxe_entidades"
_COMPONENTES = ["tok2vec", "parser", "ner"]
_DESATIVADOS = ["morphologizer", "lemmatizer", "senter", "attribute_ruler"]
_DISPONIVEIS = ["tok2vec", "morphologizer", "parser", "lemmatizer", "senter", "attribute_ruler", "ner"]
_ROTULOS_ENTIDADES = ["LOC", "MISC", "ORG", "PER"]
_ROTULOS_DEPENDENCIAS = [
    "ROOT", "acl", "acl:relcl", "advcl", "advmod", "amod", "appos", "aux",
    "aux:pass", "case", "cc", "ccomp", "compound", "conj", "cop", "csubj",
    "dep", "det", "discourse", "expl", "fixed", "flat", "flat:foreign",
    "flat:name", "iobj", "mark", "nmod", "nsubj", "nsubj:pass", "nummod",
    "obj", "obl", "obl:agent", "parataxis", "punct", "xcomp",
]
_MODELO_LOCK = Lock()
_VERIFICACOES = [
    "estrutura_json_identificadores_e_data", "anotacao_e_registros_de_origem",
    "coordenadas_e_processamento", "cobertura_alinhamento_e_vinculos_dos_tokens",
    "arvores_raizes_cabecas_e_ausencia_de_ciclos",
    "entidades_ordem_textos_tokens_unidades_e_mapa_original",
]


class ErroAnalise(ValueError):
    """Argumento ou registro de análise inválido."""


class ErroEntrada(ErroAnalise):
    """A anotação recebida não satisfaz o contrato da etapa 05."""


class ErroModelo(ErroAnalise):
    """Modelo ausente, incompatível ou com falha de inferência."""


class ErroAlinhamento(ErroAnalise):
    """Texto, tokens ou fronteiras divergem da origem."""


class ErroLimite(ErroAnalise):
    """Limite de processamento excedido, sem truncamento."""


class ErroPrevisao(ErroAnalise):
    """A previsão retornada não satisfaz o contrato estrutural."""


def _exigir(condicao, mensagem):
    if not condicao:
        raise ErroAnalise(mensagem)


def _objeto(valor, nome):
    _exigir(isinstance(valor, dict), f"{nome} deve ser um objeto JSON.")
    return valor


def _campo(objeto, chave, nome):
    _exigir(chave in objeto, f"Campo obrigatório ausente: {nome}.{chave}.")
    return objeto[chave]


def _id(valor, nome):
    _exigir(isinstance(valor, str) and bool(valor.strip()), f"{nome} deve ser textual e não vazio.")
    return valor


def _data_analise(valor, *, gerar=False):
    try:
        return _data(valor, gerar=gerar)
    except ErroAnotacao as erro:
        raise ErroAnalise(str(erro)) from erro


def _fonte(anotacao):
    try:
        validar_anotacao(anotacao)
    except ErroAnotacao as erro:
        raise ErroEntrada(f"Anotação da etapa 05 inválida: {erro}") from erro
    return anotacao["segmentacao"]["preparacao"]["trabalho"]["texto"]


@lru_cache(maxsize=1)
def _carregar_modelo():
    try:
        import spacy
        from packaging.specifiers import SpecifierSet
        modelo = spacy.load(MODELO_NOME, disable=_DESATIVADOS)
        if (
            modelo.meta.get("lang") != "pt" or modelo.meta.get("name") != "core_news_sm"
            or modelo.meta.get("version") != MODELO_VERSAO
            or modelo.meta.get("spacy_version") != ">=3.8.0,<3.9.0"
            or spacy.__version__ not in SpecifierSet(modelo.meta["spacy_version"])
            or modelo.pipe_names != _COMPONENTES
            or modelo.get_pipe_meta("parser").factory != "parser"
            or modelo.get_pipe_meta("ner").factory != "ner"
            or sorted(modelo.get_pipe("parser").labels) != _ROTULOS_DEPENDENCIAS
            or sorted(modelo.get_pipe("ner").labels) != _ROTULOS_ENTIDADES
            or any(modelo.analyze_pipes(pretty=False)["problems"].values())
        ):
            raise ErroModelo("Modelo português ou componentes incompatíveis com a etapa 06.")
        modelo.max_length = MAX_CARACTERES
        return modelo, spacy.__version__
    except ErroModelo:
        raise
    except Exception as erro:
        raise ErroModelo(
            "Não foi possível carregar pt_core_news_sm 3.8.0 compatível. "
            "Instale com o Python do ambiente virtual: python -m pip install -r requirements.txt."
        ) from erro


def _configuracoes(anotacao, versao):
    return {
        "tokenizador": {"identificacao": "spacy.blank(pt)",
                        "versao_origem": anotacao["segmentacao"]["processamento"]["versao"],
                        "versao_execucao": versao, "ajustes": []},
        "max_caracteres": MAX_CARACTERES, "max_tokens_internos": MAX_TOKENS,
        "fronteiras_periodos": "herdadas_da_etapa_04_nos_tokens_de_conteudo",
        "separadores": "preservados_no_documento_interno_sem_ids_linguisticos",
        "morfologia": "herdada_da_etapa_05_sem_reanotacao",
        "raiz": {"dependencia": "ROOT", "cabeca": "proprio_token"},
        "mapa_original": "lote_equivalente_a_mapear_intervalo",
        "embeddings_exportados": False,
    }


def _processamento(modelo, versao, anotacao):
    return {
        "ferramenta": "spacy", "versao": versao, "idioma": "pt",
        "modulo": {"nome": "sintaxe_entidades", "versao": MODULO_VERSION},
        "modelo": {"nome": MODELO_NOME, "versao": modelo.meta["version"],
                   "compatibilidade_spacy": modelo.meta["spacy_version"]},
        "componentes_executados": list(modelo.pipe_names),
        "componentes_desativados": list(modelo.disabled),
        "componentes_disponiveis": list(modelo.component_names),
        "rotulos_dependencias": sorted(modelo.get_pipe("parser").labels),
        "rotulos_entidades": sorted(modelo.get_pipe("ner").labels),
        "natureza": "previsoes_do_modelo", "configuracoes": _configuracoes(anotacao, versao),
    }


def _origem(processamento):
    return {"ferramenta": processamento["ferramenta"], "versao": processamento["versao"],
            "modelo": deepcopy(processamento["modelo"]),
            "componentes": list(processamento["componentes_executados"]),
            "natureza": "previsoes_do_modelo"}


def _assinatura(documento):
    return [(t.text, t.idx, t.whitespace_) for t in documento]


def _conteudo(documento):
    return [t for t in documento if not t.text.isspace()]


def _conferir_alinhamento(documento, segmentacao, texto):
    obtidos = [(t.text, t.idx, t.idx + len(t.text)) for t in _conteudo(documento)]
    esperados = [(t["texto"], t["trabalho"]["inicio"], t["trabalho"]["fim"]) for t in segmentacao["tokens"]]
    if documento.text != texto or obtidos != esperados:
        raise ErroAlinhamento("Texto ou fronteiras dos tokens divergem da etapa 04.")


def _atributos(token):
    return (token.lemma_ or None, token.pos_ or None, token.tag_ or None,
            token.morph.to_dict() if token.has_morph() else None)


def _executar(anotacao, texto):
    # Cache e lock próprios: esta etapa não ativa componentes no modelo da 05.
    with _MODELO_LOCK:
        modelo, versao = _carregar_modelo()
        try:
            from spacy.tokens import Doc
            from segmentacao import _tokenizador
            segmentacao = anotacao["segmentacao"]
            tokenizador, _ = _tokenizador()
            interno = tokenizador.make_doc(texto)
            _conferir_alinhamento(interno, segmentacao, texto)
            if len(interno) > MAX_TOKENS:
                raise ErroLimite(f"A análise aceita até {MAX_TOKENS} tokens internos. Nenhum conteúdo foi truncado.")
            if any(t.whitespace_ not in ("", " ") for t in interno):
                raise ErroAlinhamento("Os separadores internos não podem ser reconstruídos literalmente.")
            documento = Doc(modelo.vocab, words=[t.text for t in interno], spaces=[t.whitespace_ == " " for t in interno])
            _conferir_alinhamento(documento, segmentacao, texto)
            conteudo = _conteudo(documento)
            for token, fonte in zip(conteudo, anotacao["anotacoes_tokens"]):
                if fonte["lema"] is not None:
                    token.lemma_ = fonte["lema"]
                if fonte["pos"] is not None:
                    token.pos_ = fonte["pos"]
                if fonte["tag"] is not None:
                    token.tag_ = fonte["tag"]
                if fonte["morfologia"] is not None:
                    token.set_morph(fonte["morfologia"])
                if _atributos(token) != (fonte["lema"], fonte["pos"], fonte["tag"], fonte["morfologia"]):
                    raise ErroAlinhamento("Não foi possível recuperar os atributos exatos da etapa 05.")
            inicios = {p["trabalho"]["inicio"] for p in segmentacao["periodos"]}
            for token in documento:
                token.is_sent_start = token.idx in inicios
            assinatura = _assinatura(documento)
            atributos = [_atributos(t) for t in conteudo]
            fronteiras = [t.is_sent_start for t in conteudo]
            # Branco não tem unidades linguísticas: não produzir raízes internas.
            if conteudo:
                documento = modelo(documento)
            _conferir_alinhamento(documento, segmentacao, texto)
            conteudo = _conteudo(documento)
            if _assinatura(documento) != assinatura or [t.is_sent_start for t in conteudo] != fronteiras:
                raise ErroAlinhamento("O modelo alterou tokens, separadores ou períodos de conteúdo herdados.")
            if [_atributos(t) for t in conteudo] != atributos:
                raise ErroAlinhamento("O modelo alterou as anotações morfológicas herdadas.")
            processamento = _processamento(modelo, versao, anotacao)
            origem = _origem(processamento)
            ids = {t.i: fonte["id"] for t, fonte in zip(conteudo, segmentacao["tokens"])}
            sintaxe = []
            for token, fonte in zip(conteudo, segmentacao["tokens"]):
                if token.head.i not in ids:
                    raise ErroAlinhamento("A cabeça sintática aponta para um separador sem identificador linguístico.")
                item = {c: deepcopy(fonte[c]) for c in ("texto", "periodo_id", "paragrafo_id", "trabalho", "original")}
                item.update(token_id=fonte["id"], dependencia=token.dep_,
                            cabeca_token_id=ids[token.head.i], origem=deepcopy(origem))
                sintaxe.append(item)
            entidades = []
            spans = list(documento.ents) if conteudo else []
            limites = [(e.start_char, e.end_char) for e in spans]
            originais = mapear_intervalos(segmentacao["preparacao"], limites) if limites else []
            por_id = {t["id"]: t for t in segmentacao["tokens"]}
            for entidade, original in zip(spans, originais):
                token_ids = [ids[t.i] for t in entidade if t.i in ids]
                if not token_ids:
                    raise ErroPrevisao("Entidade sem tokens correspondentes na etapa 04.")
                primeiro = por_id[token_ids[0]]
                entidades.append({
                    "texto": texto[entidade.start_char:entidade.end_char], "rotulo": entidade.label_,
                    "token_ids": token_ids, "periodo_id": primeiro["periodo_id"],
                    "paragrafo_id": primeiro["paragrafo_id"],
                    "trabalho": {"inicio": entidade.start_char, "fim": entidade.end_char},
                    "original": original, "origem": deepcopy(origem),
                })
            return sintaxe, entidades, processamento
        except ErroAnalise:
            raise
        except Exception as erro:
            raise ErroModelo("O modelo falhou na análise integral; nenhum resultado parcial foi entregue.") from erro


def _processamento_valido(valor, fonte):
    _objeto(valor, "processamento")
    versao = _id(_campo(valor, "versao", "processamento"), "processamento.versao")
    _exigir(re.fullmatch(r"3\.8\.\d+", versao) is not None, "Versão do spaCy incompatível.")
    esperados = {
        "ferramenta": "spacy", "idioma": "pt", "natureza": "previsoes_do_modelo",
        "modulo": {"nome": "sintaxe_entidades", "versao": MODULO_VERSION},
        "modelo": {"nome": MODELO_NOME, "versao": MODELO_VERSAO, "compatibilidade_spacy": ">=3.8.0,<3.9.0"},
        "componentes_executados": _COMPONENTES, "componentes_desativados": _DESATIVADOS,
        "componentes_disponiveis": _DISPONIVEIS, "rotulos_dependencias": _ROTULOS_DEPENDENCIAS,
        "rotulos_entidades": _ROTULOS_ENTIDADES, "configuracoes": _configuracoes(fonte, versao),
    }
    for chave, esperado in esperados.items():
        _exigir(_campo(valor, chave, "processamento") == esperado, f"processamento.{chave} inconsistente.")
    configuracoes = valor["configuracoes"]
    _exigir(type(configuracoes["max_caracteres"]) is int and type(configuracoes["max_tokens_internos"]) is int
            and configuracoes["embeddings_exportados"] is False, "Tipos de limites ou opções inconsistentes.")


def _validar_arvores(sintaxe, periodos):
    por_id = {item["token_id"]: item for item in sintaxe}
    for periodo in periodos:
        ids = set(periodo["tokens"])
        raizes = []
        for identificador in periodo["tokens"]:
            item = por_id[identificador]
            cabeca = item["cabeca_token_id"]
            _exigir(cabeca in por_id, "Cabeça sintática inexistente.")
            _exigir(cabeca in ids and por_id[cabeca]["paragrafo_id"] == item["paragrafo_id"],
                    "Ligação sintática atravessa período ou parágrafo.")
            if item["dependencia"] == "ROOT":
                _exigir(cabeca == identificador, "A raiz ROOT deve apontar para o próprio token.")
                raizes.append(identificador)
            else:
                _exigir(cabeca != identificador, "Autorreferência permitida somente para ROOT.")
        _exigir(len(raizes) == 1, "Cada período deve ter exatamente uma raiz ROOT.")
        # Cada vértice é visitado uma vez: evita seguir cadeias O(n²).
        alcançados = {raizes[0]}
        for identificador in periodo["tokens"]:
            percurso, visitados = [], set()
            atual = identificador
            while atual not in alcançados:
                _exigir(atual not in visitados, "Ciclo na árvore sintática.")
                visitados.add(atual)
                percurso.append(atual)
                atual = por_id[atual]["cabeca_token_id"]
            alcançados.update(percurso)


def _intervalo(valor, nome, comprimento):
    _objeto(valor, nome)
    inicio, fim = _campo(valor, "inicio", nome), _campo(valor, "fim", nome)
    _exigir(type(inicio) is int and type(fim) is int and 0 <= inicio < fim <= comprimento,
            f"{nome}: intervalo não vazio fora dos limites.")
    return inicio, fim


def _relatorio(fonte, sintaxe, entidades):
    return {
        "estado": "pronto_para_etapa_07", "pronto_para_etapa_07": True,
        "verificacoes": list(_VERIFICACOES),
        "cobertura": {"tokens_total": len(fonte["segmentacao"]["tokens"]), "tokens_analisados": len(sintaxe),
                      "periodos_total": len(fonte["segmentacao"]["periodos"]), "entidades_total": len(entidades)},
    }


def _validar(registro, *, conferir_relatorio, fonte_validada=False):
    _objeto(registro, "registro")
    try:
        _json_estrito(registro)
    except ErroAnotacao as erro:
        raise ErroAnalise(str(erro)) from erro
    _exigir(_campo(registro, "schema_version", "registro") == SCHEMA_VERSION
            and _campo(registro, "etapa", "registro") == ETAPA, "Formato ou etapa não suportado.")
    identificador = _id(_campo(registro, "analise_id", "registro"), "analise_id")
    _data_analise(_campo(registro, "registrado_em", "registro"))
    fonte = _campo(registro, "anotacao", "registro")
    if not fonte_validada:
        _fonte(fonte)
    for campo in ("documento_id", "preparacao_id", "segmentacao_id", "anotacao_id"):
        valor = _campo(registro, campo, "registro")
        _exigir(type(valor) is type(fonte[campo]) and valor == fonte[campo], f"{campo} diverge da origem.")
    coordenadas = _objeto(_campo(registro, "coordenadas", "registro"), "coordenadas")
    _exigir(coordenadas == fonte["coordenadas"] and type(coordenadas.get("base")) is int
            and coordenadas.get("fim_exclusivo") is True, "Coordenadas divergentes.")
    processamento = _campo(registro, "processamento", "registro")
    _processamento_valido(processamento, fonte)
    origem = _origem(processamento)
    segmentacao = fonte["segmentacao"]
    tokens = segmentacao["tokens"]
    sintaxe = _campo(registro, "sintaxe", "registro")
    _exigir(isinstance(sintaxe, list) and len(sintaxe) == len(tokens), "Cada token deve possuir exatamente uma análise sintática.")
    for numero, (item, token) in enumerate(zip(sintaxe, tokens)):
        nome = f"sintaxe[{numero}]"
        _objeto(item, nome)
        _exigir(_campo(item, "token_id", nome) == token["id"], "Tokens sintáticos ausentes, repetidos ou fora de ordem.")
        for campo in ("periodo_id", "paragrafo_id", "texto", "trabalho", "original"):
            valor = _campo(item, campo, nome)
            _exigir(valor == token[campo], f"{nome}.{campo} diverge da origem.")
            if campo in ("trabalho", "original"):
                _exigir(type(valor.get("inicio")) is int and type(valor.get("fim")) is int, "Índices devem ser inteiros.")
        dependencia = _campo(item, "dependencia", nome)
        _exigir(isinstance(dependencia, str) and dependencia in processamento["rotulos_dependencias"], "Rótulo de dependência não suportado pelo modelo.")
        _id(_campo(item, "cabeca_token_id", nome), f"{nome}.cabeca_token_id")
        _exigir(_campo(item, "origem", nome) == origem, "Origem da previsão sintática inconsistente.")
    _validar_arvores(sintaxe, segmentacao["periodos"])
    entidades = _campo(registro, "entidades", "registro")
    _exigir(isinstance(entidades, list), "entidades deve ser uma lista.")
    texto = segmentacao["preparacao"]["trabalho"]["texto"]
    texto_original = segmentacao["preparacao"]["original"]["texto"]
    por_id = {t["id"]: (numero, t) for numero, t in enumerate(tokens)}
    limites = []
    ultimo_fim = 0
    for numero, entidade in enumerate(entidades):
        nome = f"entidades[{numero}]"
        _objeto(entidade, nome)
        _exigir(_campo(entidade, "id", nome) == f"{identificador}:entidade:{numero}"
                and type(_campo(entidade, "ordem", nome)) is int and entidade["ordem"] == numero,
                "Identificador ou ordem de entidade inconsistente.")
        inicio, fim = _intervalo(_campo(entidade, "trabalho", nome), f"{nome}.trabalho", len(texto))
        _exigir(inicio >= ultimo_fim, "Entidades sobrepostas ou fora de ordem.")
        ultimo_fim = fim
        _exigir(_campo(entidade, "texto", nome) == texto[inicio:fim], "Texto de entidade diverge do recorte literal.")
        rotulo = _campo(entidade, "rotulo", nome)
        _exigir(isinstance(rotulo, str) and rotulo in processamento["rotulos_entidades"], "Rótulo de entidade não suportado pelo modelo.")
        token_ids = _campo(entidade, "token_ids", nome)
        _exigir(isinstance(token_ids, list) and bool(token_ids), "Entidade deve conter tokens correspondentes.")
        _exigir(all(isinstance(t, str) and t in por_id for t in token_ids), "Entidade referencia token inexistente.")
        indices = [por_id[t][0] for t in token_ids]
        _exigir(indices == list(range(indices[0], indices[0] + len(indices))), "Tokens de entidade devem ser consecutivos e ordenados.")
        selecionados = [por_id[t][1] for t in token_ids]
        _exigir(inicio == selecionados[0]["trabalho"]["inicio"] and fim == selecionados[-1]["trabalho"]["fim"],
                "Limites da entidade não coincidem com os tokens de conteúdo.")
        periodo = _campo(entidade, "periodo_id", nome)
        paragrafo = _campo(entidade, "paragrafo_id", nome)
        _exigir(all(t["periodo_id"] == periodo and t["paragrafo_id"] == paragrafo for t in selecionados),
                "Entidade atravessa período/parágrafo ou possui associação incorreta.")
        _intervalo(_campo(entidade, "original", nome), f"{nome}.original", len(texto_original))
        _exigir(_campo(entidade, "origem", nome) == origem, "Origem da previsão de entidade inconsistente.")
        limites.append((inicio, fim))
    try:
        originais = mapear_intervalos(segmentacao["preparacao"], limites) if limites else []
    except ErroPreparacao as erro:
        raise ErroAnalise(f"Mapa original inválido: {erro}") from erro
    for entidade, esperado in zip(entidades, originais):
        _exigir(entidade["original"] == esperado, "Intervalo da entidade no original diverge do mapa.")
    relatorio = _relatorio(fonte, sintaxe, entidades)
    if conferir_relatorio:
        informado = _objeto(_campo(registro, "validacao", "registro"), "validacao")
        _exigir(informado == relatorio and informado.get("pronto_para_etapa_07") is True, "Relatório de validação inconsistente.")
        _exigir(all(type(v) is int for v in informado["cobertura"].values()), "Contagens de cobertura devem ser inteiras.")
    return relatorio


def analisar_sintaxe_entidades(anotacao, *, analise_id=None, registrado_em=None):
    """Produza uma nova análise sobre uma cópia da saída validada da etapa 05."""
    try:
        fonte = deepcopy(anotacao)
    except (TypeError, ValueError, RecursionError) as erro:
        raise ErroEntrada("Não foi possível copiar a anotação de origem.") from erro
    texto = _fonte(fonte)
    if len(texto) > MAX_CARACTERES:
        raise ErroLimite(f"A análise aceita até {MAX_CARACTERES} pontos de código. Nenhum conteúdo foi truncado.")
    identificador = str(uuid4()) if analise_id is None else _id(analise_id, "analise_id")
    data = _data_analise(registrado_em, gerar=True)
    sintaxe, entidades, processamento = _executar(fonte, texto)
    entidades = deepcopy(entidades)
    for numero, entidade in enumerate(entidades):
        entidade.update(id=f"{identificador}:entidade:{numero}", ordem=numero)
    registro = {
        "schema_version": SCHEMA_VERSION, "etapa": ETAPA,
        **{c: fonte[c] for c in ("documento_id", "preparacao_id", "segmentacao_id", "anotacao_id")},
        "analise_id": identificador, "registrado_em": data, "anotacao": fonte,
        "coordenadas": deepcopy(fonte["coordenadas"]), "processamento": processamento,
        "sintaxe": sintaxe, "entidades": entidades,
    }
    try:
        registro["validacao"] = _validar(registro, conferir_relatorio=False, fonte_validada=True)
    except ErroAnalise as erro:
        raise ErroPrevisao(f"A previsão não satisfaz o contrato da etapa 06: {erro}") from erro
    return registro


def validar_analise_sintaxe_entidades(registro):
    """Valide registro e origem sem carregar spaCy nem refazer previsões."""
    return _validar(registro, conferir_relatorio=True)
