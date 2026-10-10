"""Etapa 04: tokens portugueses e segmentos com rastreabilidade literal.

O tokenizador é ``spacy.blank('pt')``, sem modelo nem pipeline estatístico.
As regras de períodos são determinísticas, sem confiança numérica: reticências
não encerram; títulos Dr./Dra./Sr./Sra. são protegidos; etc. encerra diante de
uma próxima palavra iniciada em maiúscula. Esses critérios não desambiguam
semanticamente abreviações ou citações. Aspas retas após pontuação são tratadas
como fechamento quando adjacentes; após espaço podem abrir a passagem seguinte.

Posições usam pontos de código Unicode, base zero, fim exclusivo. Tokens e
separadores particionam o trabalho. Períodos e parágrafos são camadas contendo
tokens e espaços internos; as camadas se sobrepõem por definição. Apenas LF e
CRLF delimitam linhas; CR isolado e U+2028/U+2029 continuam preservados.
"""

from bisect import bisect_right
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
import json
import re
from uuid import uuid4

from preparacao import ErroPreparacao, validar_registro


SCHEMA_VERSION = "1.0.0"
ETAPA = "04_tokenizacao_segmentacao"
VERSAO_REGRAS = "1.0.0"
MAX_CARACTERES = 2 * 1024 * 1024

_DELIMITADOR_PARAGRAFO = re.compile(r"(?:\r\n|\n)[ \t]*(?:\r\n|\n)(?:[ \t]*(?:\r\n|\n))*")
_PONTUACAO_TERMINAL = re.compile(r"[.!?]+\Z")
_TITULOS = frozenset({"dr", "dra", "sr", "sra"})
_FECHAMENTOS = frozenset({'"', "'", "”", "’", ")", "]", "}", "»", "›", "」", "』"})
_VERIFICACOES = (
    "estrutura_json_identificadores_e_data",
    "preparacao_textos_hashes_e_mapa",
    "coordenadas_e_processamento",
    "limites_ordem_identificadores_e_textos_exatos",
    "reconstrucao_por_tokens_e_separadores",
    "paragrafos_e_periodos_por_regras",
    "relacoes_e_contencao_das_unidades",
    "correspondencia_ao_original",
)


class ErroSegmentacao(ValueError):
    """Entrada inconsistente ou segmentação sem rastreabilidade válida."""


@dataclass(frozen=True)
class _Token:
    texto: str
    inicio: int
    fim: int


def _exigir(condicao, mensagem):
    if not condicao:
        raise ErroSegmentacao(mensagem)


def _objeto(valor, nome):
    _exigir(isinstance(valor, dict), f"{nome} deve ser um objeto JSON.")
    return valor


def _campo(objeto, chave, nome):
    _exigir(chave in objeto, f"Campo obrigatório ausente: {nome}.{chave}.")
    return objeto[chave]


def _inteiro(valor, nome):
    _exigir(type(valor) is int and valor >= 0, f"{nome} deve ser um inteiro não negativo.")
    return valor


def _identificador(valor, nome):
    _exigir(isinstance(valor, str) and bool(valor.strip()), f"{nome} deve ser uma string não vazia.")
    return valor


def _tipos_json(valor):
    if valor is None or isinstance(valor, (str, bool, int, float)):
        return
    if isinstance(valor, dict):
        for chave, item in valor.items():
            _exigir(isinstance(chave, str), "O registro contém uma chave JSON não textual.")
            _tipos_json(item)
    elif isinstance(valor, list):
        for item in valor:
            _tipos_json(item)
    else:
        raise ErroSegmentacao("O registro contém um valor que não pertence aos tipos JSON.")


def _conferir_json(valor):
    try:
        _tipos_json(valor)
        json.dumps(valor, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except ErroSegmentacao:
        raise
    except (TypeError, ValueError, OverflowError, RecursionError) as erro:
        raise ErroSegmentacao("O registro deve ser serializável em JSON UTF-8 estrito.") from erro


def _data_com_fuso(valor):
    _exigir(isinstance(valor, str), "registrado_em deve ser uma data ISO 8601 com fuso.")
    try:
        data = datetime.fromisoformat(valor.replace("Z", "+00:00"))
        _exigir(data.tzinfo is not None and data.utcoffset() is not None, "registrado_em deve conter fuso horário.")
    except ErroSegmentacao:
        raise
    except (TypeError, ValueError, OverflowError) as erro:
        raise ErroSegmentacao("registrado_em deve ser uma data ISO 8601 válida com fuso.") from erro
    return valor


def _nova_data(valor):
    if valor is None:
        valor = datetime.now(timezone.utc).isoformat()
    elif isinstance(valor, datetime):
        try:
            _exigir(valor.tzinfo is not None and valor.utcoffset() is not None, "registrado_em deve conter fuso horário.")
            valor = valor.isoformat()
        except (TypeError, ValueError, OverflowError) as erro:
            raise ErroSegmentacao("registrado_em deve ser uma data válida com fuso.") from erro
    return _data_com_fuso(valor)


def _conferir_limite(preparacao):
    _objeto(preparacao, "preparacao")
    trabalho = _objeto(_campo(preparacao, "trabalho", "preparacao"), "preparacao.trabalho")
    texto = _campo(trabalho, "texto", "preparacao.trabalho")
    _exigir(isinstance(texto, str), "preparacao.trabalho.texto deve ser uma string.")
    _exigir(
        len(texto) <= MAX_CARACTERES,
        f"O texto excede o limite de {MAX_CARACTERES} pontos de código; nenhum conteúdo foi truncado.",
    )
    return texto


def _validar_preparacao(preparacao):
    texto = _conferir_limite(preparacao)
    try:
        validar_registro(preparacao)
    except ErroPreparacao as erro:
        raise ErroSegmentacao(f"Preparação inválida: {erro}") from erro
    return texto


def _configuracoes():
    return {
        "max_caracteres": MAX_CARACTERES,
        "paragrafos": "linhas_em_branco_lf_ou_crlf_com_espacos_ascii_ou_tabulacoes",
        "limites_paragrafo": "primeiro_ao_ultimo_token_nao_espacial",
        "reticencias": "nao_encerram_por_si",
        "abreviacoes_protegidas": ["Dr.", "Dra.", "Sr.", "Sra."],
        "etc": "encerra_antes_de_proxima_palavra_com_inicial_maiuscula_ou_fim_paragrafo",
        "separadores": "complemento_dos_intervalos_dos_tokens_nao_espaciais",
        "fechamento_aspas_retas": "adjacente_a_pontuacao_terminal",
        "segmentador": "componente_proprio_sobre_tokens",
    }


@lru_cache(maxsize=1)
def _tokenizador():
    try:
        import spacy
    except ImportError as erro:
        raise ErroSegmentacao("spaCy não está instalado. Instale as dependências do projeto.") from erro
    try:
        idioma = spacy.blank("pt")
        idioma.max_length = MAX_CARACTERES
    except Exception as erro:
        raise ErroSegmentacao("Não foi possível iniciar o tokenizador português do spaCy.") from erro
    return idioma, spacy.__version__


def _tokenizar(texto):
    idioma, versao = _tokenizador()
    # Somente o tokenizador: não executa pipeline/modelo estatístico.
    try:
        documento = idioma.make_doc(texto)
    except Exception as erro:
        raise ErroSegmentacao("O tokenizador não conseguiu processar o texto integral.") from erro
    _exigir(documento.text == texto, "O documento do tokenizador diverge do texto de trabalho.")
    tokens = []
    for token in documento:
        if token.text.isspace():
            continue
        inicio, fim = token.idx, token.idx + len(token.text)
        _exigir(token.text == texto[inicio:fim], "O tokenizador produziu um token sem correspondência literal.")
        _exigir(inicio < fim, "O tokenizador produziu um token vazio.")
        tokens.append(_Token(token.text, inicio, fim))
    return tokens, versao


class _MapaOriginal:
    """Índice de um mapa previamente validado, sem revalidar por unidade."""

    def __init__(self, preparacao):
        self.trechos = preparacao["mapa_trechos"]
        self.inicios = [trecho["trabalho"]["inicio"] for trecho in self.trechos]
        self.comprimento = preparacao["trabalho"]["comprimento"]
        self.comprimento_original = preparacao["original"]["comprimento"]

    def intervalo(self, inicio, fim):
        _exigir(0 <= inicio <= fim <= self.comprimento, "Intervalo fora do mapa validado.")
        if inicio == fim:
            if inicio == self.comprimento:
                ponto = self.comprimento_original
            else:
                trecho = self.trechos[bisect_right(self.inicios, inicio) - 1]
                ponto = trecho["original"]["inicio"]
                if trecho["tipo"] == "copia":
                    ponto += inicio - trecho["trabalho"]["inicio"]
            return {"inicio": ponto, "fim": ponto}
        primeiro = self.trechos[bisect_right(self.inicios, inicio) - 1]
        ultimo = self.trechos[bisect_right(self.inicios, fim - 1) - 1]
        inicio_original = primeiro["original"]["inicio"]
        if primeiro["tipo"] == "copia":
            inicio_original += inicio - primeiro["trabalho"]["inicio"]
        fim_original = ultimo["original"]["fim"]
        if ultimo["tipo"] == "copia":
            fim_original = ultimo["original"]["inicio"] + fim - ultimo["trabalho"]["inicio"]
        return {"inicio": inicio_original, "fim": fim_original}


def _grupos_paragrafos(texto, tokens):
    """Retorne intervalos de índices dos tokens por bloco não vazio."""
    blocos = []
    cursor = 0
    for delimitador in _DELIMITADOR_PARAGRAFO.finditer(texto):
        blocos.append((cursor, delimitador.start()))
        cursor = delimitador.end()
    blocos.append((cursor, len(texto)))
    grupos = []
    bloco = 0
    inicio_grupo = None
    for numero, token in enumerate(tokens):
        while bloco < len(blocos) - 1 and token.inicio >= blocos[bloco][1]:
            if inicio_grupo is not None:
                grupos.append((inicio_grupo, numero))
                inicio_grupo = None
            bloco += 1
        inicio, fim = blocos[bloco]
        _exigir(
            inicio <= token.inicio < token.fim <= fim,
            "Um token atravessa um delimitador de parágrafo.",
        )
        if inicio_grupo is None:
            inicio_grupo = numero
    if inicio_grupo is not None:
        grupos.append((inicio_grupo, len(tokens)))
    return grupos


def _proxima_inicial_maiuscula(tokens, indice, fim):
    while indice < fim and tokens[indice].texto in _FECHAMENTOS:
        indice += 1
    if indice == fim:
        return True
    # Aspas/parênteses de abertura não escondem a inicial do próximo conteúdo.
    while indice < fim and tokens[indice].texto in {'"', "'", "“", "‘", "(", "[", "{", "«"}:
        indice += 1
    if indice == fim:
        return False
    for caractere in tokens[indice].texto:
        if caractere.isalpha():
            return caractere.isupper()
    return False


def _candidato_terminal(tokens, indice, inicio, fim):
    token = tokens[indice]
    texto = token.texto
    if texto.casefold() == "etc.":
        return _proxima_inicial_maiuscula(tokens, indice + 1, fim)
    if _PONTUACAO_TERMINAL.fullmatch(texto) is None:
        return False
    if set(texto) == {"."} and len(texto) > 1:
        return False
    if texto == "." and indice > inicio:
        anterior = tokens[indice - 1]
        if anterior.fim == token.inicio:
            palavra = anterior.texto.casefold().rstrip(".")
            if palavra in _TITULOS:
                return False
            if palavra == "etc":
                return _proxima_inicial_maiuscula(tokens, indice + 1, fim)
            # Guarda suplementar caso o tokenizador separe uma pontuação interna.
            if indice + 1 < fim:
                seguinte = tokens[indice + 1]
                if (
                    token.fim == seguinte.inicio and anterior.texto[-1:].isdigit()
                    and seguinte.texto[:1].isdigit()
                ):
                    return False
    return True


def _absorver_fechamentos(tokens, indice, fim):
    while indice + 1 < fim:
        atual, seguinte = tokens[indice], tokens[indice + 1]
        texto = seguinte.texto
        if _PONTUACAO_TERMINAL.fullmatch(texto) is not None:
            indice += 1
        elif texto in _FECHAMENTOS and (texto not in {'"', "'"} or atual.fim == seguinte.inicio):
            indice += 1
        else:
            break
    return indice


def _grupos_periodos(tokens, inicio, fim):
    grupos = []
    primeiro = inicio
    indice = inicio
    while indice < fim:
        if _candidato_terminal(tokens, indice, inicio, fim):
            ultimo = _absorver_fechamentos(tokens, indice, fim)
            grupos.append((primeiro, ultimo + 1, "pontuacao_terminal"))
            primeiro = ultimo + 1
            indice = ultimo
        indice += 1
    if primeiro < fim:
        grupos.append((primeiro, fim, "fim_paragrafo"))
    return grupos


def _id_unidade(segmentacao_id, tipo, ordem):
    return f"{segmentacao_id}:{tipo}:{ordem}"


def _unidade(segmentacao_id, tipo, ordem, inicio, fim, texto, mapa):
    return {
        "id": _id_unidade(segmentacao_id, tipo, ordem),
        "ordem": ordem,
        "texto": texto[inicio:fim],
        "trabalho": {"inicio": inicio, "fim": fim},
        "original": mapa.intervalo(inicio, fim),
    }


def _relatorio():
    return {
        "estado": "pronto_para_etapa_05",
        "pronto_para_etapa_05": True,
        "verificacoes": list(_VERIFICACOES),
    }


def segmentar_preparacao(preparacao, *, segmentacao_id=None, registrado_em=None):
    """Segmente uma preparação válida, sem modificá-la nem refazer a etapa 03.

    IDs e data podem ser fornecidos para testes reproduzíveis. O limite é de
    MAX_CARACTERES pontos de código; excessos geram erro, sem truncamento.
    A preparação é copiada integralmente, validada uma vez e mapeada por índice.
    """
    _conferir_limite(preparacao)
    try:
        copia = deepcopy(preparacao)
    except (TypeError, ValueError, RecursionError) as erro:
        raise ErroSegmentacao("Não foi possível preservar uma cópia da preparação.") from erro
    texto = _validar_preparacao(copia)
    if segmentacao_id is None:
        segmentacao_id = str(uuid4())
    _identificador(segmentacao_id, "segmentacao_id")
    data = _nova_data(registrado_em)
    tokens_brutos, versao = _tokenizar(texto)
    mapa = _MapaOriginal(copia)
    tokens = [
        _unidade(segmentacao_id, "token", numero, token.inicio, token.fim, texto, mapa)
        for numero, token in enumerate(tokens_brutos)
    ]
    separadores = []
    cursor = 0
    for token in tokens_brutos:
        if cursor < token.inicio:
            separadores.append(_unidade(segmentacao_id, "separador", len(separadores), cursor, token.inicio, texto, mapa))
        cursor = token.fim
    if cursor < len(texto):
        separadores.append(_unidade(segmentacao_id, "separador", len(separadores), cursor, len(texto), texto, mapa))

    paragrafos = []
    periodos = []
    for inicio, fim in _grupos_paragrafos(texto, tokens_brutos):
        paragrafo = _unidade(
            segmentacao_id, "paragrafo", len(paragrafos),
            tokens_brutos[inicio].inicio, tokens_brutos[fim - 1].fim, texto, mapa,
        )
        paragrafo["periodos"] = []
        for pi, pf, regra in _grupos_periodos(tokens_brutos, inicio, fim):
            periodo = _unidade(
                segmentacao_id, "periodo", len(periodos),
                tokens_brutos[pi].inicio, tokens_brutos[pf - 1].fim, texto, mapa,
            )
            periodo.update({
                "paragrafo_id": paragrafo["id"],
                "tokens": [token["id"] for token in tokens[pi:pf]],
                "regra_encerramento": regra,
            })
            for token in tokens[pi:pf]:
                token.update({"paragrafo_id": paragrafo["id"], "periodo_id": periodo["id"]})
            periodos.append(periodo)
            paragrafo["periodos"].append(periodo["id"])
        paragrafos.append(paragrafo)

    registro = {
        "schema_version": SCHEMA_VERSION,
        "etapa": ETAPA,
        "documento_id": copia["documento_id"],
        "preparacao_id": copia["preparacao_id"],
        "segmentacao_id": segmentacao_id,
        "registrado_em": data,
        "preparacao": copia,
        "coordenadas": deepcopy(copia["coordenadas"]),
        "processamento": {
            "ferramenta": "spacy",
            "versao": versao,
            "idioma": "pt",
            "modelo": None,
            "tokenizador": {"identificacao": "spacy.blank(pt)", "versao": versao, "ajustes": []},
            "regras": {"identificacao": "segmentacao_portugues_por_regras", "versao": VERSAO_REGRAS},
            "configuracoes": _configuracoes(),
        },
        "paragrafos": paragrafos,
        "periodos": periodos,
        "tokens": tokens,
        "separadores": separadores,
    }
    registro["validacao"] = _validar_segmentacao(registro, conferir_relatorio=False, preparacao_validada=True)
    return registro


def _intervalo(valor, nome, comprimento):
    _objeto(valor, nome)
    inicio = _inteiro(_campo(valor, "inicio", nome), f"{nome}.inicio")
    fim = _inteiro(_campo(valor, "fim", nome), f"{nome}.fim")
    _exigir(0 <= inicio < fim <= comprimento, f"{nome} deve ser um intervalo positivo dentro do texto.")
    return inicio, fim


def _conferir_processamento(valor):
    _objeto(valor, "processamento")
    _exigir(_campo(valor, "ferramenta", "processamento") == "spacy", "Ferramenta não suportada.")
    _exigir(_campo(valor, "idioma", "processamento") == "pt", "O idioma do processamento deve ser pt.")
    _exigir(_campo(valor, "modelo", "processamento") is None, "Esta etapa não utiliza modelo estatístico.")
    versao = _identificador(_campo(valor, "versao", "processamento"), "processamento.versao")
    _exigir(re.fullmatch(r"\d+\.\d+\.\d+(?:[a-zA-Z0-9.+-]*)", versao) is not None, "Versão do spaCy inválida.")
    tokenizador = _objeto(_campo(valor, "tokenizador", "processamento"), "processamento.tokenizador")
    _exigir(
        _campo(tokenizador, "identificacao", "processamento.tokenizador") == "spacy.blank(pt)"
        and _campo(tokenizador, "versao", "processamento.tokenizador") == versao
        and _campo(tokenizador, "ajustes", "processamento.tokenizador") == [],
        "A identificação, versão ou ajustes do tokenizador não correspondem às regras suportadas.",
    )
    regras = _objeto(_campo(valor, "regras", "processamento"), "processamento.regras")
    _exigir(
        _campo(regras, "identificacao", "processamento.regras") == "segmentacao_portugues_por_regras"
        and _campo(regras, "versao", "processamento.regras") == VERSAO_REGRAS,
        "A identificação ou versão das regras não é suportada.",
    )
    configuracoes = _objeto(_campo(valor, "configuracoes", "processamento"), "processamento.configuracoes")
    _exigir(configuracoes == _configuracoes(), "As configurações não correspondem à política suportada.")


def _validar_segmentacao(registro, *, conferir_relatorio, preparacao_validada=False):
    _objeto(registro, "registro")
    _conferir_json(registro)
    _exigir(_campo(registro, "schema_version", "registro") == SCHEMA_VERSION, "schema_version não suportada.")
    _exigir(_campo(registro, "etapa", "registro") == ETAPA, "Identificação da etapa inválida.")
    segmentacao_id = _identificador(_campo(registro, "segmentacao_id", "registro"), "segmentacao_id")
    _data_com_fuso(_campo(registro, "registrado_em", "registro"))
    preparacao = _campo(registro, "preparacao", "registro")
    texto = _conferir_limite(preparacao) if preparacao_validada else _validar_preparacao(preparacao)
    for chave in ("documento_id", "preparacao_id"):
        recebido = _campo(registro, chave, "registro")
        _exigir(
            type(recebido) is type(preparacao[chave]) and recebido == preparacao[chave],
            f"{chave} diverge da preparação preservada.",
        )
    coordenadas = _objeto(_campo(registro, "coordenadas", "registro"), "coordenadas")
    _exigir(
        coordenadas == preparacao["coordenadas"] and type(coordenadas.get("base")) is int
        and coordenadas.get("fim_exclusivo") is True,
        "A convenção de coordenadas diverge da preparação.",
    )
    _conferir_processamento(_campo(registro, "processamento", "registro"))
    mapa = _MapaOriginal(preparacao)
    colecoes = {}
    identificadores = set()
    for plural, tipo in (("paragrafos", "paragrafo"), ("periodos", "periodo"), ("tokens", "token"), ("separadores", "separador")):
        unidades = _campo(registro, plural, "registro")
        _exigir(isinstance(unidades, list), f"{plural} deve ser uma lista.")
        anterior_fim = 0
        for numero, unidade in enumerate(unidades):
            nome = f"{plural}[{numero}]"
            _objeto(unidade, nome)
            ordem = _inteiro(_campo(unidade, "ordem", nome), f"{nome}.ordem")
            _exigir(ordem == numero, f"{nome}.ordem deve seguir base zero sem lacunas.")
            identificador = _identificador(_campo(unidade, "id", nome), f"{nome}.id")
            _exigir(identificador == _id_unidade(segmentacao_id, tipo, numero), f"{nome}.id não corresponde à execução e ordem.")
            _exigir(identificador not in identificadores, f"{nome}.id está duplicado.")
            identificadores.add(identificador)
            inicio, fim = _intervalo(_campo(unidade, "trabalho", nome), f"{nome}.trabalho", len(texto))
            _exigir(inicio >= anterior_fim, f"{nome} está fora de ordem ou sobrepõe uma unidade da mesma coleção.")
            anterior_fim = fim
            conteudo = _campo(unidade, "texto", nome)
            _exigir(isinstance(conteudo, str) and conteudo == texto[inicio:fim], f"{nome}.texto diverge do intervalo exato.")
            original = _campo(unidade, "original", nome)
            _intervalo(original, f"{nome}.original", preparacao["original"]["comprimento"])
            _exigir(original == mapa.intervalo(inicio, fim), f"{nome}.original diverge do mapa da etapa 03.")
            if tipo == "token":
                _exigir(not conteudo.isspace(), f"{nome} é um token exclusivamente espacial.")
            elif tipo == "separador":
                _exigir(conteudo.isspace(), f"{nome} contém conteúdo que não é espaço ou separador.")
        colecoes[plural] = unidades

    tokens = colecoes["tokens"]
    separadores = colecoes["separadores"]
    cursor = 0
    for unidade in sorted(tokens + separadores, key=lambda item: item["trabalho"]["inicio"]):
        inicio, fim = unidade["trabalho"]["inicio"], unidade["trabalho"]["fim"]
        _exigir(inicio == cursor, "Tokens e separadores apresentam uma lacuna ou sobreposição.")
        cursor = fim
    _exigir(cursor == len(texto), "Tokens e separadores não reconstroem integralmente o trabalho.")

    tokens_brutos = [_Token(token["texto"], token["trabalho"]["inicio"], token["trabalho"]["fim"]) for token in tokens]
    grupos = _grupos_paragrafos(texto, tokens_brutos)
    paragrafos, periodos = colecoes["paragrafos"], colecoes["periodos"]
    _exigir(len(paragrafos) == len(grupos), "A coleção de parágrafos diverge dos delimitadores e tokens.")
    indice_periodo = 0
    for numero, (inicio, fim) in enumerate(grupos):
        paragrafo = paragrafos[numero]
        _exigir(
            paragrafo["trabalho"] == {"inicio": tokens_brutos[inicio].inicio, "fim": tokens_brutos[fim - 1].fim},
            "O parágrafo não vai do primeiro ao último token do bloco.",
        )
        ids_periodos = []
        for pi, pf, regra in _grupos_periodos(tokens_brutos, inicio, fim):
            _exigir(indice_periodo < len(periodos), "Há períodos ausentes para os tokens do parágrafo.")
            periodo = periodos[indice_periodo]
            _exigir(
                periodo["trabalho"] == {"inicio": tokens_brutos[pi].inicio, "fim": tokens_brutos[pf - 1].fim},
                "Os limites do período divergem das regras identificadas.",
            )
            _exigir(_campo(periodo, "regra_encerramento", "periodo") == regra, "A regra de encerramento do período é inconsistente.")
            _exigir(_campo(periodo, "paragrafo_id", "periodo") == paragrafo["id"], "O período aponta para outro parágrafo.")
            ids_tokens = [token["id"] for token in tokens[pi:pf]]
            _exigir(_campo(periodo, "tokens", "periodo") == ids_tokens, "As relações entre período e tokens estão incorretas.")
            for token in tokens[pi:pf]:
                _exigir(
                    _campo(token, "paragrafo_id", "token") == paragrafo["id"]
                    and _campo(token, "periodo_id", "token") == periodo["id"],
                    "Um token não corresponde a seu período e parágrafo.",
                )
            ids_periodos.append(periodo["id"])
            indice_periodo += 1
        _exigir(_campo(paragrafo, "periodos", "paragrafo") == ids_periodos, "As relações entre parágrafo e períodos estão incorretas.")
    _exigir(indice_periodo == len(periodos), "Existem períodos sem vínculo com um parágrafo.")

    relatorio = _relatorio()
    if conferir_relatorio:
        informado = _objeto(_campo(registro, "validacao", "registro"), "validacao")
        _exigir(
            _campo(informado, "estado", "validacao") == relatorio["estado"]
            and _campo(informado, "pronto_para_etapa_05", "validacao") is True
            and _campo(informado, "verificacoes", "validacao") == relatorio["verificacoes"],
            "O relatório informado não corresponde à validação estrutural realizada.",
        )
    return relatorio


def validar_segmentacao(registro):
    """Revalide estrutura, texto, preparação, mapa e relações, sem mutação.

    Não exige spaCy instalado para ler registros existentes e não retokeniza.
    A prontidão estrutural não representa perfeição linguística das regras.
    """
    return _validar_segmentacao(registro, conferir_relatorio=True)


def contexto_periodo(registro, periodo_id):
    """Retorne período, anterior e seguinte na ordem global, como cópias.

    Nas bordas, o vizinho inexistente é None. Os vizinhos podem pertencer a
    parágrafos diferentes; os períodos em si nunca atravessam parágrafos.
    """
    validar_segmentacao(registro)
    _identificador(periodo_id, "periodo_id")
    periodos = registro["periodos"]
    for numero, periodo in enumerate(periodos):
        if periodo["id"] == periodo_id:
            return {
                "anterior": deepcopy(periodos[numero - 1]) if numero else None,
                "atual": deepcopy(periodo),
                "seguinte": deepcopy(periodos[numero + 1]) if numero + 1 < len(periodos) else None,
            }
    raise ErroSegmentacao("O período solicitado não existe nesta segmentação.")
