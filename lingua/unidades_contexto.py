"""Etapa 08: focos e janelas exatos sobre uma execução pronta da etapa 07.

Organiza referências, sem executar modelos ou interpretar as pendências.
Coordenadas são pontos de código Unicode [inicio, fim). A fonte integral é
preservada uma vez; os hashes identificam campos textuais e a identidade
lógica identifica a seleção, independentemente dos IDs de processamento.
"""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from uuid import uuid4

from preparacao import ErroPreparacao, mapear_intervalos
from regras_linguisticas import ErroRegras, validar_regras_linguisticas


SCHEMA_VERSION = "1.0.0"
MODULO_VERSION = "1.0.0"
POLITICA_VERSION = "1.0.0"
IDENTIDADE_VERSION = "1.0.0"
ETAPA = "08_unidades_contexto"
MAX_UNIDADES = 10_000
MAX_REFERENCIAS_PERIODOS = 100_000
MAX_CARACTERES_TEXTUAIS = 16 * 1024 * 1024
MAX_REFERENCIAS = 500_000
_IDS_ORIGEM = ("documento_id", "preparacao_id", "segmentacao_id", "anotacao_id", "analise_id")
_CAMPOS_PENDENCIAS = ("ambiguidades", "necessidade_contexto")
_VERIFICACOES = [
    "origem_integral_validada_e_pronta",
    "cobertura_ordem_identificadores_e_associacoes",
    "politica_vizinhos_fronteiras_e_limites",
    "recortes_tokens_separadores_e_mapeamento_original",
    "hashes_sha256_utf8_dos_campos_exatos",
    "selecao_logica_canonica_e_identidade",
    "anotacoes_por_proprietario_e_vinculos_tipados",
    "pendencias_preservadas_sem_inferencia",
]


class ErroContexto(ValueError):
    """Configuração, consulta ou registro incompatível com a etapa 08."""


class ErroEntrada(ErroContexto):
    """A origem da etapa 07 está ausente, inválida ou incompleta."""


class ErroConfiguracao(ErroContexto):
    """A política solicitada tem tipos ou valores inválidos."""


class ErroLimite(ErroContexto):
    """O resultado excederia os limites declarados; nada foi truncado."""


def _exigir(condicao, mensagem):
    if not condicao:
        raise ErroContexto(mensagem)


def _canonico(valor):
    return json.dumps(valor, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _sha256(texto):
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _json_estrito(valor):
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
    except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError) as erro:
        if isinstance(erro, ErroContexto):
            raise
        raise ErroContexto("O registro deve ser serializável em JSON UTF-8 estrito.") from erro


def _id(valor, nome):
    _exigir(type(valor) is str and bool(valor.strip()), f"{nome} deve ser uma string não vazia.")
    return valor


def _data(valor, *, gerar=False):
    if gerar and valor is None:
        valor = datetime.now(timezone.utc).isoformat()
    if gerar and isinstance(valor, datetime):
        valor = valor.isoformat()
    _exigir(type(valor) is str, "registrado_em deve ser uma data ISO 8601 com fuso.")
    try:
        instante = datetime.fromisoformat(valor.replace("Z", "+00:00"))
        _exigir(instante.tzinfo is not None and instante.utcoffset() is not None,
                "registrado_em deve conter fuso horário.")
    except (TypeError, ValueError, OverflowError) as erro:
        if isinstance(erro, ErroContexto):
            raise
        raise ErroContexto("registrado_em deve ser uma data ISO 8601 válida com fuso.") from erro
    return valor


def _configuracao(raio_anterior, raio_seguinte, atravessar_paragrafos):
    for nome, raio in (("raio_anterior", raio_anterior), ("raio_seguinte", raio_seguinte)):
        if type(raio) is not int or raio < 0:
            raise ErroConfiguracao(f"{nome} deve ser um inteiro não negativo, sem booleanos.")
    if type(atravessar_paragrafos) is not bool:
        raise ErroConfiguracao("atravessar_paragrafos deve ser um booleano JSON.")
    configuracao = {"raio_anterior": raio_anterior, "raio_seguinte": raio_seguinte,
                    "atravessar_paragrafos": atravessar_paragrafos}
    try:
        _json_estrito(configuracao)
    except ErroContexto as erro:
        raise ErroConfiguracao("A política de contexto deve ser serializável em JSON UTF-8; "
                               "um raio excedeu a capacidade de representação do ambiente.") from erro
    return configuracao


def _politica(configuracao):
    return {"identificacao": "janela_periodos_consecutivos", "versao": POLITICA_VERSION,
            **configuracao}


def _validar_fonte(regras):
    try:
        relatorio = validar_regras_linguisticas(regras)
    except (ErroRegras, TypeError, KeyError, RecursionError) as erro:
        raise ErroEntrada(f"Origem da etapa 07 inválida: {erro}") from erro
    if relatorio.get("pronto_para_etapa_08") is not True:
        raise ErroEntrada("A execução da etapa 07 não está pronta para a etapa 08; "
                          "consulte seus estados de regras e pendências.")
    return relatorio


class _Fonte:
    """Índices de proprietário reutilizados por toda a execução."""

    def __init__(self, regras):
        self.regras = regras
        self.analise = regras["analise"]
        self.anotacao = self.analise["anotacao"]
        self.segmentacao = self.anotacao["segmentacao"]
        self.preparacao = self.segmentacao["preparacao"]
        self.texto = self.preparacao["trabalho"]["texto"]
        self.original = self.preparacao["original"]["texto"]
        self.periodos = self.segmentacao["periodos"]
        self.paragrafos = {p["id"]: p for p in self.segmentacao["paragrafos"]}
        self.tokens = {t["id"]: t for t in self.segmentacao["tokens"]}
        self.entidades = {e["id"]: e for e in self.analise["entidades"]}
        self.ocorrencias = {o["id"]: o for o in regras["ocorrencias"]}
        self.anotacoes = {p["id"]: {"morfologia": [], "sintaxe": [], "entidades": [], "regras": []}
                          for p in self.periodos}
        self.por_periodo = {p["id"]: [] for p in self.periodos}
        for a in self.anotacao["anotacoes_tokens"]:
            self.anotacoes[a["periodo_id"]]["morfologia"].append(
                {"anotacao_id": regras["anotacao_id"], "token_id": a["token_id"]})
        for a in self.analise["sintaxe"]:
            self.anotacoes[a["periodo_id"]]["sintaxe"].append(
                {"analise_id": regras["analise_id"], "token_id": a["token_id"]})
        for e in self.analise["entidades"]:
            self.anotacoes[e["periodo_id"]]["entidades"].append(
                {"analise_id": regras["analise_id"], "entidade_id": e["id"]})
        for o in regras["ocorrencias"]:
            self.anotacoes[o["periodo_id"]]["regras"].append(
                {"regras_execucao_id": regras["execucao_id"], "ocorrencia_id": o["id"]})
            self.por_periodo[o["periodo_id"]].append(o)
        self.fronteiras = {}
        posicao = {p["id"]: i for i, p in enumerate(self.periodos)}
        for paragrafo in self.paragrafos.values():
            if paragrafo["periodos"]:
                inicio = posicao[paragrafo["periodos"][0]]
                fim = posicao[paragrafo["periodos"][-1]] + 1
                self.fronteiras[paragrafo["id"]] = (inicio, fim)

    def periodo_alvo(self, vinculo):
        tipo, identificador = vinculo["alvo_tipo"], vinculo["alvo_id"]
        if tipo == "periodo":
            return identificador
        indice = {"token": self.tokens, "entidade": self.entidades, "ocorrencia": self.ocorrencias}[tipo]
        return indice[identificador]["periodo_id"]


def _prefixo(valores):
    resultado = [0]
    for valor in valores:
        resultado.append(resultado[-1] + valor)
    return resultado


def _limites_recursos():
    return {"max_unidades": MAX_UNIDADES, "max_referencias_periodos": MAX_REFERENCIAS_PERIODOS,
            "max_caracteres_textuais": MAX_CARACTERES_TEXTUAIS, "max_referencias": MAX_REFERENCIAS}


def _planejar(fonte, configuracao):
    """Recuse a expansão excessiva antes de materializar janelas e cópias."""
    periodos = fonte.periodos
    if len(periodos) > MAX_UNIDADES:
        raise ErroLimite(f"O resultado excede {MAX_UNIDADES} unidades; nenhum período foi omitido.")
    referencias = _prefixo(sum(len(v) for v in fonte.anotacoes[p["id"]].values())
                          + sum(len(o["vinculos"]) for o in fonte.por_periodo[p["id"]]) for p in periodos)
    mensagens = _prefixo(sum(len(o[c]) for o in fonte.por_periodo[p["id"]] for c in _CAMPOS_PENDENCIAS)
                        for p in periodos)
    planos = []
    custo = {"unidades": len(periodos), "referencias_periodos": 0, "caracteres_textuais": 0,
             "referencias": 0}
    for i, foco in enumerate(periodos):
        piso, teto = (0, len(periodos)) if configuracao["atravessar_paragrafos"] else fonte.fronteiras[foco["paragrafo_id"]]
        inicio = max(piso, i - configuracao["raio_anterior"])
        fim = min(teto, i + configuracao["raio_seguinte"] + 1)
        quantidade = fim - inicio
        primeiro, ultimo = periodos[inicio], periodos[fim - 1]
        paragrafos_total = (fonte.paragrafos[ultimo["paragrafo_id"]]["ordem"]
                           - fonte.paragrafos[primeiro["paragrafo_id"]]["ordem"] + 1)
        custo["referencias_periodos"] += quantidade
        custo["caracteres_textuais"] += sum(b["fim"] - a["inicio"] for a, b in (
            (foco["trabalho"], foco["trabalho"]), (foco["original"], foco["original"]),
            (primeiro["trabalho"], ultimo["trabalho"]), (primeiro["original"], ultimo["original"])))
        # Referências das quatro camadas, vínculos, tokens, seleção e contexto
        # de cada mensagem; inclui repetições que serão armazenadas no JSON.
        custo["referencias"] += (referencias[fim] - referencias[inicio] + len(foco["tokens"])
                                  + 2 * quantidade + paragrafos_total + 6
                                  + (mensagens[fim] - mensagens[inicio]) * (2 * quantidade + paragrafos_total + 2))
        for campo, limite in (("referencias_periodos", MAX_REFERENCIAS_PERIODOS),
                              ("caracteres_textuais", MAX_CARACTERES_TEXTUAIS),
                              ("referencias", MAX_REFERENCIAS)):
            if custo[campo] > limite:
                raise ErroLimite(f"O resultado excede o limite de {limite} para {campo}; "
                                 "nenhuma unidade ou texto foi truncado.")
        planos.append((inicio, fim))
    return planos, custo


def _bloco(fonte, trabalho, original):
    texto = fonte.texto[trabalho["inicio"]:trabalho["fim"]]
    texto_original = fonte.original[original["inicio"]:original["fim"]]
    return {"texto": texto, "trabalho": deepcopy(trabalho), "original": deepcopy(original),
            "texto_original": texto_original, "sha256_texto": _sha256(texto),
            "sha256_texto_original": _sha256(texto_original)}


def _limite(raio, selecionados, borda, total, direcao):
    if raio == 0:
        motivo = "raio_zero"
    elif selecionados == raio:
        motivo = "raio_atendido"
    elif (direcao == "anterior" and borda == 0) or (direcao == "seguinte" and borda == total):
        motivo = "inicio_documento" if direcao == "anterior" else "fim_documento"
    else:
        motivo = "fronteira_paragrafo"
    return {"raio_solicitado": raio, "quantidade_selecionada": selecionados, "motivo": motivo}


def _selecao_logica(fonte, foco, selecionados, politica):
    def limites(periodo):
        return {"original": deepcopy(periodo["original"]),
                "paragrafo_original": deepcopy(fonte.paragrafos[periodo["paragrafo_id"]]["original"])}
    return {
        "versao": IDENTIDADE_VERSION,
        "documento": {"documento_id": fonte.regras["documento_id"],
                      "sha256_original": fonte.preparacao["original"]["sha256"]},
        "foco": limites(foco), "periodos": [limites(p) for p in selecionados],
        "regras_segmentacao": deepcopy(fonte.segmentacao["processamento"]["regras"]),
        "politica_contexto": deepcopy(politica),
    }


def _relatorio(fonte, unidades, custo):
    return {"estado": "valido", "pronto_para_etapa_09": True,
            "verificacoes": _VERIFICACOES[:],
            "cobertura": {"periodos_total": len(fonte.periodos), "unidades_total": len(unidades),
                          "pendencias_total": sum(len(u["pendencias"]) for u in unidades),
                          "custo": custo},
            "limites": ["Disponibilizar contexto não resolve as pendências linguísticas.",
                        "A prontidão preserva a cobertura e as limitações das regras da origem.",
                        "Identidade lógica não substitui o hash do texto escolhido para vetorização."]}


def _gerar(fonte, execucao_id, data, configuracao):
    politica = _politica(configuracao)
    planos, custo = _planejar(fonte, configuracao)
    intervalos = []
    for foco, (inicio, fim) in zip(fonte.periodos, planos):
        intervalos.append((foco["trabalho"]["inicio"], foco["trabalho"]["fim"]))
        intervalos.append((fonte.periodos[inicio]["trabalho"]["inicio"], fonte.periodos[fim - 1]["trabalho"]["fim"]))
    try:
        originais = mapear_intervalos(fonte.preparacao, intervalos)
    except ErroPreparacao as erro:
        raise ErroEntrada(f"Mapeamento da preparação de origem inválido: {erro}") from erro
    unidades = []
    for i, (foco, (inicio, fim)) in enumerate(zip(fonte.periodos, planos)):
        selecionados = fonte.periodos[inicio:fim]
        periodo_ids = [p["id"] for p in selecionados]
        paragrafo_ids = list(dict.fromkeys(p["paragrafo_id"] for p in selecionados))
        ids_selecionados = set(periodo_ids)
        trabalho_janela = {"inicio": intervalos[2 * i + 1][0], "fim": intervalos[2 * i + 1][1]}
        bloco_foco = _bloco(fonte, foco["trabalho"], originais[2 * i])
        _exigir(bloco_foco["original"] == foco["original"], "O mapeamento do foco diverge da segmentação de origem.")
        bloco_foco.update(token_ids=foco["tokens"][:], anotacoes=deepcopy(fonte.anotacoes[foco["id"]]))
        bloco_janela = _bloco(fonte, trabalho_janela, originais[2 * i + 1])
        bloco_janela.update(periodo_ids=periodo_ids, paragrafo_ids=paragrafo_ids)
        selecao_logica = _selecao_logica(fonte, foco, selecionados, politica)
        contexto_disponivel = {
            "unidade_ids": [f"{execucao_id}:unidade:{p['ordem']}" for p in selecionados],
            "periodo_ids": periodo_ids, "paragrafo_ids": paragrafo_ids,
            "documento_id": fonte.regras["documento_id"],
        }
        pendencias, vinculos = [], []
        for periodo in selecionados:
            for ocorrencia in fonte.por_periodo[periodo["id"]]:
                for campo in _CAMPOS_PENDENCIAS:
                    for indice, mensagem in enumerate(ocorrencia[campo]):
                        pendencias.append({
                            "origem": {"regras_execucao_id": fonte.regras["execucao_id"],
                                       "ocorrencia_id": ocorrencia["id"], "periodo_id": periodo["id"],
                                       "campo": campo, "indice": indice},
                            "texto": mensagem, "papel": "foco" if periodo["id"] == foco["id"] else "vizinho",
                            "localizacao": "localizacao_indeterminada",
                            "contexto_disponivel": deepcopy(contexto_disponivel),
                        })
                for vinculo in ocorrencia["vinculos"]:
                    periodo_alvo = fonte.periodo_alvo(vinculo)
                    vinculos.append({"ocorrencia_id": ocorrencia["id"], "periodo_origem_id": periodo["id"],
                                     "vinculo": deepcopy(vinculo), "periodo_alvo_id": periodo_alvo,
                                     "localizacao": "na_janela" if periodo_alvo in ids_selecionados else "fora_da_janela"})
        unidades.append({
            "id": f"{execucao_id}:unidade:{i}", "ordem": i,
            "periodo_foco_id": foco["id"], "paragrafo_foco_id": foco["paragrafo_id"],
            "anteriores": [p["id"] for p in fonte.periodos[inicio:i]],
            "seguintes": [p["id"] for p in fonte.periodos[i + 1:fim]],
            "foco": bloco_foco, "janela": bloco_janela,
            "janela_logica_id": _sha256(_canonico(selecao_logica)), "selecao_logica": selecao_logica,
            "anotacoes_vizinhos": [{"periodo_id": p["id"], "anotacoes": deepcopy(fonte.anotacoes[p["id"]])}
                                   for p in selecionados if p["id"] != foco["id"]],
            "referencias": {**{c: fonte.regras[c] for c in _IDS_ORIGEM},
                            "regras_execucao_id": fonte.regras["execucao_id"], "paragrafo_id": foco["paragrafo_id"]},
            "limites": {
                "anterior": _limite(configuracao["raio_anterior"], i - inicio, inicio, len(fonte.periodos), "anterior"),
                "seguinte": _limite(configuracao["raio_seguinte"], fim - i - 1, fim, len(fonte.periodos), "seguinte"),
            },
            "pendencias": pendencias, "vinculos_disponiveis": vinculos,
        })
    return {
        "schema_version": SCHEMA_VERSION, "etapa": ETAPA,
        **{c: fonte.regras[c] for c in _IDS_ORIGEM},
        "regras_execucao_id": fonte.regras["execucao_id"], "execucao_id": execucao_id, "registrado_em": data,
        "regras": fonte.regras, "coordenadas": deepcopy(fonte.regras["coordenadas"]),
        "configuracao": configuracao, "politica": politica,
        "processamento": {
            "ferramenta": "biblioteca_padrao", "natureza": "organizacao_de_contexto_sem_inferencia",
            "modulo": {"nome": "unidades_contexto", "versao": MODULO_VERSION},
            "modelo_reexecutado": False, "integridade": {"algoritmo": "sha256", "codificacao": "utf-8"},
            "identidade_logica": {"versao": IDENTIDADE_VERSION, "algoritmo": "sha256",
                                  "serializacao": "json_chaves_ordenadas_separadores_compactos_unicode_utf8"},
            "limites_recursos": _limites_recursos(),
        },
        "unidades": unidades, "validacao": _relatorio(fonte, unidades, custo),
    }


def construir_unidades_contexto(regras, *, execucao_id=None, registrado_em=None,
                               raio_anterior=1, raio_seguinte=1, atravessar_paragrafos=False):
    """Construa uma unidade por período sobre uma cópia da origem pronta.

    Erros não persistem resultados parciais. Raios grandes são permitidos,
    respeitando os limites totais declarados antes de materializar a expansão.
    """
    configuracao = _configuracao(raio_anterior, raio_seguinte, atravessar_paragrafos)
    try:
        regras = deepcopy(regras)
    except (TypeError, ValueError, RecursionError) as erro:
        raise ErroEntrada("Não foi possível copiar a origem da etapa 07.") from erro
    _validar_fonte(regras)
    identificador = str(uuid4()) if execucao_id is None else _id(execucao_id, "execucao_id")
    _exigir(identificador != regras["execucao_id"], "A execução contextual deve ter ID próprio, distinto da etapa 07.")
    data = _data(registrado_em, gerar=True)
    registro = _gerar(_Fonte(regras), identificador, data, configuracao)
    _json_estrito(registro)
    return registro


def validar_unidades_contexto(registro):
    """Recalcule origem, seleção, referências, textos, hashes e prontidão.

    A reprodução canônica também recusa campos extras, coerções de tipos,
    referências estrangeiras e adulterações conjuntas de texto e hash.
    """
    _exigir(type(registro) is dict, "O registro precisa ser um objeto JSON.")
    _json_estrito(registro)
    _exigir(registro.get("schema_version") == SCHEMA_VERSION and registro.get("etapa") == ETAPA,
            "Formato ou etapa de contexto não suportado.")
    identificador = _id(registro.get("execucao_id"), "execucao_id")
    data = _data(registro.get("registrado_em"))
    regras = registro.get("regras")
    _validar_fonte(regras)
    _exigir(identificador != regras["execucao_id"], "A execução contextual deve ter ID próprio, distinto da etapa 07.")
    informado = registro.get("configuracao")
    _exigir(type(informado) is dict and set(informado) == {"raio_anterior", "raio_seguinte", "atravessar_paragrafos"},
            "Configuração de contexto ausente ou inválida.")
    configuracao = _configuracao(**informado)
    esperado = _gerar(_Fonte(regras), identificador, data, configuracao)
    _exigir(_canonico(registro) == _canonico(esperado),
            "Registro de contexto inconsistente: origem, política, unidades, textos, hashes, "
            "identidade lógica, anotações, pendências ou prontidão divergentes.")
    return deepcopy(esperado["validacao"])


def consultar_unidade_contexto(registro, *, unidade_id=None, periodo_id=None):
    """Consulte dentro da execução fornecida, devolvendo uma cópia defensiva."""
    _exigir((unidade_id is None) != (periodo_id is None), "Informe somente unidade_id ou periodo_id.")
    campo, valor = ("id", unidade_id) if unidade_id is not None else ("periodo_foco_id", periodo_id)
    _id(valor, "unidade_id" if campo == "id" else "periodo_id")
    validar_unidades_contexto(registro)
    for unidade in registro["unidades"]:
        if unidade[campo] == valor:
            return deepcopy(unidade)
    raise ErroContexto("A unidade ou o período não existe nesta execução contextual.")
