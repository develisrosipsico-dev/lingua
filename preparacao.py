"""Preparação literal de textos, antes de qualquer análise linguística.

As posições são índices Python de ``str``: pontos de código Unicode, com base
zero e fim exclusivo, ``[inicio, fim)``. Um emoji fora do plano básico ocupa um
ponto de código, mas pode ocupar duas unidades UTF-16; um caractere combinante
ocupa sua própria posição. Grafemas visuais e bytes UTF-8 não são coordenadas
deste formato. Consumidores que usam bytes ou UTF-16 devem converter os limites
com os prefixos do texto correspondente, por exemplo ``len(texto[:p].encode(
"utf-8"))`` ou ``len(texto[:p].encode("utf-16-le")) // 2``. Não use esses
comprimentos diretamente como índices de ``mapa_trechos``.

Este módulo usa somente a biblioteca padrão. Não corrige, normaliza Unicode,
tokeniza, classifica ou interpreta o conteúdo. A única transformação permitida
é a conversão opcional de CRLF em LF, com rastreabilidade até o original.
"""

from bisect import bisect_right
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from uuid import uuid4


SCHEMA_VERSION = "1.0.0"
VERSAO_POLITICA = "1.0.0"
VERSAO_REGRA_CRLF = "1.0.0"
COORDENADAS = {
    "unidade": "pontos_de_codigo_unicode",
    "base": 0,
    "fim_exclusivo": True,
}

_VERIFICACOES = (
    "estrutura_json",
    "schema_identificadores_e_data",
    "metadados_origem",
    "versoes_comprimentos_e_hashes_utf8",
    "convencao_de_coordenadas",
    "politica_e_resultado_permitido",
    "limites_ordem_e_cobertura_dos_intervalos",
    "igualdade_dos_trechos_copiados",
    "transformacoes_e_alteracoes_correspondentes",
)


class ErroPreparacao(ValueError):
    """Entrada inválida ou registro que não preserva sua rastreabilidade."""


def _exigir(condicao, mensagem):
    if not condicao:
        raise ErroPreparacao(mensagem)


def _dicionario(valor, nome):
    _exigir(isinstance(valor, dict), f"{nome} deve ser um objeto JSON.")
    return valor


def _campo(objeto, chave, nome):
    _exigir(chave in objeto, f"Campo obrigatório ausente: {nome}.{chave}.")
    return objeto[chave]


def _inteiro(valor, nome, minimo=0):
    _exigir(
        isinstance(valor, int) and not isinstance(valor, bool) and valor >= minimo,
        f"{nome} deve ser um inteiro maior ou igual a {minimo}.",
    )
    return valor


def _string_nao_vazia(valor, nome):
    _exigir(
        isinstance(valor, str) and bool(valor.strip()),
        f"{nome} deve ser uma string não vazia.",
    )
    return valor


def _documento_id(valor, nome="documento_id"):
    if isinstance(valor, str):
        return _string_nao_vazia(valor, nome)
    return _inteiro(valor, nome)


def _valor_json(valor, nome, ancestrais=None):
    """Rejeita coerções, ciclos e números que JSON estrito não representa."""
    if valor is None or isinstance(valor, (str, bool, int)):
        return
    if isinstance(valor, float):
        _exigir(math.isfinite(valor), f"{nome} contém um número não finito.")
        return
    _exigir(
        isinstance(valor, (dict, list)),
        f"{nome} contém um valor que não pertence aos tipos JSON.",
    )
    if ancestrais is None:
        ancestrais = set()
    identificador = id(valor)
    _exigir(identificador not in ancestrais, f"{nome} contém uma referência circular.")
    ancestrais.add(identificador)
    try:
        if isinstance(valor, dict):
            for chave, item in valor.items():
                _exigir(isinstance(chave, str), f"{nome} contém uma chave não textual.")
                _valor_json(item, nome, ancestrais)
        else:
            for item in valor:
                _valor_json(item, nome, ancestrais)
    finally:
        ancestrais.remove(identificador)


def _conferir_json(valor, nome):
    try:
        _valor_json(valor, nome)
        # Além de allow_nan=False, confira que o JSON pode ser escrito em UTF-8.
        json.dumps(valor, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except ErroPreparacao:
        raise
    except (TypeError, ValueError, OverflowError, RecursionError) as erro:
        raise ErroPreparacao(f"{nome} não é serializável em JSON UTF-8 estrito.") from erro


def _copiar_metadados(metadados):
    if metadados is None:
        return {}
    _dicionario(metadados, "metadados")
    _conferir_json(metadados, "metadados")
    return json.loads(json.dumps(metadados, ensure_ascii=False, allow_nan=False))


def _sha256(texto):
    try:
        return hashlib.sha256(texto.encode("utf-8")).hexdigest()
    except UnicodeError as erro:
        raise ErroPreparacao("O texto não pode ser codificado em UTF-8 estrito.") from erro


def _data_com_fuso(valor, nome="registrado_em"):
    _exigir(isinstance(valor, str), f"{nome} deve ser uma data ISO 8601 com fuso.")
    try:
        data = datetime.fromisoformat(valor.replace("Z", "+00:00"))
        _exigir(
            data.tzinfo is not None and data.utcoffset() is not None,
            f"{nome} deve conter explicitamente o fuso horário.",
        )
    except ErroPreparacao:
        raise
    except (TypeError, ValueError, OverflowError) as erro:
        raise ErroPreparacao(f"{nome} deve ser uma data ISO 8601 válida com fuso.") from erro
    return valor


def _registrado_em(valor):
    if valor is None:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(valor, datetime):
        _exigir(
            valor.tzinfo is not None and valor.utcoffset() is not None,
            "registrado_em deve conter explicitamente o fuso horário.",
        )
        valor = valor.isoformat()
    return _data_com_fuso(valor)


def _intervalo(inicio, fim):
    return {"inicio": inicio, "fim": fim}


def _conferir_intervalo(valor, nome, comprimento, *, positivo=False):
    _dicionario(valor, nome)
    inicio = _inteiro(_campo(valor, "inicio", nome), f"{nome}.inicio")
    fim = _inteiro(_campo(valor, "fim", nome), f"{nome}.fim")
    _exigir(inicio <= fim <= comprimento, f"{nome} está fora dos limites do texto.")
    if positivo:
        _exigir(inicio < fim, f"{nome} deve ter comprimento positivo.")
    return inicio, fim


def _conferir_metadados(metadados, documento_id, original):
    _dicionario(metadados, "metadados_origem")
    if "documento_id" in metadados:
        recebido = _documento_id(metadados["documento_id"], "metadados_origem.documento_id")
        _exigir(
            type(recebido) is type(documento_id) and recebido == documento_id,
            "O documento_id dos metadados diverge do identificador recebido.",
        )
    if "versao" in metadados:
        versao = _inteiro(metadados["versao"], "metadados_origem.versao", 1)
        _exigir(versao == original["versao"], "A versão original diverge dos metadados.")
    if "comprimento" in metadados:
        comprimento = _inteiro(metadados["comprimento"], "metadados_origem.comprimento")
        _exigir(
            comprimento == original["comprimento"],
            "O comprimento original diverge dos metadados.",
        )
    if "sha256" in metadados:
        sha256 = metadados["sha256"]
        _exigir(
            isinstance(sha256, str) and re.fullmatch(r"[0-9a-fA-F]{64}", sha256) is not None,
            "metadados_origem.sha256 deve conter um SHA-256 hexadecimal de 64 caracteres.",
        )
        _exigir(
            sha256.lower() == original["sha256"],
            "O SHA-256 original diverge dos metadados.",
        )


def _conferir_texto(valor, nome):
    _dicionario(valor, nome)
    texto = _campo(valor, "texto", nome)
    _exigir(isinstance(texto, str), f"{nome}.texto deve ser uma string.")
    _inteiro(_campo(valor, "versao", nome), f"{nome}.versao", 1)
    comprimento = _inteiro(_campo(valor, "comprimento", nome), f"{nome}.comprimento")
    _exigir(comprimento == len(texto), f"{nome}.comprimento diverge do texto exato.")
    sha256 = _campo(valor, "sha256", nome)
    _exigir(
        isinstance(sha256, str) and re.fullmatch(r"[0-9a-f]{64}", sha256) is not None,
        f"{nome}.sha256 deve ser um SHA-256 hexadecimal em minúsculas.",
    )
    _exigir(sha256 == _sha256(texto), f"{nome}.sha256 diverge do texto UTF-8 exato.")
    return texto


def _relatorio_validacao():
    return {
        "estado": "pronto_para_etapa_04",
        "pronto_para_etapa_04": True,
        "verificacoes": list(_VERIFICACOES),
    }


def _validar_registro(registro, *, conferir_relatorio):
    _dicionario(registro, "registro")
    _conferir_json(registro, "registro")
    _exigir(
        _campo(registro, "schema_version", "registro") == SCHEMA_VERSION,
        "schema_version não é suportada; esperado 1.0.0.",
    )
    documento_id = _documento_id(_campo(registro, "documento_id", "registro"))
    _string_nao_vazia(_campo(registro, "preparacao_id", "registro"), "preparacao_id")
    _data_com_fuso(_campo(registro, "registrado_em", "registro"))

    original = _campo(registro, "original", "registro")
    trabalho = _campo(registro, "trabalho", "registro")
    texto_original = _conferir_texto(original, "original")
    texto_trabalho = _conferir_texto(trabalho, "trabalho")
    metadados = _campo(registro, "metadados_origem", "registro")
    _conferir_metadados(metadados, documento_id, original)

    coordenadas = _dicionario(_campo(registro, "coordenadas", "registro"), "coordenadas")
    _exigir(
        set(coordenadas) == set(COORDENADAS)
        and coordenadas["unidade"] == COORDENADAS["unidade"]
        and type(coordenadas["base"]) is int and coordenadas["base"] == 0
        and coordenadas["fim_exclusivo"] is True,
        "coordenadas deve usar pontos de código Unicode, base zero e fim exclusivo.",
    )
    politica = _dicionario(_campo(registro, "politica", "registro"), "politica")
    _exigir(
        _campo(politica, "identificacao", "politica") == "preservacao_literal"
        and _campo(politica, "versao", "politica") == VERSAO_POLITICA,
        "A identificação ou versão da política não é suportada.",
    )
    opcoes = _dicionario(_campo(politica, "opcoes", "politica"), "politica.opcoes")
    _exigir(
        set(opcoes) == {"normalizar_crlf"} and type(opcoes["normalizar_crlf"]) is bool,
        "politica.opcoes deve conter somente normalizar_crlf, com valor booleano.",
    )
    normalizar_crlf = opcoes["normalizar_crlf"]
    esperado = texto_original.replace("\r\n", "\n") if normalizar_crlf else texto_original
    _exigir(
        texto_trabalho == esperado,
        "O texto de trabalho não corresponde à transformação permitida pela política.",
    )
    _exigir(
        trabalho["versao"] == original["versao"] + int(normalizar_crlf),
        "A versão do texto de trabalho não corresponde à política utilizada.",
    )

    mapa = _campo(registro, "mapa_trechos", "registro")
    _exigir(isinstance(mapa, list), "mapa_trechos deve ser uma lista.")
    cursor_original = 0
    cursor_trabalho = 0
    transformacoes = []
    ids_transformacoes = set()
    for numero, trecho in enumerate(mapa):
        nome = f"mapa_trechos[{numero}]"
        _dicionario(trecho, nome)
        oi, of = _conferir_intervalo(
            _campo(trecho, "original", nome), f"{nome}.original", len(texto_original),
            positivo=True,
        )
        ti, tf = _conferir_intervalo(
            _campo(trecho, "trabalho", nome), f"{nome}.trabalho", len(texto_trabalho),
            positivo=True,
        )
        _exigir(
            oi == cursor_original and ti == cursor_trabalho,
            f"{nome} viola a ordem ou a cobertura contínua dos textos.",
        )
        tipo = _campo(trecho, "tipo", nome)
        alteracao_id = _campo(trecho, "alteracao_id", nome)
        if tipo == "copia":
            _exigir(alteracao_id is None, f"{nome}: cópias não podem ter alteracao_id.")
            _exigir(
                texto_original[oi:of] == texto_trabalho[ti:tf],
                f"{nome}: os conteúdos de um trecho copiado são diferentes.",
            )
        elif tipo == "transformacao_tecnica":
            _exigir(normalizar_crlf, f"{nome}: a política não permite transformações.")
            _string_nao_vazia(alteracao_id, f"{nome}.alteracao_id")
            _exigir(
                alteracao_id not in ids_transformacoes,
                f"{nome}: alteracao_id está duplicado no mapa.",
            )
            _exigir(
                texto_original[oi:of] == "\r\n" and texto_trabalho[ti:tf] == "\n",
                f"{nome}: transformação diferente de CRLF para LF.",
            )
            ids_transformacoes.add(alteracao_id)
            transformacoes.append(trecho)
        else:
            raise ErroPreparacao(f"{nome}.tipo não é suportado.")
        cursor_original, cursor_trabalho = of, tf
    _exigir(
        cursor_original == len(texto_original) and cursor_trabalho == len(texto_trabalho),
        "mapa_trechos não cobre integralmente os dois textos.",
    )

    alteracoes = _campo(registro, "alteracoes", "registro")
    _exigir(isinstance(alteracoes, list), "alteracoes deve ser uma lista.")
    _exigir(
        len(alteracoes) == len(transformacoes),
        "Cada transformação deve corresponder a exatamente uma alteração registrada.",
    )
    ids_alteracoes = set()
    for numero, (alteracao, trecho) in enumerate(zip(alteracoes, transformacoes)):
        nome = f"alteracoes[{numero}]"
        _dicionario(alteracao, nome)
        identificador = _string_nao_vazia(_campo(alteracao, "id", nome), f"{nome}.id")
        _exigir(identificador not in ids_alteracoes, f"{nome}.id está duplicado.")
        ids_alteracoes.add(identificador)
        _exigir(
            identificador == trecho["alteracao_id"],
            f"{nome}: vínculo ou ordem da alteração diverge do mapa.",
        )
        _exigir(
            _campo(alteracao, "operacao", nome) == "crlf_para_lf"
            and _campo(alteracao, "versao_regra", nome) == VERSAO_REGRA_CRLF,
            f"{nome}: operação ou versão da regra não é suportada.",
        )
        _string_nao_vazia(_campo(alteracao, "motivo", nome), f"{nome}.motivo")
        anterior = _campo(alteracao, "antes", nome)
        posterior = _campo(alteracao, "depois", nome)
        _exigir(
            anterior == "\r\n" and posterior == "\n",
            f"{nome}: conteúdos anterior ou posterior não correspondem à regra.",
        )
        oi, of = _conferir_intervalo(
            _campo(alteracao, "original", nome), f"{nome}.original", len(texto_original),
            positivo=True,
        )
        ti, tf = _conferir_intervalo(
            _campo(alteracao, "trabalho", nome), f"{nome}.trabalho", len(texto_trabalho),
            positivo=True,
        )
        _exigir(
            alteracao["original"] == trecho["original"]
            and alteracao["trabalho"] == trecho["trabalho"],
            f"{nome}: intervalos da alteração divergem do mapa.",
        )
        _exigir(
            anterior == texto_original[oi:of] and posterior == texto_trabalho[ti:tf],
            f"{nome}: conteúdos da alteração divergem dos intervalos dos textos.",
        )

    relatorio = _relatorio_validacao()
    if conferir_relatorio:
        informado = _dicionario(_campo(registro, "validacao", "registro"), "validacao")
        _exigir(
            _campo(informado, "estado", "validacao") == relatorio["estado"]
            and _campo(informado, "pronto_para_etapa_04", "validacao") is True
            and _campo(informado, "verificacoes", "validacao") == relatorio["verificacoes"],
            "O relatório de validação informado diverge das verificações realizadas.",
        )
    return relatorio


def validar_registro(registro):
    """Confira um registro completo, inclusive após leitura do JSON.

    Retorna um novo relatório de validação, sem modificar o registro. A presença
    de ``pronto_para_etapa_04`` nunca substitui a conferência efetiva. Qualquer
    entrada inválida ou inconsistência produz ``ErroPreparacao``.
    """
    return _validar_registro(registro, conferir_relatorio=True)


def preparar_texto(
    texto_original, documento_id, *, metadados=None, normalizar_crlf=False,
    preparacao_id=None, registrado_em=None,
):
    """Crie e valide uma nova preparação sem modificar texto ou metadados.

    ``documento_id`` aceita uma string não vazia ou inteiro não negativo.
    ``preparacao_id`` pode ser fornecido pelo chamador ou é um UUID4 novo.
    ``registrado_em`` aceita datetime com fuso ou uma string ISO 8601 com fuso;
    quando omitida, utiliza a data corrente em UTC. IDs das alterações são
    derivados do ID da preparação e sua ordem. Fornecer data e identificadores
    torna o registro reproduzível.

    ``metadados`` preserva os dados JSON da etapa anterior em uma cópia
    independente. Quando presentes, ``documento_id``, ``versao``, ``comprimento``
    e ``sha256`` são conferidos. O hash recebido aceita letras maiúsculas, sem
    modificar os metadados recebidos. A versão original é reutilizada; uma
    preparação com a opção CRLF ativa cria a versão de trabalho seguinte,
    mesmo quando o texto não contém CRLF. A opção começa desativada.
    """
    _exigir(isinstance(texto_original, str), "texto_original deve ser uma string.")
    _documento_id(documento_id)
    _exigir(type(normalizar_crlf) is bool, "normalizar_crlf deve ser um booleano.")
    if preparacao_id is None:
        preparacao_id = str(uuid4())
    _string_nao_vazia(preparacao_id, "preparacao_id")
    data = _registrado_em(registrado_em)
    metadados_origem = _copiar_metadados(metadados)
    versao_original = _inteiro(metadados_origem.get("versao", 1), "metadados.versao", 1)
    original = {
        "texto": texto_original,
        "versao": versao_original,
        "comprimento": len(texto_original),
        "sha256": _sha256(texto_original),
    }
    _conferir_metadados(metadados_origem, documento_id, original)

    mapa = []
    alteracoes = []
    partes = []
    cursor_original = 0
    cursor_trabalho = 0
    while cursor_original < len(texto_original):
        proximo = texto_original.find("\r\n", cursor_original) if normalizar_crlf else -1
        fim_copia = proximo if proximo >= 0 else len(texto_original)
        if cursor_original < fim_copia:
            copia = texto_original[cursor_original:fim_copia]
            fim_trabalho = cursor_trabalho + len(copia)
            partes.append(copia)
            mapa.append({
                "tipo": "copia",
                "trabalho": _intervalo(cursor_trabalho, fim_trabalho),
                "original": _intervalo(cursor_original, fim_copia),
                "alteracao_id": None,
            })
            cursor_original, cursor_trabalho = fim_copia, fim_trabalho
        if proximo < 0:
            break
        identificador = f"{preparacao_id}:alteracao:{len(alteracoes) + 1}"
        intervalo_original = _intervalo(cursor_original, cursor_original + 2)
        intervalo_trabalho = _intervalo(cursor_trabalho, cursor_trabalho + 1)
        partes.append("\n")
        mapa.append({
            "tipo": "transformacao_tecnica",
            "trabalho": dict(intervalo_trabalho),
            "original": dict(intervalo_original),
            "alteracao_id": identificador,
        })
        alteracoes.append({
            "id": identificador,
            "operacao": "crlf_para_lf",
            "versao_regra": VERSAO_REGRA_CRLF,
            "motivo": "Conversão técnica opcional de CRLF em LF solicitada pelo chamador.",
            "antes": "\r\n",
            "depois": "\n",
            "original": intervalo_original,
            "trabalho": intervalo_trabalho,
        })
        cursor_original += 2
        cursor_trabalho += 1

    texto_trabalho = "".join(partes)
    registro = {
        "schema_version": SCHEMA_VERSION,
        "documento_id": documento_id,
        "preparacao_id": preparacao_id,
        "registrado_em": data,
        "original": original,
        "trabalho": {
            "texto": texto_trabalho,
            "versao": versao_original + int(normalizar_crlf),
            "comprimento": len(texto_trabalho),
            "sha256": _sha256(texto_trabalho),
        },
        "coordenadas": dict(COORDENADAS),
        "politica": {
            "identificacao": "preservacao_literal",
            "versao": VERSAO_POLITICA,
            "opcoes": {"normalizar_crlf": normalizar_crlf},
        },
        "metadados_origem": metadados_origem,
        "mapa_trechos": mapa,
        "alteracoes": alteracoes,
    }
    # O estado de prontidão só existe depois de todas as verificações passarem.
    registro["validacao"] = _validar_registro(registro, conferir_relatorio=False)
    return registro


def mapear_intervalo(registro, inicio, fim):
    """Recupere ``[inicio, fim)`` do trabalho como um intervalo no original.

    As posições usam pontos de código Unicode, não bytes nem unidades UTF-16.
    Toda transformação intersectada por um intervalo não vazio contribui com
    seu intervalo original completo. Um intervalo vazio mapeia a posição da
    borda, sem incluir caracteres; no documento vazio retorna ``[0, 0)``.
    Confira sempre o conteúdo do registro antes de confiar em suas posições.
    """
    validar_registro(registro)
    comprimento = registro["trabalho"]["comprimento"]
    _conferir_intervalo(_intervalo(inicio, fim), "intervalo", comprimento)
    if inicio == fim:
        if inicio == comprimento:
            ponto = registro["original"]["comprimento"]
        else:
            ponto = None
            for trecho in registro["mapa_trechos"]:
                ti, tf = trecho["trabalho"]["inicio"], trecho["trabalho"]["fim"]
                if ti <= inicio < tf:
                    oi = trecho["original"]["inicio"]
                    ponto = oi + inicio - ti if trecho["tipo"] == "copia" else oi
                    break
            _exigir(ponto is not None, "O mapa não contém a posição solicitada.")
        return _intervalo(ponto, ponto)

    inicio_original = None
    fim_original = None
    for trecho in registro["mapa_trechos"]:
        ti, tf = trecho["trabalho"]["inicio"], trecho["trabalho"]["fim"]
        intersecao_inicio, intersecao_fim = max(inicio, ti), min(fim, tf)
        if intersecao_inicio >= intersecao_fim:
            continue
        oi, of = trecho["original"]["inicio"], trecho["original"]["fim"]
        if trecho["tipo"] == "copia":
            oi += intersecao_inicio - ti
            of = trecho["original"]["inicio"] + intersecao_fim - ti
        if inicio_original is None:
            inicio_original = oi
        fim_original = of
    _exigir(inicio_original is not None, "O mapa não contém o intervalo solicitado.")
    return _intervalo(inicio_original, fim_original)


def mapear_intervalos(registro, intervalos):
    """Mapeie uma lista ou tupla de pares ``(inicio, fim)`` ao original.

    Os resultados são uma lista de objetos ``{inicio, fim}``, na mesma ordem
    dos pares recebidos. A convenção e o tratamento das transformações e dos
    intervalos vazios são exatamente os de :func:`mapear_intervalo`.

    Valida o registro uma única vez, inclusive para um lote vazio, e confere
    todos os pares antes de produzir os resultados. O índice usa busca binária
    nas bordas do mapa, sem percorrer novamente o texto para cada intervalo.
    O registro e os pares recebidos permanecem intactos.
    """
    validar_registro(registro)
    _exigir(
        isinstance(intervalos, (list, tuple)),
        "intervalos deve ser uma lista ou tupla de pares (inicio, fim).",
    )
    comprimento = registro["trabalho"]["comprimento"]
    for numero, par in enumerate(intervalos):
        nome = f"intervalos[{numero}]"
        _exigir(
            isinstance(par, (list, tuple)) and len(par) == 2,
            f"{nome} deve ser um par (inicio, fim).",
        )
        _conferir_intervalo(_intervalo(par[0], par[1]), nome, comprimento)

    trechos = registro["mapa_trechos"]
    inicios = [trecho["trabalho"]["inicio"] for trecho in trechos]
    resultados = []
    for inicio, fim in intervalos:
        if inicio == fim:
            if inicio == comprimento:
                ponto = registro["original"]["comprimento"]
            else:
                trecho = trechos[bisect_right(inicios, inicio) - 1]
                ponto = trecho["original"]["inicio"]
                if trecho["tipo"] == "copia":
                    ponto += inicio - trecho["trabalho"]["inicio"]
            resultados.append(_intervalo(ponto, ponto))
            continue

        primeiro = trechos[bisect_right(inicios, inicio) - 1]
        ultimo = trechos[bisect_right(inicios, fim - 1) - 1]
        inicio_original = primeiro["original"]["inicio"]
        if primeiro["tipo"] == "copia":
            inicio_original += inicio - primeiro["trabalho"]["inicio"]
        fim_original = ultimo["original"]["fim"]
        if ultimo["tipo"] == "copia":
            fim_original = ultimo["original"]["inicio"] + fim - ultimo["trabalho"]["inicio"]
        resultados.append(_intervalo(inicio_original, fim_original))
    return resultados


def ler_texto_original(caminho, *, encoding="utf-8"):
    """Leia o texto sem substituir CRLF, CR isolado ou outras quebras de linha.

    A decodificação é estrita, sem remover BOM ou aplicar normalização Unicode.
    O chamador deve escolher a codificação real do arquivo. ``newline=''``
    mantém literalmente as quebras que a abertura normal de arquivos costuma
    converter. Erros de leitura ou decodificação são explícitos.
    """
    try:
        with Path(caminho).open("r", encoding=encoding, errors="strict", newline="") as arquivo:
            return arquivo.read()
    except (OSError, UnicodeError, LookupError, TypeError, ValueError) as erro:
        raise ErroPreparacao("Não foi possível ler o texto original com a codificação informada.") from erro
