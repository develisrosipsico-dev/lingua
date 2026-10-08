"""Etapa 07: ocorrências explicáveis sobre a análise preservada da etapa 06.

Não importa spaCy nem refaz previsões. O catálogo e o mecanismo são separados;
validação reproduz as condições mecânicas das regras sobre o JSON de origem.
Prontidão estrutural não certifica a correção linguística das hipóteses.
"""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import re
from uuid import uuid4

from catalogo_regras import CATALOGO
from preparacao import mapear_intervalos
from regras_detectores import construir_contexto, detectar_regra
from sintaxe_entidades import ErroAnalise, validar_analise_sintaxe_entidades


SCHEMA_VERSION = "1.0.0"
MODULO_VERSION = "1.0.0"
ETAPA = "07_regras_linguisticas"
FAMILIAS = ("oracoes", "negacao", "tempo", "modalidade", "conectores")
_IDS_ORIGEM = ("documento_id", "preparacao_id", "segmentacao_id", "anotacao_id", "analise_id")
_MENSAGEM_FALHA = "A regra falhou durante a execução; nenhum resultado dessa regra foi entregue."


class ErroRegras(ValueError):
    """Argumento ou registro incompatível com o contrato da etapa 07."""


class ErroEntrada(ErroRegras):
    """A análise ou sua cadeia de origem não satisfaz o contrato da etapa 06."""


class ErroConfiguracao(ErroRegras):
    """Regra desconhecida, seleção duplicada ou configuração inválida."""


def _exigir(condicao, mensagem):
    if not condicao:
        raise ErroRegras(mensagem)


def _json_estrito(valor):
    """Recuse tipos implícitos, chaves não textuais, ciclos e números não finitos."""
    def visitar(item, ancestrais):
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float:
            _exigir(math.isfinite(item), "Números JSON precisam ser finitos.")
            return
        _exigir(type(item) in (dict, list), "O registro deve conter somente tipos JSON.")
        identidade = id(item)
        _exigir(identidade not in ancestrais, "O registro JSON não pode conter ciclos.")
        ancestrais.add(identidade)
        if type(item) is dict:
            _exigir(all(type(k) is str for k in item), "Chaves JSON precisam ser textuais.")
            for filho in item.values():
                visitar(filho, ancestrais)
        else:
            for filho in item:
                visitar(filho, ancestrais)
        ancestrais.remove(identidade)
    try:
        visitar(valor, set())
        json.dumps(valor, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, UnicodeError, ValueError, OverflowError, RecursionError) as erro:
        if isinstance(erro, ErroRegras):
            raise
        raise ErroRegras("O registro deve ser serializável em JSON UTF-8 estrito.") from erro


def _canonico(valor):
    return json.dumps(valor, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _id(valor, nome):
    _exigir(isinstance(valor, str) and bool(valor.strip()), f"{nome} deve ser uma string não vazia.")
    return valor


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
        if isinstance(erro, ErroRegras):
            raise
        raise ErroRegras("registrado_em deve ser uma data ISO 8601 válida com fuso.") from erro
    return valor


def _fonte(analise):
    try:
        validar_analise_sintaxe_entidades(analise)
    except (ErroAnalise, TypeError, KeyError, RecursionError) as erro:
        raise ErroEntrada(f"Análise da etapa 06 inválida: {erro}") from erro


def _regras_catalogo():
    return sorted(CATALOGO["regras"], key=lambda r: (r["prioridade"], r["id"]))


def _configuracao(habilitadas):
    ids = [r["id"] for r in _regras_catalogo()]
    if habilitadas is None:
        habilitadas = ids
    if not isinstance(habilitadas, list) or not all(isinstance(r, str) for r in habilitadas):
        raise ErroConfiguracao("regras_habilitadas deve ser uma lista de identificadores textuais.")
    if len(habilitadas) != len(set(habilitadas)):
        raise ErroConfiguracao("A seleção de regras contém identificadores duplicados.")
    desconhecidas = set(habilitadas) - set(ids)
    if desconhecidas:
        raise ErroConfiguracao("Regra não suportada: " + ", ".join(sorted(desconhecidas)) + ".")
    selecionadas = [r for r in ids if r in habilitadas]
    return {
        "regras_habilitadas": selecionadas,
        "ordem_execucao": selecionadas[:],
        "politica_falha": "preservar_diagnostico_sem_prontidao",
        "alcance": "tokens_selecionados_sem_expandir_periodo",
        "datas_relativas": "sem_conversao_automatica",
    }


class _Fonte:
    def __init__(self, analise):
        self.analise = analise
        self.segmentacao = analise["anotacao"]["segmentacao"]
        self.preparacao = self.segmentacao["preparacao"]
        self.texto = self.preparacao["trabalho"]["texto"]
        self.tokens = self.segmentacao["tokens"]
        self.por_id = {t["id"]: t for t in self.tokens}
        self.ordem = {t["id"]: i for i, t in enumerate(self.tokens)}
        self.morfologia = {t["token_id"]: t for t in analise["anotacao"]["anotacoes_tokens"]}
        self.sintaxe = {t["token_id"]: t for t in analise["sintaxe"]}
        self.periodos = {p["id"] for p in self.segmentacao["periodos"]}
        self.entidades = {e["id"] for e in analise["entidades"]}

    def ids(self, valor, nome, *, vazio=True):
        _exigir(isinstance(valor, list) and all(isinstance(t, str) and t in self.por_id for t in valor),
                f"{nome} precisa referenciar tokens existentes.")
        _exigir(len(valor) == len(set(valor)), f"{nome} contém tokens duplicados.")
        _exigir(vazio or bool(valor), f"{nome} não pode ser vazio.")
        return sorted(valor, key=self.ordem.__getitem__)

    def fragmentos(self, ids, pendentes):
        if not ids:
            return []
        grupos = []
        for identificador in ids:
            if grupos and self.ordem[identificador] == self.ordem[grupos[-1][-1]] + 1:
                grupos[-1].append(identificador)
            else:
                grupos.append([identificador])
        resultado = []
        for grupo in grupos:
            primeiro, ultimo = self.por_id[grupo[0]], self.por_id[grupo[-1]]
            inicio, fim = primeiro["trabalho"]["inicio"], ultimo["trabalho"]["fim"]
            trecho = {
                "token_ids": grupo, "texto": self.texto[inicio:fim],
                "trabalho": {"inicio": inicio, "fim": fim}, "original": None,
                "periodo_id": primeiro["periodo_id"], "paragrafo_id": primeiro["paragrafo_id"],
            }
            pendentes.append(trecho)
            resultado.append(trecho)
        return resultado


def _textos(valor, nome):
    _exigir(isinstance(valor, list) and all(isinstance(v, str) and bool(v.strip()) for v in valor),
            f"{nome} deve ser uma lista de mensagens textuais.")
    return list(dict.fromkeys(valor))


def _proposta(proposta, regra, fonte):
    _exigir(isinstance(proposta, dict), "A regra deve produzir objetos de ocorrência.")
    _exigir(proposta.get("familia") == regra["familia"], "Família da ocorrência diverge da regra.")
    categoria = _id(proposta.get("categoria"), "categoria")
    marcador = fonte.ids(proposta.get("marcador_token_ids"), "marcador_token_ids", vazio=False)
    nucleo = fonte.ids(proposta.get("nucleo_token_ids"), "nucleo_token_ids")
    alcance = proposta.get("alcance_token_ids")
    if alcance is not None:
        alcance = fonte.ids(alcance, "alcance_token_ids", vazio=False)
    periodo = fonte.por_id[marcador[0]]["periodo_id"]
    paragrafo = fonte.por_id[marcador[0]]["paragrafo_id"]
    _exigir(all(fonte.por_id[t]["periodo_id"] == periodo and fonte.por_id[t]["paragrafo_id"] == paragrafo
                for t in marcador + nucleo + (alcance or [])),
            "Marcador, núcleo e alcance devem permanecer no período e parágrafo de origem.")
    vinculos = proposta.get("vinculos", [])
    _exigir(isinstance(vinculos, list), "vinculos deve ser uma lista.")
    referencia = {"token": fonte.por_id, "periodo": fonte.periodos, "entidade": fonte.entidades}
    for vinculo in vinculos:
        _exigir(isinstance(vinculo, dict) and set(vinculo) == {"tipo", "alvo_tipo", "alvo_id"},
                "Formato de vínculo inválido.")
        _id(vinculo["tipo"], "vinculo.tipo")
        alvo_tipo = vinculo["alvo_tipo"]
        _exigir(isinstance(alvo_tipo, str) and alvo_tipo in referencia
                and isinstance(vinculo["alvo_id"], str) and vinculo["alvo_id"] in referencia[alvo_tipo],
                "Vínculo referencia unidade inexistente.")
    return {
        "familia": regra["familia"], "categoria": categoria,
        "regra": {"id": regra["id"], "versao": regra["versao"]},
        "marcador_token_ids": marcador, "nucleo_token_ids": nucleo,
        "alcance_token_ids": alcance, "periodo_id": periodo, "paragrafo_id": paragrafo,
        "vinculos": sorted({_canonico(v): deepcopy(v) for v in vinculos}.values(), key=_canonico),
        "explicacao": _id(proposta.get("explicacao"), "explicacao"),
        "ambiguidades": _textos(proposta.get("ambiguidades", []), "ambiguidades"),
        "alternativas": _textos(proposta.get("alternativas", []), "alternativas"),
        "necessidade_contexto": _textos(proposta.get("necessidade_contexto", []), "necessidade_contexto"),
    }


def _pendencias(valor, fonte):
    _exigir(isinstance(valor, list), "pendencias deve ser uma lista.")
    resultado = []
    for pendencia in valor:
        _exigir(isinstance(pendencia, dict) and set(pendencia) == {"token_ids", "campos", "motivo"},
                "Formato de pendência inválido.")
        resultado.append({
            "token_ids": fonte.ids(pendencia["token_ids"], "pendencia.token_ids", vazio=False),
            "campos": _textos(pendencia["campos"], "pendencia.campos"),
            "motivo": _id(pendencia["motivo"], "pendencia.motivo"),
        })
    return sorted({_canonico(p): p for p in resultado}.values(), key=_canonico)


def _executar(analise, configuracao, *, falhas=None):
    fonte = _Fonte(analise)
    contexto, erro_contexto = None, None
    try:
        contexto = construir_contexto(analise)
    except Exception as erro:
        erro_contexto = {"codigo": "falha_execucao_regra", "tipo": type(erro).__name__,
                         "mensagem": _MENSAGEM_FALHA}
    propostas, execucoes = [], []
    habilitadas = configuracao["regras_habilitadas"]
    for regra in _regras_catalogo():
        identificador = regra["id"]
        execucao = {"regra_id": identificador, "versao": regra["versao"], "estado": "desabilitada",
                    "ocorrencias_total": 0, "pendencias": [], "erro": None}
        execucoes.append(execucao)
        if identificador not in habilitadas:
            continue
        if falhas and identificador in falhas:
            execucao.update(estado="falhou", erro=deepcopy(falhas[identificador]))
            continue
        if erro_contexto is not None:
            execucao.update(estado="falhou", erro=deepcopy(erro_contexto))
            continue
        try:
            saida = detectar_regra(identificador, analise, regras_habilitadas=habilitadas, contexto=contexto)
            _exigir(isinstance(saida, dict) and set(saida) == {"ocorrencias", "pendencias"}
                    and isinstance(saida["ocorrencias"], list), "Formato de retorno da regra inválido.")
            candidatas = [_proposta(p, regra, fonte) for p in saida["ocorrencias"]]
            # Chave completa: regra/versão, categoria, tokens, alcance, vínculos e resultado.
            candidatas = list({_canonico(p): p for p in candidatas}.values())
            pendencias = _pendencias(saida["pendencias"], fonte)
            estado = "executada"
            if pendencias:
                estado = "executada_com_pendencias" if candidatas else "impedida"
            execucao.update(estado=estado, ocorrencias_total=len(candidatas), pendencias=pendencias)
            propostas.extend(candidatas)
        except Exception as erro:
            execucao.update(estado="falhou", erro={
                "codigo": "falha_execucao_regra", "tipo": type(erro).__name__, "mensagem": _MENSAGEM_FALHA,
            })
    propostas.sort(key=lambda p: (
        fonte.ordem[p["marcador_token_ids"][0]], p["regra"]["id"], p["categoria"], _canonico(p),
    ))
    return fonte, propostas, execucoes


def _ocorrencias(propostas, fonte, execucao_id):
    resultado, trechos = [], []
    for ordem, proposta in enumerate(propostas):
        item = deepcopy(proposta)
        ids_alcance = item.pop("alcance_token_ids")
        item.update(id=f"{execucao_id}:ocorrencia:{ordem}", ordem=ordem)
        evidencia_ids = sorted(set(item["marcador_token_ids"] + item["nucleo_token_ids"]), key=fonte.ordem.__getitem__)
        item["evidencias"] = fonte.fragmentos(evidencia_ids, trechos)
        item["alcance"] = None if ids_alcance is None else {
            "token_ids": ids_alcance, "trechos": fonte.fragmentos(ids_alcance, trechos),
        }
        item["anotacoes_utilizadas"] = [{
            "token_id": t, **{c: deepcopy(fonte.morfologia[t][c]) for c in ("lema", "pos", "morfologia")},
            **{c: fonte.sintaxe[t][c] for c in ("dependencia", "cabeca_token_id")},
        } for t in evidencia_ids]
        resultado.append(item)
    limites = [(t["trabalho"]["inicio"], t["trabalho"]["fim"]) for t in trechos]
    originais = mapear_intervalos(fonte.preparacao, limites)
    for trecho, original in zip(trechos, originais):
        trecho["original"] = original
    # Vínculos linguísticos mantêm o token de sustentação e referenciam o ID da oração encontrada.
    oracoes, negadores = {}, {}
    for item in resultado:
        if item["familia"] == "oracoes":
            for token_id in item["nucleo_token_ids"]:
                oracoes.setdefault(token_id, []).append(item["id"])
        if item["familia"] == "negacao" and len(item["marcador_token_ids"]) == 1:
            negadores.setdefault(item["marcador_token_ids"][0], []).append(item["id"])
    for item in resultado:
        adicionais = []
        for vinculo in item["vinculos"]:
            if vinculo["alvo_tipo"] == "token":
                for alvo in oracoes.get(vinculo["alvo_id"], []):
                    if alvo != item["id"]:
                        adicionais.append({"tipo": vinculo["tipo"], "alvo_tipo": "ocorrencia", "alvo_id": alvo})
                if vinculo["tipo"] in ("marcador_negacao", "negacao_no_conteudo"):
                    for alvo in negadores.get(vinculo["alvo_id"], []):
                        if alvo != item["id"]:
                            adicionais.append({"tipo": vinculo["tipo"], "alvo_tipo": "ocorrencia", "alvo_id": alvo})
        if item["familia"] != "oracoes":
            for token_id in item["nucleo_token_ids"]:
                for alvo in oracoes.get(token_id, []):
                    adicionais.append({"tipo": "estrutura_associada", "alvo_tipo": "ocorrencia", "alvo_id": alvo})
        item["vinculos"] = sorted({_canonico(v): v for v in item["vinculos"] + adicionais}.values(), key=_canonico)
    return resultado


def _relatorio(execucoes, ocorrencias):
    habilitadas = [e for e in execucoes if e["estado"] != "desabilitada"]
    pronto = bool(habilitadas) and all(e["estado"] == "executada" for e in habilitadas)
    regras = {r["id"]: r for r in CATALOGO["regras"]}
    familias = {}
    for familia in FAMILIAS:
        itens = [e for e in execucoes if regras[e["regra_id"]]["familia"] == familia]
        ativas = [e for e in itens if e["estado"] != "desabilitada"]
        estado = "desabilitada" if not ativas else (
            "executada" if all(e["estado"] == "executada" for e in ativas) else "incompleta")
        familias[familia] = {
            "estado": estado, "regras_habilitadas": len(ativas),
            "ocorrencias_total": sum(e["ocorrencias_total"] for e in itens),
        }
    return {
        "estado": "pronto_para_etapa_08" if pronto else "incompleto", "pronto_para_etapa_08": pronto,
        "verificacoes": ["analise_e_cadeia_de_origem", "catalogo_e_configuracao", "execucao_por_regra",
                         "condicoes_e_evidencias", "tokens_textos_intervalos_e_mapa_original",
                         "alcances_fragmentos_vinculos_ordem_e_duplicatas"],
        "cobertura": {
            "regras_total": len(execucoes), "regras_habilitadas": len(habilitadas),
            "regras_executadas": sum(e["estado"] == "executada" for e in execucoes),
            "regras_desabilitadas": sum(e["estado"] == "desabilitada" for e in execucoes),
            "regras_com_pendencias": sum(e["estado"] in ("impedida", "executada_com_pendencias") for e in execucoes),
            "regras_falharam": sum(e["estado"] == "falhou" for e in execucoes),
            "ocorrencias_total": len(ocorrencias), "familias": familias,
        },
        "limites": ["Validade estrutural não certifica a correção linguística das hipóteses.",
                    "Ambiguidades e necessidade de contexto são preservadas para a etapa 08."],
    }


def _gerar(fonte, execucao_id, data, configuracao, *, falhas=None):
    indice, propostas, execucoes = _executar(fonte, configuracao, falhas=falhas)
    ocorrencias = _ocorrencias(propostas, indice, execucao_id)
    registro = {
        "schema_version": SCHEMA_VERSION, "etapa": ETAPA,
        **{c: fonte[c] for c in _IDS_ORIGEM}, "execucao_id": execucao_id, "registrado_em": data,
        "analise": fonte, "coordenadas": deepcopy(fonte["coordenadas"]),
        "catalogo": deepcopy(CATALOGO), "configuracao": configuracao,
        "processamento": {
            "ferramenta": "biblioteca_padrao", "natureza": "hipoteses_por_regras",
            "modulo": {"nome": "regras_linguisticas", "versao": MODULO_VERSION},
            "catalogo_sha256": hashlib.sha256(_canonico(CATALOGO).encode("utf-8")).hexdigest(),
            "modelo_reexecutado": False,
        },
        "execucoes_regras": execucoes, "ocorrencias": ocorrencias,
        "validacao": _relatorio(execucoes, ocorrencias),
    }
    return registro


def aplicar_regras_linguisticas(analise, *, execucao_id=None, registrado_em=None, regras_habilitadas=None):
    """Acrescente as cinco famílias sem modificar a origem ou executar um modelo.

    Erros de entrada/configuração são exceções. Falhas operacionais de regras
    são diagnósticos no registro e impedem prontidão, preservando o histórico.
    """
    try:
        fonte = deepcopy(analise)
    except (TypeError, ValueError, RecursionError) as erro:
        raise ErroEntrada("Não foi possível copiar a análise de origem.") from erro
    _fonte(fonte)
    identificador = str(uuid4()) if execucao_id is None else _id(execucao_id, "execucao_id")
    data = _data(registrado_em, gerar=True)
    configuracao = _configuracao(regras_habilitadas)
    registro = _gerar(fonte, identificador, data, configuracao)
    # A construção usa fonte validada e fragmentos canônicos; a validação pública
    # adicional permite verificar o JSON posteriormente, sem inferência neural.
    _json_estrito(registro)
    return registro


def validar_regras_linguisticas(registro):
    """Revalide origem e reproduza as regras mecânicas, sem modificar o registro.

    Registros incompletos podem ser estruturalmente válidos, com prontidão
    false. Um diagnóstico de falha não equivale a resultado linguístico vazio.
    """
    _exigir(isinstance(registro, dict), "O registro precisa ser um objeto JSON.")
    _json_estrito(registro)
    _exigir(registro.get("schema_version") == SCHEMA_VERSION and registro.get("etapa") == ETAPA,
            "Formato ou etapa não suportado.")
    identificador = _id(registro.get("execucao_id"), "execucao_id")
    data = _data(registro.get("registrado_em"))
    fonte = registro.get("analise")
    _fonte(fonte)
    _exigir(_canonico(registro.get("catalogo")) == _canonico(CATALOGO),
            "Catálogo inexistente, alterado ou versão não suportada.")
    informado = registro.get("configuracao")
    _exigir(isinstance(informado, dict) and "regras_habilitadas" in informado, "Configuração ausente ou inválida.")
    configuracao = _configuracao(informado["regras_habilitadas"])
    _exigir(_canonico(informado) == _canonico(configuracao), "Configuração ou ordem de execução inconsistente.")
    execucoes = registro.get("execucoes_regras")
    _exigir(isinstance(execucoes, list), "execucoes_regras deve ser uma lista.")
    falhas = {}
    for execucao in execucoes:
        _exigir(isinstance(execucao, dict), "Formato de execução de regra inválido.")
        if execucao.get("estado") == "falhou":
            regra_id = execucao.get("regra_id")
            _exigir(isinstance(regra_id, str) and regra_id in configuracao["regras_habilitadas"]
                    and regra_id not in falhas, "Falha de regra inexistente, desabilitada ou duplicada.")
            erro = execucao.get("erro")
            _exigir(isinstance(erro, dict) and set(erro) == {"codigo", "tipo", "mensagem"}
                    and erro["codigo"] == "falha_execucao_regra" and erro["mensagem"] == _MENSAGEM_FALHA
                    and isinstance(erro["tipo"], str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", erro["tipo"]) is not None,
                    "Diagnóstico de falha inconsistente.")
            falhas[regra_id] = erro
    esperado = _gerar(fonte, identificador, data, configuracao, falhas=falhas)
    _exigir(_canonico(registro) == _canonico(esperado),
            "Registro inconsistente: origem, execução, condições, evidências, alcances, vínculos ou prontidão divergentes.")
    return deepcopy(esperado["validacao"])
