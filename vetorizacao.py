"""Etapa 09: contratos multimodelo, vetores e recortes rastreáveis.

O núcleo usa somente a biblioteca padrão. Modelos são adaptadores explícitos;
ler e validar um registro não importa bibliotecas de inferência. Coordenadas
são pontos de código Unicode, base zero e fim exclusivo. Hashes demonstram
integridade; não constituem prova de que um vetor externo veio de um modelo.
"""

from copy import deepcopy
from collections import OrderedDict
from bisect import bisect_right
from datetime import datetime, timezone
import hashlib
import json
import math
import struct
from uuid import uuid4

from preparacao import mapear_intervalos
from unidades_contexto import ErroContexto, validar_unidades_contexto


SCHEMA_VERSION = "1.0.0"
ETAPA = "09_vetorizacao"
_FORMATOS = {"float16": ("e", 2), "float32": ("f", 4), "float64": ("d", 8)}
_IDS = ("documento_id", "preparacao_id", "segmentacao_id", "anotacao_id", "analise_id",
        "regras_execucao_id")
_VERIFICACOES = ["origem_08_recalculada_e_pronta", "associacoes_e_textos_exatos",
                 "cobertura_recortes_e_mapeamentos_unicode", "configuracao_e_espaco_identificados",
                 "entradas_tokens_e_hashes", "dimensoes_finitude_formato_bytes_e_norma",
                 "agregacao_componentes_pesos_e_formula", "historico_sem_sobrescrever_origem"]


class ErroVetorizacao(ValueError):
    """Entrada, vetor ou contrato inconsistentes."""


class ErroEntrada(ErroVetorizacao):
    """A origem contextual não é válida e pronta."""


class ErroConfiguracao(ErroVetorizacao):
    """Perfil ou capacidade incompatível."""


class ErroLimite(ErroVetorizacao):
    """A entrada excede um orçamento explícito, sem truncamento silencioso."""


def _exigir(condicao, mensagem):
    if not condicao:
        raise ErroVetorizacao(mensagem)


def _canonico(valor):
    try:
        return json.dumps(valor, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          allow_nan=False)
    except (ValueError, TypeError, RecursionError) as erro:
        raise ErroVetorizacao("O registro deve conter JSON finito, sem ciclos.") from erro


def _json(valor):
    def visitar(item, ancestrais):
        if item is None or type(item) in (str, int, bool):
            return
        if type(item) is float:
            _exigir(math.isfinite(item), "Números JSON precisam ser finitos.")
            return
        _exigir(type(item) in (dict, list), "Utilize somente tipos JSON, sem tuplas ou objetos.")
        _exigir(id(item) not in ancestrais, "JSON não pode conter ciclos.")
        ancestrais.add(id(item))
        if type(item) is dict:
            _exigir(all(type(k) is str for k in item), "Chaves JSON precisam ser strings.")
            filhos = item.values()
        else:
            filhos = item
        for filho in filhos:
            visitar(filho, ancestrais)
        ancestrais.remove(id(item))
    try:
        visitar(valor, set())
        _canonico(valor).encode("utf-8")
    except (UnicodeError, RecursionError) as erro:
        raise ErroVetorizacao("JSON deve ser Unicode UTF-8 válido, sem ciclos.") from erro


def _sha(texto):
    try:
        return hashlib.sha256(texto.encode("utf-8")).hexdigest()
    except UnicodeError as erro:
        raise ErroVetorizacao("Texto não codificável em UTF-8.") from erro


def _hash(valor):
    return _sha(_canonico(valor))


def _id(valor, nome):
    _exigir(type(valor) is str and bool(valor.strip()), f"{nome} precisa ser uma string não vazia.")
    return valor


def _data(valor, gerar=False):
    if gerar and valor is None:
        valor = datetime.now(timezone.utc).isoformat()
    if gerar and isinstance(valor, datetime):
        valor = valor.isoformat()
    _exigir(type(valor) is str, "registrado_em precisa ser uma data ISO 8601 com fuso.")
    try:
        instante = datetime.fromisoformat(valor.replace("Z", "+00:00"))
        _exigir(instante.tzinfo is not None and instante.utcoffset() is not None,
                "registrado_em precisa conter fuso.")
    except (ValueError, OverflowError) as erro:
        raise ErroVetorizacao("registrado_em inválido.") from erro
    return valor


def _numero_finito(valor):
    if type(valor) not in (int, float):
        return False
    try:
        return math.isfinite(valor)
    except (OverflowError, ValueError):
        return False


def codificar_vetor(valores, formato):
    """Codifique números finitos em IEEE 754 little-endian, sem coerção de bool."""
    _exigir(type(formato) is str and formato in _FORMATOS, "Formato vetorial não suportado.")
    _exigir(type(valores) is list and bool(valores), "Vetor precisa ser uma lista não vazia.")
    _exigir(all(_numero_finito(v) for v in valores),
            "Vetor precisa conter somente números finitos, sem booleanos.")
    try:
        return struct.pack("<" + _FORMATOS[formato][0] * len(valores), *valores)
    except (struct.error, OverflowError) as erro:
        raise ErroVetorizacao("Vetor excede a faixa do formato declarado.") from erro


def decodificar_vetor(conteudo, formato):
    """Leia bytes IEEE 754 little-endian; comprimento e finitude são obrigatórios."""
    _exigir(type(formato) is str and formato in _FORMATOS, "Formato vetorial não suportado.")
    _exigir(type(conteudo) is bytes and bool(conteudo), "BLOB vetorial vazio ou inválido.")
    codigo, tamanho = _FORMATOS[formato]
    _exigir(len(conteudo) % tamanho == 0, "Comprimento do BLOB incompatível com formato.")
    valores = list(struct.unpack("<" + codigo * (len(conteudo) // tamanho), conteudo))
    _exigir(all(math.isfinite(v) for v in valores), "BLOB contém NaN ou infinito.")
    return valores


def _normalizar(valores, politica):
    _exigir(type(valores) is list and bool(valores), "Modelo retornou vetor vazio.")
    _exigir(all(_numero_finito(v) for v in valores),
            "Modelo retornou valores inválidos.")
    norma = math.hypot(*valores)
    _exigir(math.isfinite(norma) and norma > 0, "Vetor zero ou norma não finita.")
    _exigir(politica in ("l2", "nenhuma"), "Normalização não suportada.")
    return [float(v / norma if politica == "l2" else v) for v in valores]


def _fonte(contexto):
    try:
        relatorio = validar_unidades_contexto(contexto)
    except (ErroContexto, KeyError, TypeError, RecursionError) as erro:
        raise ErroEntrada(f"Origem da etapa 08 inválida: {erro}") from erro
    if relatorio.get("pronto_para_etapa_09") is not True:
        raise ErroEntrada("A etapa 08 não está pronta para vetorização.")
    seg = contexto["regras"]["analise"]["anotacao"]["segmentacao"]
    return seg["preparacao"], seg["periodos"], seg["paragrafos"]


def _intervalo(inicio, fim):
    return {"inicio": inicio, "fim": fim}


def _mapa(preparacao, inicio, fim, campo="trabalho"):
    if campo == "trabalho":
        return {"trabalho": _intervalo(inicio, fim),
                "original": mapear_intervalos(preparacao, [(inicio, fim)])[0]}
    # Inversão: uma interseção com transformação inclui o trecho transformado todo.
    candidatos = []
    for trecho in preparacao["mapa_trechos"]:
        oi, of = trecho["original"]["inicio"], trecho["original"]["fim"]
        ti, tf = trecho["trabalho"]["inicio"], trecho["trabalho"]["fim"]
        if max(oi, inicio) < min(of, fim):
            if trecho["tipo"] == "copia":
                candidatos.append((ti + max(oi, inicio) - oi, ti + min(of, fim) - oi))
            else:
                candidatos.append((ti, tf))
    trabalho = (candidatos[0][0], candidatos[-1][1]) if candidatos else (0, 0)
    return {"trabalho": _intervalo(*trabalho), "original": _intervalo(inicio, fim)}


def _descricoes(contexto, perfil, preparacao):
    for unidade in contexto["unidades"]:
        for tipo, campo in (("periodo", "foco"), ("contextual", "janela")):
            bloco = unidade[campo]
            yield {"tipo": tipo, "texto": bloco["texto"], "campo_texto": "trabalho",
                   "origem": {"campo": f"unidades.{campo}.texto", "unidade_id": unidade["id"],
                              "periodo_foco_id": unidade["periodo_foco_id"],
                              "janela_logica_id": unidade["janela_logica_id"],
                              "trabalho": deepcopy(bloco["trabalho"]),
                              "original": deepcopy(bloco["original"])}}
    campo = perfil["texto_documento"]
    texto = preparacao[campo]["texto"]
    yield {"tipo": "documento", "texto": texto, "campo_texto": campo,
           "origem": {"campo": f"preparacao.{campo}.texto", **_mapa(preparacao, 0, len(texto), campo)}}


def _processamento(descricao, perfil):
    _json(descricao)
    _exigir(type(descricao) is dict, "Descrição do adaptador precisa ser um objeto.")
    for campo in ("backend", "versao_backend", "dispositivo", "precisao_inferencia", "pooling"):
        _id(descricao.get(campo), campo)
    for campo in ("modelo", "tokenizador"):
        _exigir(type(descricao.get(campo)) is dict, f"Descrição {campo} ausente.")
        for nome in ("identificacao", "revisao"):
            _id(descricao[campo].get(nome), f"{campo}.{nome}")
        _exigir(descricao[campo] == perfil[campo], f"{campo} efetivo diverge do perfil.")
    _exigir(descricao["backend"] == perfil["backend"], "Backend efetivo diverge do perfil.")
    _exigir(descricao["pooling"] == perfil["pooling"], "Pooling efetivo diverge do perfil.")
    _exigir(descricao["precisao_inferencia"] == perfil["precisao_inferencia"],
            "Precisão de inferência diverge do perfil.")
    _exigir(type(descricao.get("bibliotecas")) is dict, "Versões das bibliotecas ausentes.")
    for campo in ("dimensao", "limite_tokens"):
        _exigir(type(descricao.get(campo)) is int and descricao[campo] > 0,
                f"{campo} efetivo precisa ser um inteiro positivo.")
    limite = perfil["limite_tokens"]
    if limite is not None:
        _exigir(type(limite) is int and limite > 0, "Orçamento de tokens inválido.")
        _exigir(limite <= descricao["limite_tokens"], "Orçamento solicitado excede a capacidade efetiva do modelo.")
    resultado = deepcopy(descricao)
    resultado["limite_modelo_tokens"] = descricao["limite_tokens"]
    resultado["limite_tokens"] = min(descricao["limite_tokens"], limite or descricao["limite_tokens"])
    resultado["adaptador"] = deepcopy(descricao)
    resultado["modulo"] = {"nome": "vetorizacao", "versao": SCHEMA_VERSION}
    return resultado


def _espaco(perfil, processamento, *, sem_prefixo=False):
    # Dispositivo consta na proveniência; não é uma identidade semântica suficiente.
    perfil_campos = ("modelo", "tokenizador", "backend", "precisao_inferencia", "pooling",
                     "normalizacao", "formato_armazenamento", "fragmentacao", "agregacao")
    def sem_caminhos(item):
        if type(item) is dict:
            return {k: sem_caminhos(v) for k, v in item.items()
                    if k not in {"caminho", "path", "arquivo", "cache_modelos", "diretorio"}}
        if type(item) is list:
            return [sem_caminhos(v) for v in item]
        return item
    campos_efetivos = {k: processamento[k] for k in ("backend", "versao_backend", "modelo",
                      "tokenizador", "dimensao", "pooling", "precisao_inferencia", "bibliotecas",
                      "limite_tokens", "limite_modelo_tokens")}
    for campo in ("artefatos", "pooling_configuracao", "tokenizacao_configuracao", "normalizacao_configuracao"):
        if campo in processamento:
            campos_efetivos[campo] = sem_caminhos(processamento[campo])
    for campo in ("modelo_onnx_sha256",):
        if campo in perfil:
            campos_efetivos[campo] = perfil[campo]
    valor = {"versao": "1.0.0", "perfil": {k: perfil[k] for k in perfil_campos},
             "adaptador": campos_efetivos}
    if not sem_prefixo:
        valor["prefixo"] = perfil["prefixo"]
        valor["finalidade"] = perfil["finalidade"]
        valor["perfil_versao"] = perfil["versao"]
        valor["legado"] = perfil["legado"]
    return "sha256:" + _hash(valor)


def _entrada(texto, tokens):
    _exigir(type(tokens) is dict, "Tokenizador retornou estrutura inválida.")
    obrigatorios = {"input_ids", "attention_mask", "special_tokens_mask", "offsets"}
    _exigir(obrigatorios <= tokens.keys() and not tokens.keys() - obrigatorios - {"token_type_ids"},
            "Contrato de tokenização inválido.")
    entrada = {"texto": texto, "sha256": _sha(texto), **deepcopy(tokens)}
    entrada["sha256_tokens"] = _hash(tokens)
    _validar_entrada(entrada)
    return entrada


def _validar_entrada(entrada):
    _exigir(type(entrada) is dict and type(entrada.get("texto")) is str,
            "Entrada efetiva ausente.")
    campos = {"texto", "sha256", "input_ids", "attention_mask", "special_tokens_mask", "offsets", "sha256_tokens"}
    _exigir(campos <= entrada.keys() and not entrada.keys() - campos - {"token_type_ids"},
            "Campos da entrada efetiva inválidos.")
    ids = entrada["input_ids"]
    _exigir(type(ids) is list and bool(ids) and all(type(v) is int and v >= 0 for v in ids),
            "input_ids precisam ser inteiros não negativos, sem booleanos.")
    for nome in ("attention_mask", "special_tokens_mask"):
        mascara = entrada[nome]
        _exigir(type(mascara) is list and len(mascara) == len(ids)
                and all(type(v) is int and v in (0, 1) for v in mascara), "Máscaras inválidas.")
    _exigir(all(entrada["attention_mask"]), "Entradas individuais não podem conter padding oculto.")
    if "token_type_ids" in entrada:
        _exigir(type(entrada["token_type_ids"]) is list and len(entrada["token_type_ids"]) == len(ids)
                and all(type(v) is int and v >= 0 for v in entrada["token_type_ids"]),
                "token_type_ids inválidos.")
    offsets = entrada["offsets"]
    _exigir(type(offsets) is list and len(offsets) == len(ids), "Offsets não cobrem os tokens.")
    anterior = 0
    for indice, offset in enumerate(offsets):
        _exigir(type(offset) is list and len(offset) == 2 and all(type(v) is int for v in offset),
                "Offsets precisam usar pontos de código Unicode inteiros.")
        inicio, fim = offset
        _exigir(0 <= inicio <= fim <= len(entrada["texto"]), "Offset fora da entrada.")
        if entrada["special_tokens_mask"][indice]:
            _exigir(offset == [0, 0], "Tokens especiais precisam ter offset [0,0].")
        else:
            _exigir(inicio >= anterior, "Offsets precisam ser ordenados.")
            anterior = inicio
    tokens = {k: entrada[k] for k in ("input_ids", "attention_mask", "special_tokens_mask", "offsets", "token_type_ids")
              if k in entrada}
    _exigir(entrada["sha256"] == _sha(entrada["texto"]) and entrada["sha256_tokens"] == _hash(tokens),
            "Hash da entrada ou tokenização divergente.")


def _chave(entrada, configuracao_sha256):
    return _hash({"versao": "1.0.0", "configuracao_sha256": configuracao_sha256,
                  "entrada_sha256": entrada["sha256"], "tokens_sha256": entrada["sha256_tokens"]})


def _artefato(valores, perfil, processamento, configuracao_sha256, *, entrada=None, derivacao=None):
    _exigir(type(valores) is list, "Modelo precisa retornar um vetor em lista.")
    _exigir(len(valores) == processamento["dimensao"], "Dimensão retornada pelo modelo incompatível.")
    normalizados = _normalizar(valores, perfil["normalizacao"])
    formato = perfil["formato_armazenamento"]
    dados = codificar_vetor(normalizados, formato)
    arredondados = decodificar_vetor(dados, formato)
    objeto = {"dimensao": processamento["dimensao"], "formato": formato, "ordem_bytes": "little-endian",
              "valores": arredondados, "sha256_bytes": hashlib.sha256(dados).hexdigest(),
              "normalizacao": perfil["normalizacao"], "configuracao_sha256": configuracao_sha256}
    if entrada is not None:
        objeto["entrada"] = deepcopy(entrada)
        objeto["chave_cache"] = _chave(entrada, configuracao_sha256)
    else:
        objeto["derivacao"] = deepcopy(derivacao)
        objeto["chave_cache"] = _hash({"configuracao_sha256": configuracao_sha256, "derivacao": derivacao})
    objeto["id"] = "sha256:" + _hash(objeto)
    _validar_artefato(objeto, perfil, processamento, configuracao_sha256)
    return objeto


def _validar_artefato(artefato, perfil, processamento, cfg):
    _exigir(type(artefato) is dict, "Artefato precisa ser objeto JSON.")
    campos = {"id", "chave_cache", "dimensao", "formato", "ordem_bytes", "valores", "sha256_bytes",
              "normalizacao", "configuracao_sha256"}
    _exigir(campos <= artefato.keys() and len(set(artefato) - campos) == 1
            and ("entrada" in artefato) != ("derivacao" in artefato), "Campos de artefato inválidos.")
    _exigir(type(artefato["dimensao"]) is int and artefato["dimensao"] == processamento["dimensao"],
            "Dimensão inconsistente.")
    _exigir(artefato["formato"] == perfil["formato_armazenamento"] and artefato["ordem_bytes"] == "little-endian"
            and artefato["normalizacao"] == perfil["normalizacao"] and artefato["configuracao_sha256"] == cfg,
            "Formato ou configuração do artefato divergente.")
    valores = artefato["valores"]
    _exigir(type(valores) is list and len(valores) == processamento["dimensao"], "Valores não cobrem a dimensão.")
    dados = codificar_vetor(valores, artefato["formato"])
    _exigir(decodificar_vetor(dados, artefato["formato"]) == valores, "Valores precisam representar o formato armazenado.")
    norma = math.hypot(*valores)
    _exigir(norma > 0 and math.isfinite(norma), "Vetor zero ou inválido.")
    tolerancia = {"float16": 0.002, "float32": 0.000002, "float64": 1e-12}[artefato["formato"]]
    if perfil["normalizacao"] == "l2":
        _exigir(abs(norma - 1.0) <= tolerancia, "Norma L2 incompatível com formato.")
    _exigir(artefato["sha256_bytes"] == hashlib.sha256(dados).hexdigest(), "Hash dos bytes vetoriais divergente.")
    sem_id = {k: v for k, v in artefato.items() if k != "id"}
    _exigir(artefato["id"] == "sha256:" + _hash(sem_id), "ID de artefato não corresponde ao conteúdo.")
    if "entrada" in artefato:
        _validar_entrada(artefato["entrada"])
        _exigir(len(artefato["entrada"]["input_ids"]) <= processamento["limite_tokens"], "Artefato excede orçamento de tokens.")
        _exigir(artefato["chave_cache"] == _chave(artefato["entrada"], cfg), "Chave de cache divergente.")
    else:
        _exigir(artefato["chave_cache"] == _hash({"configuracao_sha256": cfg, "derivacao": artefato["derivacao"]}),
                "Chave de agregação divergente.")


def _tokens_conteudo(entrada, inicio_conteudo):
    return sum(1 for especial, (inicio, fim) in zip(entrada["special_tokens_mask"], entrada["offsets"])
               if not especial and fim > inicio_conteudo and fim > inicio)


def _combinar(artefatos, pesos):
    _exigir(bool(artefatos) and len(artefatos) == len(pesos), "Componentes e pesos incompatíveis.")
    _exigir(all(_numero_finito(p) and p > 0 for p in pesos),
            "Pesos precisam ser positivos e finitos, sem booleanos.")
    soma = math.fsum(pesos)
    _exigir(math.isfinite(soma) and soma > 0, "Soma dos pesos inválida.")
    dimensao = artefatos[0]["dimensao"]
    _exigir(all(a["dimensao"] == dimensao for a in artefatos), "Componentes com dimensões diferentes.")
    return [math.fsum(a["valores"][i] * (p / soma) for a, p in zip(artefatos, pesos))
            for i in range(dimensao)]


def _limite_fragmento(texto, inicio, teto, entrada_para, limite):
    """Encontre um recorte literal que cabe; nenhuma reconstrução pelo tokenizer."""
    # Crescimento exponencial evita tokenizar todo o sufixo em cada fragmento.
    restante = teto - inicio
    tamanho, melhor, primeiro_excesso = 1, None, None
    while tamanho <= restante:
        fim = inicio + tamanho
        if len(entrada_para(texto[inicio:fim])["input_ids"]) > limite:
            primeiro_excesso = fim
            break
        melhor = fim
        if tamanho == restante:
            return fim
        tamanho = min(restante, tamanho * 2)
    baixo = (melhor + 1) if melhor is not None else inicio + 1
    alto = (primeiro_excesso - 1) if primeiro_excesso is not None else teto
    while baixo <= alto:
        meio = (baixo + alto) // 2
        if len(entrada_para(texto[inicio:meio])["input_ids"]) <= limite:
            melhor = meio
            baixo = meio + 1
        else:
            alto = meio - 1
    if melhor is None:
        # Mesmo sem monotonicidade estrita, um caractere é a unidade técnica mínima.
        minimo = inicio + 1
        if len(entrada_para(texto[inicio:minimo])["input_ids"]) <= limite:
            melhor = minimo
        else:
            raise ErroLimite("Nem um ponto de código cabe com prefixo e tokens especiais.")
    return melhor


def _compor_paragrafos(descricao, preparacao, paragrafos):
    """Composição histórica opt-in; cada cópia/inserção tem proveniência própria."""
    campo = descricao["campo_texto"]
    inicio, fim = (descricao["origem"][campo][k] for k in ("inicio", "fim"))
    pecas, textos, posicao = [], [], 0
    for paragrafo in paragrafos:
        pi = max(inicio, paragrafo[campo]["inicio"])
        pf = min(fim, paragrafo[campo]["fim"])
        if pi >= pf:
            continue
        if textos:
            pecas.append({"tipo": "insercao", "inicio": posicao, "fim": posicao + 1, "texto": " "})
            textos.append(" ")
            posicao += 1
        recorte = preparacao[campo]["texto"][pi:pf]
        pecas.append({"tipo": "copia", "inicio": posicao, "fim": posicao + len(recorte),
                      "texto": recorte, "fonte_inicio": pi})
        textos.append(recorte)
        posicao += len(recorte)
    return "".join(textos), pecas


def _recortes_compostos(pecas, tamanho, prefixo_tamanho, preparacao, campo):
    recortes = []
    for peca in pecas:
        inicio, fim = peca["inicio"], min(peca["fim"], tamanho)
        if inicio >= fim:
            continue
        recorte = {"tipo": peca["tipo"], "entrada": _intervalo(prefixo_tamanho + inicio, prefixo_tamanho + fim),
                   "texto": peca["texto"][:fim - inicio]}
        if peca["tipo"] == "copia":
            recorte.update(_mapa(preparacao, peca["fonte_inicio"], peca["fonte_inicio"] + fim - inicio, campo))
        recortes.append(recorte)
    return recortes


def _omissoes_compostas(descricao, recortes, preparacao):
    campo = descricao["campo_texto"]
    inicio = descricao["origem"][campo]["inicio"]
    teto = descricao["origem"][campo]["fim"]
    omissoes = []
    for recorte in recortes:
        if recorte["tipo"] != "copia":
            continue
        pi, pf = recorte[campo]["inicio"], recorte[campo]["fim"]
        if inicio < pi:
            omissoes.append(_mapa(preparacao, inicio, pi, campo))
        inicio = pf
    if inicio < teto:
        omissoes.append(_mapa(preparacao, inicio, teto, campo))
    return omissoes


def _planejar(descricao, perfil, processamento, adaptador, preparacao, periodos, paragrafos, memo):
    texto = descricao["texto"]
    legado = perfil["legado"]
    cabecalho = (legado["cabecalho"] + "\n") if legado and legado["ativo"] else ""
    prefixo = perfil["prefixo"] + cabecalho
    def entrada_para(recorte):
        efetiva = prefixo + recorte
        if efetiva in memo:
            memo.move_to_end(efetiva)
            return memo[efetiva]
        entrada = _entrada(efetiva, adaptador.tokenizar(efetiva))
        memo[efetiva] = entrada
        # Candidatos de busca não crescem com todos os recortes do documento.
        while len(memo) > 128 or sum(len(k) for k in memo) > 512 * 1024:
            memo.popitem(last=False)
        return entrada
    if not texto.strip():
        return []
    limite = processamento["limite_tokens"]
    teto = len(texto)
    if legado and legado["ativo"] and legado.get("juntar_paragrafos"):
        composto, pecas = _compor_paragrafos(descricao, preparacao, paragrafos)
        teto_composto = max(0, min(len(composto), legado["corte_caracteres"] - len(cabecalho)))
        _exigir(teto_composto > 0, "Composição histórica não contém conteúdo no orçamento.")
        if len(entrada_para(composto[:teto_composto])["input_ids"]) > limite:
            teto_composto = _limite_fragmento(composto, 0, teto_composto, entrada_para, limite)
        entrada = entrada_para(composto[:teto_composto])
        recortes = _recortes_compostos(pecas, teto_composto, len(prefixo), preparacao, descricao["campo_texto"])
        copias = [r for r in recortes if r["tipo"] == "copia"]
        _exigir(bool(copias), "Composição histórica não preservou nenhuma cópia de conteúdo.")
        campo = descricao["campo_texto"]
        conteudo = _tokens_conteudo(entrada, len(prefixo))
        _exigir(conteudo > 0, "Composição histórica não contém tokens de conteúdo.")
        return [{"entrada": entrada, "tokens_conteudo": conteudo, "recortes_origem": recortes,
                 **_mapa(preparacao, copias[0][campo]["inicio"], copias[-1][campo]["fim"], campo)}]
    if legado and legado["ativo"]:
        teto = max(0, min(teto, legado["corte_caracteres"] - len(cabecalho)))
        if teto == 0:
            raise ErroLimite("Cabeçalho histórico consumiu todo o corte de caracteres.")
        if len(entrada_para(texto[:teto])["input_ids"]) > limite:
            teto = _limite_fragmento(texto, 0, teto, entrada_para, limite)
        recortes = [(0, teto)]
    elif len(entrada_para(texto)["input_ids"]) <= limite:
        recortes = [(0, teto)]
    elif perfil["fragmentacao"] == "erro":
        raise ErroLimite("Entrada excede limite de tokens; política selecionada recusa fragmentação.")
    else:
        recortes, inicio = [], 0
        campo = descricao["campo_texto"]
        base = descricao["origem"][campo]["inicio"]
        fronteiras = [p[campo]["fim"] - base for p in periodos if 0 < p[campo]["fim"] - base <= teto]
        while inicio < teto:
            fim = _limite_fragmento(texto, inicio, teto, entrada_para, limite)
            if fim < teto:
                indice_fronteira = bisect_right(fronteiras, fim) - 1
                if indice_fronteira >= 0 and fronteiras[indice_fronteira] > inicio:
                    fim = fronteiras[indice_fronteira]
            recortes.append((inicio, fim))
            inicio = fim
    # Tokenizadores reais podem ignorar separadores. Eles continuam no recorte
    # literal, anexados a um vizinho que contenha conteúdo, sem vetor artificial.
    indice = 0
    while indice < len(recortes):
        inicio, fim = recortes[indice]
        if _tokens_conteudo(entrada_para(texto[inicio:fim]), len(prefixo)) > 0:
            indice += 1
            continue
        if indice > 0:
            anterior = recortes[indice - 1][0]
            if len(entrada_para(texto[anterior:fim])["input_ids"]) <= limite:
                recortes[indice - 1] = (anterior, fim)
                recortes.pop(indice)
                continue
        if indice + 1 < len(recortes):
            seguinte = recortes[indice + 1][1]
            if len(entrada_para(texto[inicio:seguinte])["input_ids"]) <= limite:
                recortes[indice + 1] = (inicio, seguinte)
                recortes.pop(indice)
                continue
        raise ErroLimite("Não foi possível associar separadores a um fragmento de conteúdo dentro do orçamento.")
    resultado = []
    campo = descricao["campo_texto"]
    base = descricao["origem"][campo]["inicio"]
    for inicio, fim in recortes:
        entrada = entrada_para(texto[inicio:fim])
        conteudo = _tokens_conteudo(entrada, len(prefixo))
        # Espaços entre períodos fazem parte de recortes, mas não ganham vetores isolados.
        _exigir(conteudo > 0, "Recorte não contém tokens de conteúdo; revise orçamento/política.")
        resultado.append({"entrada": entrada, "tokens_conteudo": conteudo,
                          **_mapa(preparacao, base + inicio, base + fim, campo)})
    return resultado


def _validacao(registro):
    reps = registro["representacoes"]
    estado = "sem_conteudo" if not registro["artefatos"] else "concluida"
    return {"estado": estado, "pronto": True, "verificacoes": _VERIFICACOES[:],
            "cobertura": {"representacoes_total": len(reps), "artefatos_total": len(registro["artefatos"]),
                           "representacoes_sem_conteudo": sum(r["estado"] == "sem_conteudo" for r in reps),
                           "cobertura_integral": all(r["cobertura_integral"] for r in reps)},
            "limites": ["Integridade estrutural não comprova a inferência de um vetor importado.",
                        "Agregações são sínteses; não equivalem semanticamente ao embedding direto.",
                        "Compatibilidade entre mecanismos distintos precisa de verificação explícita.",
                        "Compatibilidade histórica completa depende de pesos, tokenização e mecanismos verificados."]}


def vetorizar_contexto(contexto, *, perfil_id="e5_simetrico", opcoes=None, execucao_id=None,
                       registrado_em=None, adaptador=None, progresso=None, obter_cache=None, salvar_cache=None):
    """Gere duas representações por unidade e uma por documento, sem sobrescrever 08."""
    from perfis_vetorizacao import carregar_perfil
    perfil = carregar_perfil(perfil_id, opcoes=opcoes)
    _json(perfil)
    contexto = deepcopy(contexto)
    preparacao, periodos, paragrafos = _fonte(contexto)
    identificador = str(uuid4()) if execucao_id is None else _id(execucao_id, "execucao_id")
    _exigir(identificador != contexto["execucao_id"], "A execução 09 precisa ter ID próprio.")
    data = _data(registrado_em, gerar=True)
    descricoes = list(_descricoes(contexto, perfil, preparacao))
    if adaptador is None:
        from modelo_embeddings import criar_adaptador
        adaptador = criar_adaptador(perfil)
    processamento = _processamento(adaptador.descrever(), perfil)
    cfg = _hash({"perfil": perfil, "processamento": processamento, "modulo_versao": SCHEMA_VERSION})
    planos, memo, entradas = [], OrderedDict(), {}
    custo_entradas_utf8 = 0
    for descricao in descricoes:
        plano = _planejar(descricao, perfil, processamento, adaptador, preparacao, periodos, paragrafos, memo)
        planos.append(plano)
        for fragmento in plano:
            chave = _chave(fragmento["entrada"], cfg)
            if chave not in entradas:
                entradas[chave] = fragmento["entrada"]
                custo_entradas_utf8 += len(fragmento["entrada"]["texto"].encode("utf-8"))
        if len(entradas) > 30_000 or custo_entradas_utf8 > 16 * 1024 * 1024:
            raise ErroLimite("Planejamento excede 30.000 artefatos ou 16 MiB UTF-8 de entradas; nada foi inferido.")
    total, concluidos = len(entradas), 0
    def informar(etapa):
        if progresso is not None:
            progresso({"concluidos": concluidos, "total": total, "etapa": etapa})
    informar("planejada")
    por_chave, pendentes = {}, []
    for chave, entrada in entradas.items():
        recuperado = obter_cache(chave) if obter_cache is not None else None
        if recuperado is not None:
            _json(recuperado)
            _validar_artefato(recuperado, perfil, processamento, cfg)
            _exigir(recuperado.get("entrada") == entrada and recuperado["chave_cache"] == chave,
                    "Cache não corresponde à entrada efetiva solicitada.")
            por_chave[chave] = deepcopy(recuperado)
            concluidos += 1
        else:
            pendentes.append((chave, entrada))
    informar("cache")
    for inicio in range(0, len(pendentes), perfil["lote"]):
        lote = pendentes[inicio:inicio + perfil["lote"]]
        saida = adaptador.gerar([e["texto"] for _, e in lote])
        _exigir(type(saida) is list and len(saida) == len(lote), "Modelo retornou quantidade incorreta de vetores.")
        for (chave, entrada), valores in zip(lote, saida):
            artefato = _artefato(valores, perfil, processamento, cfg, entrada=entrada)
            if salvar_cache is not None:
                salvar_cache(chave, deepcopy(artefato))
            por_chave[chave] = artefato
            concluidos += 1
        informar("inferindo")
    artefatos, representacoes = {}, []
    for ordem, (descricao, plano) in enumerate(zip(descricoes, planos)):
        fragmentos = []
        componentes = []
        for fragmento in plano:
            artefato = por_chave[_chave(fragmento["entrada"], cfg)]
            artefatos.setdefault(artefato["id"], artefato)
            componentes.append(artefato)
            peso = fragmento["tokens_conteudo"] if perfil["agregacao"] == "media_ponderada_tokens" else 1
            fragmentos.append({"artefato_id": artefato["id"], "trabalho": fragmento["trabalho"],
                               "original": fragmento["original"], "tokens_conteudo": fragmento["tokens_conteudo"],
                               "peso": peso})
            if "recortes_origem" in fragmento:
                fragmentos[-1]["recortes_origem"] = deepcopy(fragmento["recortes_origem"])
        metodo, artefato_id = "direto", None
        if len(componentes) == 1:
            artefato_id = componentes[0]["id"]
        elif len(componentes) > 1:
            metodo = "agregado"
            derivacao = {"metodo": perfil["agregacao"], "versao": "1.0.0",
                         "componentes_ids": [a["id"] for a in componentes],
                         "pesos": [f["peso"] for f in fragmentos]}
            agregado = _artefato(_combinar(componentes, derivacao["pesos"]), perfil, processamento, cfg,
                                derivacao=derivacao)
            artefatos.setdefault(agregado["id"], agregado)
            artefato_id = agregado["id"]
        legado = bool(perfil["legado"] and perfil["legado"]["ativo"])
        omissoes = []
        if legado and plano:
            campo = descricao["campo_texto"]
            if "recortes_origem" in plano[0]:
                omissoes = _omissoes_compostas(descricao, plano[0]["recortes_origem"], preparacao)
            else:
                fim = plano[-1][campo]["fim"]
                if fim < descricao["origem"][campo]["fim"]:
                    omissoes.append(_mapa(preparacao, fim, descricao["origem"][campo]["fim"], campo))
        representacoes.append({"id": f"{identificador}:representacao:{ordem}", "ordem": ordem,
                               "tipo": descricao["tipo"], "origem": descricao["origem"],
                               "texto_sha256": _sha(descricao["texto"]), "metodo": metodo,
                               "artefato_id": artefato_id, "fragmentos": fragmentos,
                               "estado": "concluida" if plano else "sem_conteudo",
                               "cobertura_integral": not legado, "omissoes": omissoes})
    registro = {"schema_version": SCHEMA_VERSION, "etapa": ETAPA, "execucao_id": identificador,
                "registrado_em": data, **{k: contexto[k] for k in _IDS},
                "contexto_execucao_id": contexto["execucao_id"], "contexto": contexto,
                "contexto_sha256": _hash(contexto), "perfil": perfil, "processamento": processamento,
                "configuracao_sha256": cfg, "espaco_vetorial_id": _espaco(perfil, processamento),
                "representacoes": representacoes, "artefatos": list(artefatos.values())}
    registro["validacao"] = _validacao(registro)
    validar_vetorizacao(registro)
    informar("concluida")
    return registro


def _perfil_registrado(perfil):
    from perfis_vetorizacao import ErroPerfil, validar_perfil
    try:
        return validar_perfil(perfil)
    except ErroPerfil as erro:
        raise ErroConfiguracao(f"Snapshot do perfil inválido: {erro}") from erro


def _validar_combinacao(artefato, por_id, perfil, processamento, cfg):
    derivacao = artefato["derivacao"]
    _exigir(type(derivacao) is dict and set(derivacao) == {"metodo", "versao", "componentes_ids", "pesos"},
            "Derivação inválida.")
    _exigir(derivacao["versao"] == "1.0.0" and derivacao["metodo"] in
            ("media_simples", "media_ponderada_tokens", "media_ponderada"), "Fórmula de derivação desconhecida.")
    ids, pesos = derivacao["componentes_ids"], derivacao["pesos"]
    _exigir(type(ids) is list and bool(ids) and all(type(i) is str and i in por_id for i in ids),
            "Componentes inexistentes.")
    _exigir(artefato["id"] not in ids, "Derivação não pode referenciar a si própria.")
    _exigir(type(pesos) is list, "Pesos precisam ser uma lista.")
    if derivacao["metodo"] == "media_simples":
        _exigir(pesos == [1] * len(ids), "Média simples exige pesos unitários.")
    esperado = _artefato(_combinar([por_id[i] for i in ids], pesos), perfil, processamento, cfg,
                        derivacao=derivacao)
    _exigir(_canonico(artefato) == _canonico(esperado), "Vetor agregado não corresponde aos componentes.")


def _validar_composicao_legada(rep, descricao, perfil, preparacao, paragrafos, por_id):
    _exigir(rep["metodo"] == "direto" and len(rep["fragmentos"]) == 1, "Composição histórica exige vetor direto.")
    fragmento = rep["fragmentos"][0]
    _exigir(type(fragmento) is dict and set(fragmento) == {"artefato_id", "trabalho", "original",
              "tokens_conteudo", "peso", "recortes_origem"}, "Proveniência da composição histórica ausente.")
    alvo_id = fragmento["artefato_id"]
    artefato = por_id.get(alvo_id)
    _exigir(rep["artefato_id"] == alvo_id and artefato is not None and "entrada" in artefato,
            "Composição não referencia um vetor direto existente.")
    cabecalho = perfil["legado"]["cabecalho"] + "\n"
    prefixo = perfil["prefixo"] + cabecalho
    efetiva = artefato["entrada"]["texto"]
    _exigir(efetiva.startswith(prefixo), "Prefixo/cabeçalho da composição divergente.")
    conteudo = efetiva[len(prefixo):]
    completo, pecas = _compor_paragrafos(descricao, preparacao, paragrafos)
    _exigir(bool(conteudo) and conteudo == completo[:len(conteudo)]
            and len(conteudo) + len(cabecalho) <= perfil["legado"]["corte_caracteres"],
            "Entrada histórica não corresponde à concatenação/corte dos parágrafos.")
    recortes = _recortes_compostos(pecas, len(conteudo), len(prefixo), preparacao, descricao["campo_texto"])
    _exigir(_canonico(fragmento["recortes_origem"]) == _canonico(recortes), "Mapa das cópias/inserções da composição divergente.")
    copias = [r for r in recortes if r["tipo"] == "copia"]
    _exigir(bool(copias), "Composição não contém trechos da origem.")
    campo = descricao["campo_texto"]
    mapa = _mapa(preparacao, copias[0][campo]["inicio"], copias[-1][campo]["fim"], campo)
    _exigir(_canonico(fragmento["trabalho"]) == _canonico(mapa["trabalho"])
            and _canonico(fragmento["original"]) == _canonico(mapa["original"]), "Intervalos gerais da composição divergentes.")
    omissoes = _omissoes_compostas(descricao, recortes, preparacao)
    _exigir(_canonico(rep["omissoes"]) == _canonico(omissoes), "Omissões da composição divergentes.")
    tokens = _tokens_conteudo(artefato["entrada"], len(prefixo))
    peso = tokens if perfil["agregacao"] == "media_ponderada_tokens" else 1
    _exigir(tokens > 0 and type(fragmento["tokens_conteudo"]) is int and fragmento["tokens_conteudo"] == tokens
            and type(fragmento["peso"]) is int and fragmento["peso"] == peso, "Tokens/pesos da composição incoerentes.")
    return {alvo_id}


def validar_vetorizacao(registro):
    """Confira fonte, recortes, hashes e matemática sem carregar o modelo neural."""
    try:
        return _validar_vetorizacao(registro)
    except ErroVetorizacao:
        raise
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError) as erro:
        raise ErroVetorizacao(f"Registro de vetorização inválido: {erro}") from erro


def _validar_vetorizacao(registro, profundidade=0):
    _exigir(profundidade <= 16, "Histórico de derivações excede 16 níveis; consolide a seleção.")
    _json(registro)
    _exigir(type(registro) is dict and registro.get("schema_version") == SCHEMA_VERSION
            and registro.get("etapa") == ETAPA, "Formato de vetorização não suportado.")
    _id(registro.get("execucao_id"), "execucao_id")
    _data(registro.get("registrado_em"))
    contexto = registro.get("contexto")
    preparacao, _, paragrafos = _fonte(contexto)
    _exigir(registro["execucao_id"] != contexto["execucao_id"], "Execuções 08 e 09 precisam de IDs distintos.")
    for campo in _IDS:
        _exigir(type(registro.get(campo)) is type(contexto[campo]) and registro[campo] == contexto[campo],
                f"Associação {campo} diverge da origem.")
    _exigir(registro.get("contexto_execucao_id") == contexto["execucao_id"]
            and registro.get("contexto_sha256") == _hash(contexto), "Origem contextual/hash divergente.")
    perfil = _perfil_registrado(registro.get("perfil"))
    proc = registro.get("processamento")
    _exigir(type(proc) is dict and type(proc.get("limite_modelo_tokens")) is int, "Processamento efetivo ausente.")
    bruto = deepcopy(proc)
    _exigir(bruto.pop("modulo", None) == {"nome": "vetorizacao", "versao": SCHEMA_VERSION},
            "Versão do núcleo não suportada.")
    adaptador_declarado = bruto.pop("adaptador", None)
    bruto["limite_tokens"] = bruto.pop("limite_modelo_tokens")
    _exigir(_canonico(adaptador_declarado) == _canonico(bruto), "Snapshot do adaptador diverge da descrição efetiva.")
    _exigir(_canonico(_processamento(bruto, perfil)) == _canonico(proc), "Capacidades efetivas inconsistentes.")
    cfg = _hash({"perfil": perfil, "processamento": proc, "modulo_versao": SCHEMA_VERSION})
    _exigir(registro.get("configuracao_sha256") == cfg and registro.get("espaco_vetorial_id") == _espaco(perfil, proc),
            "Identidade de configuração ou espaço divergente.")
    artefatos = registro.get("artefatos")
    _exigir(type(artefatos) is list, "Artefatos precisam ser uma lista.")
    por_id = {}
    for artefato in artefatos:
        _validar_artefato(artefato, perfil, proc, cfg)
        _exigir(artefato["id"] not in por_id, "Artefatos duplicados.")
        por_id[artefato["id"]] = artefato
    for artefato in artefatos:
        if "derivacao" in artefato:
            _validar_combinacao(artefato, por_id, perfil, proc, cfg)
    representacoes = registro.get("representacoes")
    _exigir(type(representacoes) is list, "Representações precisam ser uma lista.")
    if "derivacao_execucao" in registro:
        return _validar_derivado(registro, perfil, proc, cfg, por_id, profundidade)
    descricoes = list(_descricoes(contexto, perfil, preparacao))
    _exigir(len(descricoes) == len(representacoes), "Cobertura das unidades/documento incompleta.")
    usados = set()
    for ordem, (representacao, descricao) in enumerate(zip(representacoes, descricoes)):
        _exigir(type(representacao) is dict and set(representacao) == {"id", "ordem", "tipo", "origem", "texto_sha256",
                    "metodo", "artefato_id", "fragmentos", "estado", "cobertura_integral", "omissoes"},
                "Campos de representação inválidos.")
        _exigir(type(representacao["ordem"]) is int and representacao["ordem"] == ordem
                and representacao["id"] == f"{registro['execucao_id']}:representacao:{ordem}", "Ordem/ID divergente.")
        _exigir(representacao["tipo"] == descricao["tipo"] and _canonico(representacao["origem"]) == _canonico(descricao["origem"])
                and representacao["texto_sha256"] == _sha(descricao["texto"]), "Texto ou associação diverge da fonte 08.")
        fragmentos = representacao["fragmentos"]
        _exigir(type(fragmentos) is list, "Fragmentos precisam ser uma lista.")
        legado = bool(perfil["legado"] and perfil["legado"]["ativo"])
        _exigir(type(representacao["cobertura_integral"]) is bool and representacao["cobertura_integral"] is (not legado),
                "Cobertura integral incoerente com política histórica.")
        if not descricao["texto"].strip():
            _exigir(not fragmentos and representacao["artefato_id"] is None and representacao["estado"] == "sem_conteudo"
                    and representacao["metodo"] == "direto" and representacao["omissoes"] == [], "Conteúdo vazio ganhou vetor artificial.")
            continue
        _exigir(bool(fragmentos) and representacao["estado"] == "concluida", "Conteúdo não foi processado.")
        if legado and perfil["legado"].get("juntar_paragrafos"):
            usados.update(_validar_composicao_legada(representacao, descricao, perfil, preparacao, paragrafos, por_id))
            continue
        campo = descricao["campo_texto"]
        inicio = descricao["origem"][campo]["inicio"]
        teto = descricao["origem"][campo]["fim"]
        cabecalho = perfil["legado"]["cabecalho"] + "\n" if legado else ""
        prefixo = perfil["prefixo"] + cabecalho
        componentes, pesos = [], []
        for fragmento in fragmentos:
            _exigir(type(fragmento) is dict and set(fragmento) == {"artefato_id", "trabalho", "original", "tokens_conteudo", "peso"},
                    "Fragmento inválido.")
            posicao = fragmento.get(campo)
            _exigir(type(posicao) is dict and set(posicao) == {"inicio", "fim"} and all(type(v) is int for v in posicao.values()),
                    "Intervalo de fragmento inválido.")
            _exigir(posicao["inicio"] == inicio and inicio < posicao["fim"] <= teto,
                    "Recortes não são contíguos/ordenados ou excedem texto.")
            mapa = _mapa(preparacao, inicio, posicao["fim"], campo)
            _exigir(fragmento["trabalho"] == mapa["trabalho"] and fragmento["original"] == mapa["original"],
                    "Mapeamento do fragmento divergente.")
            artefato = por_id.get(fragmento["artefato_id"])
            _exigir(artefato is not None and "entrada" in artefato, "Fragmento não referencia vetor direto.")
            recorte = preparacao[campo]["texto"][inicio:posicao["fim"]]
            _exigir(artefato["entrada"]["texto"] == prefixo + recorte, "Entrada vetorizada diverge do recorte/prefixo.")
            tokens = _tokens_conteudo(artefato["entrada"], len(prefixo))
            _exigir(type(fragmento["tokens_conteudo"]) is int and fragmento["tokens_conteudo"] == tokens and tokens > 0,
                    "Contagem dos tokens de conteúdo inválida.")
            peso = tokens if perfil["agregacao"] == "media_ponderada_tokens" else 1
            _exigir(type(fragmento["peso"]) is int and fragmento["peso"] == peso, "Peso do fragmento divergente.")
            usados.add(artefato["id"])
            componentes.append(artefato["id"])
            pesos.append(peso)
            inicio = posicao["fim"]
        omissoes = [_mapa(preparacao, inicio, teto, campo)] if legado and inicio < teto else []
        _exigir(_canonico(representacao["omissoes"]) == _canonico(omissoes), "Omissões históricas não correspondem aos recortes.")
        if not legado:
            _exigir(inicio == teto, "Fragmentos não cobrem integralmente o texto.")
        else:
            _exigir(len(fragmentos) == 1 and len(cabecalho) + inicio - descricao["origem"][campo]["inicio"]
                    <= perfil["legado"]["corte_caracteres"], "Corte histórico incorreto.")
        alvo = representacao["artefato_id"]
        if len(componentes) == 1:
            _exigir(representacao["metodo"] == "direto" and alvo == componentes[0], "Vetor direto incorreto.")
        else:
            _exigir(representacao["metodo"] == "agregado" and alvo in por_id, "Agregado ausente.")
            esperado = {"metodo": perfil["agregacao"], "versao": "1.0.0", "componentes_ids": componentes, "pesos": pesos}
            _exigir(por_id[alvo].get("derivacao") == esperado, "Agregação não corresponde aos fragmentos.")
        usados.add(alvo)
    _exigir(usados == set(por_id), "Artefatos órfãos ou referências incompletas.")
    esperado = _validacao(registro)
    _exigir(_canonico(registro.get("validacao")) == _canonico(esperado), "Relatório de validação inconsistente.")
    return deepcopy(esperado)


def consultar_representacao(registro, *, representacao_id):
    _id(representacao_id, "representacao_id")
    validar_vetorizacao(registro)
    for representacao in registro["representacoes"]:
        if representacao["id"] == representacao_id:
            ids = {representacao["artefato_id"]} | {f["artefato_id"] for f in representacao["fragmentos"]}
            return {"execucao_id": registro["execucao_id"], "contexto_execucao_id": registro["contexto_execucao_id"],
                    "configuracao_sha256": registro["configuracao_sha256"],
                    "espaco_vetorial_id": registro["espaco_vetorial_id"],
                    "representacao": deepcopy(representacao),
                    "artefatos": deepcopy([a for a in registro["artefatos"] if a["id"] in ids])}
    raise ErroVetorizacao("Representação inexistente nesta execução.")


def verificar_compatibilidade(registro_a, registro_b, *, operacao="simetrica"):
    """Declare comparações autorizadas; não calcule similaridade nem índices."""
    validar_vetorizacao(registro_a)
    validar_vetorizacao(registro_b)
    _exigir(operacao in ("simetrica", "recuperacao"), "Operação de comparação desconhecida.")
    pa, pb = registro_a["perfil"], registro_b["perfil"]
    mesma = registro_a["espaco_vetorial_id"] == registro_b["espaco_vetorial_id"]
    par_e5 = (operacao == "recuperacao" and pa["id"] == "e5_consulta" and pb["id"] == "e5_conteudo"
              and pa["prefixo"] == "query: " and pb["prefixo"] == "passage: "
              and _espaco(pa, registro_a["processamento"], sem_prefixo=True)
              == _espaco(pb, registro_b["processamento"], sem_prefixo=True)
              and pa["modelo"]["identificacao"].startswith("intfloat/multilingual-e5-"))
    compativel = mesma if operacao == "simetrica" else par_e5
    return {"operacao": operacao, "compativel": bool(compativel),
            "contrato": "identidade_espaco_1.0.0" if mesma and operacao == "simetrica" else
                        ("e5_consulta_conteudo_1.0.0" if par_e5 else None),
            "motivo": "Configuração e papéis autorizados." if compativel else
                      "Não existe contrato explícito para esse par de espaços/papéis.",
            "espaco_a": registro_a["espaco_vetorial_id"], "espaco_b": registro_b["espaco_vetorial_id"]}


def derivar_vetor(registro, representacao_ids, *, metodo="media_simples", pesos=None,
                 execucao_id=None, registrado_em=None):
    """Produza nova execução matemática preservando fonte e componentes imutáveis."""
    validar_vetorizacao(registro)
    _exigir(type(representacao_ids) is list and bool(representacao_ids)
            and all(type(v) is str for v in representacao_ids) and len(set(representacao_ids)) == len(representacao_ids),
            "Informe uma seleção não vazia e sem repetição de representações.")
    _exigir(metodo in ("media_simples", "media_ponderada"), "Método derivado não suportado.")
    if metodo == "media_simples":
        _exigir(pesos is None, "Média simples não recebe pesos personalizados.")
        pesos = [1] * len(representacao_ids)
    else:
        _exigir(type(pesos) is list and len(pesos) == len(representacao_ids), "Pesos precisam corresponder à seleção.")
    por_rep = {r["id"]: r for r in registro["representacoes"]}
    _exigir(all(i in por_rep and por_rep[i]["artefato_id"] is not None for i in representacao_ids),
            "Representação ausente ou sem conteúdo.")
    componentes_ids = [por_rep[i]["artefato_id"] for i in representacao_ids]
    por_id = {a["id"]: a for a in registro["artefatos"]}
    derivacao = {"metodo": metodo, "versao": "1.0.0", "componentes_ids": componentes_ids, "pesos": deepcopy(pesos)}
    artefato = _artefato(_combinar([por_id[i] for i in componentes_ids], pesos), registro["perfil"],
                        registro["processamento"], registro["configuracao_sha256"], derivacao=derivacao)
    novo_id = str(uuid4()) if execucao_id is None else _id(execucao_id, "execucao_id")
    _exigir(novo_id not in (registro["execucao_id"], registro["contexto_execucao_id"]), "Derivação precisa ter execução própria.")
    resultado = {k: deepcopy(v) for k, v in registro.items() if k not in ("representacoes", "artefatos", "validacao")}
    resultado["execucao_id"] = novo_id
    resultado["registrado_em"] = _data(registrado_em, gerar=True)
    resultado["derivacao_execucao"] = {"origem_execucao_id": registro["execucao_id"],
                                      "origem_sha256": _hash(registro), "metodo": metodo,
                                      "representacao_ids": representacao_ids[:], "pesos": deepcopy(pesos),
                                      "representacoes_origem": deepcopy([por_rep[i] for i in representacao_ids]),
                                      "manifesto_origem": {k: deepcopy(v) for k, v in registro.items() if k != "contexto"}}
    # Conserve o fechamento dos componentes, inclusive fragmentos de agregados.
    necessarios = set(componentes_ids)
    fila = list(necessarios)
    while fila:
        atual = por_id[fila.pop()]
        for filho in atual.get("derivacao", {}).get("componentes_ids", []):
            if filho not in necessarios:
                necessarios.add(filho)
                fila.append(filho)
    resultado["artefatos"] = [deepcopy(a) for a in registro["artefatos"] if a["id"] in necessarios]
    if artefato["id"] not in necessarios:
        resultado["artefatos"].append(artefato)
    resultado["representacoes"] = [{"id": f"{novo_id}:representacao:0", "ordem": 0, "tipo": "derivado",
                                   "origem": {"execucao_id": registro["execucao_id"], "representacao_ids": representacao_ids[:]},
                                   "texto_sha256": None, "metodo": "derivado", "artefato_id": artefato["id"],
                                   "fragmentos": [], "estado": "concluida", "cobertura_integral": False, "omissoes": []}]
    resultado["validacao"] = _validacao(resultado)
    validar_vetorizacao(resultado)
    return resultado


def _validar_derivado(registro, perfil, proc, cfg, por_id, profundidade=0):
    derivacao = registro["derivacao_execucao"]
    _exigir(type(derivacao) is dict and set(derivacao) == {"origem_execucao_id", "origem_sha256", "metodo",
              "representacao_ids", "pesos", "representacoes_origem", "manifesto_origem"}, "Proveniência derivada inválida.")
    _id(derivacao["origem_execucao_id"], "origem_execucao_id")
    _exigir(derivacao["origem_execucao_id"] != registro["execucao_id"], "Derivação sobrescreveu a execução de origem.")
    manifesto = derivacao["manifesto_origem"]
    _exigir(type(manifesto) is dict and "contexto" not in manifesto, "Fonte 08 deve ocorrer uma única vez.")
    origem = {**deepcopy(manifesto), "contexto": registro["contexto"]}
    _validar_vetorizacao(origem, profundidade + 1)
    _exigir(origem["execucao_id"] == derivacao["origem_execucao_id"] and derivacao["origem_sha256"] == _hash(origem),
            "Hash/identificação da execução de origem diverge do manifesto validado.")
    _exigir(origem["configuracao_sha256"] == cfg and origem["espaco_vetorial_id"] == registro["espaco_vetorial_id"],
            "Componentes derivados pertencem a outro perfil/espaço.")
    ids = derivacao["representacao_ids"]
    fontes = derivacao["representacoes_origem"]
    _exigir(type(ids) is list and bool(ids) and len(set(ids)) == len(ids) and type(fontes) is list
            and len(fontes) == len(ids) and [r["id"] for r in fontes] == ids, "Seleção derivada inconsistente.")
    originais = {r["id"]: r for r in origem["representacoes"]}
    _exigir(all(i in originais for i in ids) and
            _canonico(fontes) == _canonico([originais[i] for i in ids]), "Associações derivadas divergem da execução de origem.")
    reps = registro["representacoes"]
    _exigir(len(reps) == 1, "Uma derivação produz uma representação.")
    rep = reps[0]
    esperado = {"id": f"{registro['execucao_id']}:representacao:0", "ordem": 0, "tipo": "derivado",
                "origem": {"execucao_id": derivacao["origem_execucao_id"], "representacao_ids": ids},
                "texto_sha256": None, "metodo": "derivado", "artefato_id": rep.get("artefato_id"),
                "fragmentos": [], "estado": "concluida", "cobertura_integral": False, "omissoes": []}
    _exigir(_canonico(rep) == _canonico(esperado) and rep["artefato_id"] in por_id, "Representação derivada inválida.")
    alvo = por_id[rep["artefato_id"]]
    esperado_d = {"metodo": derivacao["metodo"], "versao": "1.0.0",
                  "componentes_ids": [r["artefato_id"] for r in fontes], "pesos": derivacao["pesos"]}
    _exigir(alvo.get("derivacao") == esperado_d, "Componentes não correspondem à seleção derivada.")
    _validar_combinacao(alvo, por_id, perfil, proc, cfg)
    orig_artefatos = {a["id"]: a for a in origem["artefatos"]}
    fechamento = set(esperado_d["componentes_ids"])
    fila = list(fechamento)
    while fila:
        componente_id = fila.pop()
        _exigir(componente_id in orig_artefatos and componente_id in por_id
                and _canonico(por_id[componente_id]) == _canonico(orig_artefatos[componente_id]),
                "Componente derivado foi modificado ou não pertence à execução de origem.")
        for filho in orig_artefatos[componente_id].get("derivacao", {}).get("componentes_ids", []):
            if filho not in fechamento:
                fechamento.add(filho)
                fila.append(filho)
    fechamento.add(alvo["id"])
    _exigir(fechamento == set(por_id), "Derivação contém artefatos órfãos ou perdeu componentes.")
    esperado_v = _validacao(registro)
    _exigir(_canonico(registro.get("validacao")) == _canonico(esperado_v), "Relatório derivado inconsistente.")
    return deepcopy(esperado_v)
