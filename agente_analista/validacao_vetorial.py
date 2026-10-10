"""Validação de consumo do arquivo exportado, sem executar o produtor.

Confere textos, associações, coordenadas e aritmética vetorial declarados no
JSON. Não verifica decisões morfológicas/sintáticas, não reaplica regras e não
autentica que os vetores foram gerados pelo modelo informado pelo produtor.
"""

from copy import deepcopy
from datetime import datetime
import hashlib
import math
import re
import struct

from .contratos_importacao import (
    ErroVetorizacao, exigir, canonico, hash_json, sha256, identificador,
)


COORDENADAS = {"unidade": "pontos_de_codigo_unicode", "base": 0, "fim_exclusivo": True}


def _intervalo(posicao, tamanho, *, vazio=False):
    exigir(type(posicao) is dict and set(posicao) == {"inicio", "fim"}
           and all(type(v) is int for v in posicao.values()), "Intervalo Unicode inválido.")
    inicio, fim = posicao["inicio"], posicao["fim"]
    exigir(0 <= inicio <= fim <= tamanho and (vazio or inicio < fim), "Intervalo fora da fonte.")
    return inicio, fim


def mapear_intervalos(preparacao, intervalos):
    """Mapeie recortes do texto de trabalho utilizando somente o mapa recebido."""
    resultado = []
    tamanho = len(preparacao["trabalho"]["texto"])
    for inicio, fim in intervalos:
        _intervalo({"inicio": inicio, "fim": fim}, tamanho)
        partes = []
        for trecho in preparacao["mapa_trechos"]:
            ti, tf = trecho["trabalho"]["inicio"], trecho["trabalho"]["fim"]
            if max(inicio, ti) >= min(fim, tf):
                continue
            oi, of = trecho["original"]["inicio"], trecho["original"]["fim"]
            if trecho["tipo"] == "copia":
                partes.append((oi + max(inicio, ti) - ti, oi + min(fim, tf) - ti))
            else:
                partes.append((oi, of))
        exigir(bool(partes), "Recorte sem correspondência ao original.")
        resultado.append({"inicio": partes[0][0], "fim": partes[-1][1]})
    return resultado


def _mapa(preparacao, inicio, fim, campo="trabalho"):
    if campo == "trabalho":
        return {"trabalho": {"inicio": inicio, "fim": fim},
                "original": mapear_intervalos(preparacao, [(inicio, fim)])[0]}
    _intervalo({"inicio": inicio, "fim": fim}, len(preparacao["original"]["texto"]))
    partes = []
    for trecho in preparacao["mapa_trechos"]:
        oi, of = trecho["original"]["inicio"], trecho["original"]["fim"]
        if max(inicio, oi) >= min(fim, of):
            continue
        ti, tf = trecho["trabalho"]["inicio"], trecho["trabalho"]["fim"]
        if trecho["tipo"] == "copia":
            partes.append((ti + max(inicio, oi) - oi, ti + min(fim, of) - oi))
        else:
            partes.append((ti, tf))
    exigir(bool(partes), "Recorte original sem correspondência ao trabalho.")
    return {"original": {"inicio": inicio, "fim": fim},
            "trabalho": {"inicio": partes[0][0], "fim": partes[-1][1]}}


def _preparacao(contexto):
    return contexto["regras"]["analise"]["anotacao"]["segmentacao"]["preparacao"]


def _validar_preparacao(preparacao):
    exigir(preparacao["coordenadas"] == COORDENADAS, "Convenção de coordenadas não suportada.")
    for nome in ("original", "trabalho"):
        fonte = preparacao[nome]
        exigir(type(fonte) is dict and type(fonte.get("texto")) is str
               and type(fonte.get("comprimento")) is int
               and fonte["comprimento"] == len(fonte["texto"])
               and fonte.get("sha256") == sha256(fonte["texto"]), "Texto, tamanho ou hash da fonte divergente.")
    original, trabalho = (preparacao[k]["texto"] for k in ("original", "trabalho"))
    normalizar = preparacao["politica"]["opcoes"]["normalizar_crlf"]
    exigir(type(normalizar) is bool and trabalho == (original.replace("\r\n", "\n") if normalizar else original),
           "O texto de trabalho não preserva a transformação técnica declarada.")
    mapa = preparacao["mapa_trechos"]
    exigir(type(mapa) is list and bool(mapa), "Mapa original/trabalho ausente.")
    oi_anterior = ti_anterior = 0
    for trecho in mapa:
        oi, of = _intervalo(trecho["original"], len(original))
        ti, tf = _intervalo(trecho["trabalho"], len(trabalho))
        exigir(oi == oi_anterior and ti == ti_anterior, "Mapa tem lacunas ou sobreposições.")
        if trecho["tipo"] == "copia":
            exigir(original[oi:of] == trabalho[ti:tf], "Mapa copiado difere da fonte.")
        else:
            exigir(trecho["tipo"] == "transformacao_tecnica" and normalizar
                   and original[oi:of] == "\r\n" and trabalho[ti:tf] == "\n",
                   "Transformação do mapa não suportada.")
        oi_anterior, ti_anterior = of, tf
    exigir(oi_anterior == len(original) and ti_anterior == len(trabalho), "Mapa incompleto.")


def _trecho(bloco, preparacao, *, hashes=False):
    trabalho, original = (preparacao[k]["texto"] for k in ("trabalho", "original"))
    ti, tf = _intervalo(bloco["trabalho"], len(trabalho))
    oi, of = _intervalo(bloco["original"], len(original))
    exigir(bloco["original"] == mapear_intervalos(preparacao, [(ti, tf)])[0], "Mapa do trecho divergente.")
    exigir(bloco["texto"] == trabalho[ti:tf], "Trecho não corresponde ao texto de trabalho.")
    if hashes:
        exigir(bloco.get("texto_original") == original[oi:of]
               and bloco.get("sha256_texto") == sha256(bloco["texto"])
               and bloco.get("sha256_texto_original") == sha256(original[oi:of]),
               "Texto original ou hashes do trecho divergentes.")


def validar_contexto_exportado(contexto):
    """Confira a fonte textual e as unidades recebidas, sem refazer a análise."""
    exigir(type(contexto) is dict and contexto.get("etapa") == "08_unidades_contexto"
           and contexto.get("coordenadas") == COORDENADAS, "Fonte contextual inválida.")
    identificador(contexto.get("execucao_id"), "contexto.execucao_id")
    preparacao = _preparacao(contexto)
    _validar_preparacao(preparacao)
    exigir(contexto["documento_id"] == preparacao["documento_id"], "Documento da fonte divergente.")
    segmentacao = contexto["regras"]["analise"]["anotacao"]["segmentacao"]
    exigir(segmentacao["documento_id"] == contexto["documento_id"]
           and segmentacao["segmentacao_id"] == contexto["segmentacao_id"]
           and preparacao["preparacao_id"] == contexto["preparacao_id"], "Associações da fonte divergentes.")
    periodos, paragrafos, unidades = (segmentacao["periodos"], segmentacao["paragrafos"], contexto["unidades"])
    exigir(type(periodos) is list and bool(periodos) and type(paragrafos) is list and bool(paragrafos)
           and type(unidades) is list and len(unidades) == len(periodos), "Cobertura contextual incompleta.")
    por_periodo, por_paragrafo = {}, {}
    for itens, por_id, nome in ((paragrafos, por_paragrafo, "parágrafo"), (periodos, por_periodo, "período")):
        fim_anterior = 0
        for ordem, item in enumerate(itens):
            identificador(item["id"], nome + ".id")
            exigir(item["id"] not in por_id and type(item["ordem"]) is int and item["ordem"] == ordem,
                   "Ordem ou identificador textual divergente.")
            _trecho(item, preparacao)
            exigir(item["trabalho"]["inicio"] >= fim_anterior, "Unidades textuais se sobrepõem.")
            fim_anterior = item["trabalho"]["fim"]
            por_id[item["id"]] = item
    ids_unidades = set()
    for ordem, (unidade, periodo) in enumerate(zip(unidades, periodos)):
        identificador(unidade["id"], "unidade.id")
        exigir(unidade["id"] not in ids_unidades and unidade["id"] == f"{contexto['execucao_id']}:unidade:{ordem}"
               and type(unidade["ordem"]) is int and unidade["ordem"] == ordem
               and unidade["periodo_foco_id"] == periodo["id"], "ID, ordem ou associação do foco divergente.")
        ids_unidades.add(unidade["id"])
        foco, janela = unidade["foco"], unidade["janela"]
        _trecho(foco, preparacao, hashes=True)
        _trecho(janela, preparacao, hashes=True)
        exigir(foco["texto"] == periodo["texto"] and foco["trabalho"] == periodo["trabalho"]
               and foco["original"] == periodo["original"], "Foco não corresponde ao período declarado.")
        selecionados = janela["periodo_ids"]
        exigir(type(selecionados) is list and bool(selecionados) and len(set(selecionados)) == len(selecionados)
               and all(i in por_periodo for i in selecionados) and periodo["id"] in selecionados,
               "Janela não corresponde aos períodos declarados.")
        ordens = [por_periodo[i]["ordem"] for i in selecionados]
        exigir(ordens == list(range(ordens[0], ordens[-1] + 1))
               and janela["trabalho"] == {"inicio": por_periodo[selecionados[0]]["trabalho"]["inicio"],
                                           "fim": por_periodo[selecionados[-1]]["trabalho"]["fim"]},
               "Janela não cobre os períodos consecutivos declarados.")
        paragrafo = por_paragrafo[periodo["paragrafo_id"]]
        exigir(unidade["paragrafo_foco_id"] == paragrafo["id"]
               and paragrafo["trabalho"]["inicio"] <= foco["trabalho"]["inicio"]
               < foco["trabalho"]["fim"] <= paragrafo["trabalho"]["fim"], "Foco fora do parágrafo declarado.")
        selecao = unidade["selecao_logica"]
        exigir(unidade["janela_logica_id"] == hash_json(selecao)
               and selecao["documento"] == {"documento_id": contexto["documento_id"],
                                           "sha256_original": preparacao["original"]["sha256"]}
               and selecao["foco"]["original"] == foco["original"]
               and [p["original"] for p in selecao["periodos"]] == [por_periodo[i]["original"] for i in selecionados],
               "Identidade lógica ou associações da janela divergentes.")
    exigir(contexto.get("validacao", {}).get("pronto_para_etapa_09") is True,
           "A origem não declara prontidão para vetorização.")
    return {"pronto_para_etapa_09": True}


def _entrada_atual(entrada, limite):
    exigir(type(entrada) is dict and type(entrada.get("texto")) is str, "Entrada vetorial ausente.")
    campos = {"texto", "sha256", "input_ids", "attention_mask", "special_tokens_mask", "offsets", "sha256_tokens"}
    exigir(campos <= set(entrada) and not set(entrada) - campos - {"token_type_ids"}, "Campos de tokenização inválidos.")
    ids = entrada["input_ids"]
    exigir(type(ids) is list and 0 < len(ids) <= limite and all(type(i) is int and i >= 0 for i in ids),
           "IDs ou limite de tokens inválidos.")
    for campo in ("attention_mask", "special_tokens_mask"):
        mascara = entrada[campo]
        exigir(type(mascara) is list and len(mascara) == len(ids)
               and all(type(i) is int and i in (0, 1) for i in mascara), "Máscaras de tokens inválidas.")
    exigir(all(entrada["attention_mask"]), "Entrada possui padding oculto.")
    if "token_type_ids" in entrada:
        exigir(type(entrada["token_type_ids"]) is list and len(entrada["token_type_ids"]) == len(ids)
               and all(type(i) is int and i >= 0 for i in entrada["token_type_ids"]), "Tipos de token inválidos.")
    exigir(type(entrada["offsets"]) is list and len(entrada["offsets"]) == len(ids), "Offsets de tokens incompletos.")
    anterior = 0
    for offset, especial in zip(entrada["offsets"], entrada["special_tokens_mask"]):
        exigir(type(offset) is list and len(offset) == 2 and all(type(i) is int for i in offset), "Offset inválido.")
        inicio, fim = offset
        exigir(0 <= inicio <= fim <= len(entrada["texto"]), "Offset fora da entrada.")
        if especial:
            exigir(offset == [0, 0], "Token especial possui offset inválido.")
        else:
            exigir(inicio >= anterior, "Offsets fora de ordem.")
            anterior = inicio
    tokens = {k: entrada[k] for k in ("input_ids", "attention_mask", "special_tokens_mask", "offsets", "token_type_ids") if k in entrada}
    exigir(entrada["sha256"] == sha256(entrada["texto"]) and entrada["sha256_tokens"] == hash_json(tokens),
           "Hashes do texto ou dos tokens divergentes.")


def _chave(entrada, cfg):
    return hash_json({"versao": "1.0.0", "configuracao_sha256": cfg,
                      "entrada_sha256": entrada["sha256"], "tokens_sha256": entrada["sha256_tokens"]})


def _espaco(perfil, processamento):
    perfil_campos = ("modelo", "tokenizador", "backend", "precisao_inferencia", "pooling", "normalizacao",
                     "formato_armazenamento", "fragmentacao", "agregacao")
    def sem_caminhos(item):
        if type(item) is dict:
            return {k: sem_caminhos(v) for k, v in item.items()
                    if k not in {"caminho", "path", "arquivo", "cache_modelos", "diretorio"}}
        return [sem_caminhos(v) for v in item] if type(item) is list else item
    campos = {k: processamento[k] for k in ("backend", "versao_backend", "modelo", "tokenizador", "dimensao",
              "pooling", "precisao_inferencia", "bibliotecas", "limite_tokens", "limite_modelo_tokens")}
    for campo in ("artefatos", "pooling_configuracao", "tokenizacao_configuracao", "normalizacao_configuracao"):
        if campo in processamento:
            campos[campo] = sem_caminhos(processamento[campo])
    if "modelo_onnx_sha256" in perfil:
        campos["modelo_onnx_sha256"] = perfil["modelo_onnx_sha256"]
    valor = {"versao": "1.0.0", "perfil": {k: perfil[k] for k in perfil_campos}, "adaptador": campos,
             "prefixo": perfil["prefixo"], "finalidade": perfil["finalidade"], "perfil_versao": perfil["versao"], "legado": perfil["legado"]}
    return "sha256:" + hash_json(valor)


def _vetor_atual(artefato, perfil, proc, cfg):
    campos = {"id", "chave_cache", "dimensao", "formato", "ordem_bytes", "valores", "sha256_bytes",
              "normalizacao", "configuracao_sha256"}
    exigir(type(artefato) is dict and campos <= set(artefato) and len(set(artefato) - campos) == 1
           and ("entrada" in artefato) != ("derivacao" in artefato), "Artefato inválido.")
    valores = artefato["valores"]
    exigir(type(artefato["dimensao"]) is int and artefato["dimensao"] == proc["dimensao"]
           and type(valores) is list and len(valores) == proc["dimensao"]
           and all(type(v) in (int, float) and math.isfinite(v) for v in valores), "Dimensão ou valores inválidos.")
    exigir(artefato["formato"] == perfil["formato_armazenamento"] == "float32"
           and artefato["ordem_bytes"] == "little-endian" and artefato["normalizacao"] == perfil["normalizacao"] == "l2"
           and artefato["configuracao_sha256"] == cfg, "Configuração vetorial divergente.")
    dados = struct.pack("<" + "f" * len(valores), *valores)
    exigir(list(struct.unpack("<" + "f" * len(valores), dados)) == valores
           and abs(math.hypot(*valores) - 1.0) <= 2e-6
           and artefato["sha256_bytes"] == hashlib.sha256(dados).hexdigest(), "Bytes, hash ou norma L2 inválidos.")
    exigir(artefato["id"] == "sha256:" + hash_json({k: v for k, v in artefato.items() if k != "id"}), "ID de artefato divergente.")
    if "entrada" in artefato:
        _entrada_atual(artefato["entrada"], proc["limite_tokens"])
        exigir(artefato["chave_cache"] == _chave(artefato["entrada"], cfg), "Chave de cache divergente.")
    else:
        exigir(artefato["chave_cache"] == hash_json({"configuracao_sha256": cfg, "derivacao": artefato["derivacao"]}),
               "Chave de agregação divergente.")


def _conferir_media(artefato, por_id, dimensao):
    derivacao = artefato["derivacao"]
    exigir(type(derivacao) is dict and set(derivacao) == {"metodo", "versao", "componentes_ids", "pesos"}
           and derivacao["versao"] == "1.0.0" and derivacao["metodo"] in ("media_simples", "media_ponderada_tokens"),
           "Agregação não suportada.")
    ids, pesos = derivacao["componentes_ids"], derivacao["pesos"]
    exigir(type(ids) is list and bool(ids) and type(pesos) is list and len(ids) == len(pesos)
           and all(type(i) is str and i in por_id and i != artefato["id"] and "entrada" in por_id[i] for i in ids)
           and all(type(p) in (int, float) and math.isfinite(p) and p > 0 for p in pesos), "Componentes ou pesos inválidos.")
    if derivacao["metodo"] == "media_simples":
        exigir(pesos == [1] * len(ids), "Média simples exige pesos unitários.")
    total = math.fsum(pesos)
    media = [math.fsum(por_id[i]["valores"][j] * (peso / total) for i, peso in zip(ids, pesos)) for j in range(dimensao)]
    norma = math.hypot(*media)
    exigir(math.isfinite(norma) and norma > 0, "Agregação tem norma inválida.")
    esperado = list(struct.unpack("<" + "f" * dimensao, struct.pack("<" + "f" * dimensao, *[v / norma for v in media])))
    exigir(artefato["valores"] == esperado, "Vetor agregado não corresponde aos componentes.")


def validar_atual(registro):
    exigir(registro.get("schema_version") == "1.0.0" and registro.get("etapa") == "09_vetorizacao"
           and "derivacao_execucao" not in registro, "Importe a execução integral da etapa 09.")
    contexto = registro["contexto"]
    validar_contexto_exportado(contexto)
    preparacao = _preparacao(contexto)
    identificador(registro["execucao_id"], "execucao_id")
    instante = datetime.fromisoformat(registro["registrado_em"].replace("Z", "+00:00"))
    exigir(instante.utcoffset() is not None and registro["execucao_id"] != contexto["execucao_id"], "Data ou ID de execução inválidos.")
    for campo in ("documento_id", "preparacao_id", "segmentacao_id", "anotacao_id", "analise_id", "regras_execucao_id"):
        exigir(type(registro[campo]) is type(contexto[campo]) and registro[campo] == contexto[campo], "Associação da execução divergente.")
    exigir(registro["contexto_execucao_id"] == contexto["execucao_id"] and registro["contexto_sha256"] == hash_json(contexto), "Hash ou ID da origem divergente.")
    perfil, proc = registro["perfil"], registro["processamento"]
    exigir(type(perfil) is dict and type(proc) is dict and perfil["prefixo"] == "query: "
           and perfil["pooling"] == "mean" and perfil["normalizacao"] == "l2"
           and perfil["formato_armazenamento"] == "float32" and not perfil.get("legado")
           and perfil["fragmentacao"] in ("sem_sobreposicao", "erro")
           and perfil["agregacao"] in ("media_simples", "media_ponderada_tokens")
           and perfil["texto_documento"] in ("original", "trabalho"), "Perfil de consulta não suportado.")
    for campo in ("modelo", "tokenizador"):
        descricao = perfil[campo]
        exigir(type(descricao) is dict and set(descricao) == {"identificacao", "revisao"}, "Descrição do modelo inválida.")
        identificador(descricao["identificacao"], campo)
        exigir(type(descricao["revisao"]) is str and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", descricao["revisao"]), "Revisão imutável ausente.")
        exigir(proc[campo] == descricao, "Modelo efetivo diverge do perfil.")
    for campo in ("backend", "pooling", "precisao_inferencia"):
        exigir(proc[campo] == perfil[campo], "Processamento diverge do perfil.")
    exigir(type(proc["dimensao"]) is int and 1 <= proc["dimensao"] <= 8192
           and type(proc["limite_tokens"]) is int and 0 < proc["limite_tokens"] <= proc["limite_modelo_tokens"]
           and proc["modulo"] == {"nome": "vetorizacao", "versao": "1.0.0"}, "Dimensão ou processamento inválidos.")
    bruto = {k: deepcopy(v) for k, v in proc.items() if k not in ("adaptador", "modulo", "limite_tokens", "limite_modelo_tokens")}
    bruto["limite_tokens"] = proc["limite_modelo_tokens"]
    exigir(canonico(bruto) == canonico(proc["adaptador"]), "Snapshot do adaptador divergente.")
    solicitado = perfil["limite_tokens"]
    exigir(solicitado is None or (type(solicitado) is int and 0 < solicitado <= proc["limite_modelo_tokens"]), "Orçamento de tokens inválido.")
    exigir(proc["limite_tokens"] == min(proc["limite_modelo_tokens"], solicitado or proc["limite_modelo_tokens"]), "Limite efetivo divergente.")
    cfg = hash_json({"perfil": perfil, "processamento": proc, "modulo_versao": "1.0.0"})
    exigir(registro["configuracao_sha256"] == cfg and registro["espaco_vetorial_id"] == _espaco(perfil, proc), "Identidade vetorial divergente.")
    por_id = {}
    for artefato in registro["artefatos"]:
        _vetor_atual(artefato, perfil, proc, cfg)
        exigir(artefato["id"] not in por_id, "Artefato duplicado.")
        por_id[artefato["id"]] = artefato
    for artefato in por_id.values():
        if "derivacao" in artefato:
            _conferir_media(artefato, por_id, proc["dimensao"])
    descricoes = []
    for unidade in contexto["unidades"]:
        for tipo, campo in (("periodo", "foco"), ("contextual", "janela")):
            bloco = unidade[campo]
            origem = {"campo": f"unidades.{campo}.texto", "unidade_id": unidade["id"],
                      "periodo_foco_id": unidade["periodo_foco_id"], "janela_logica_id": unidade["janela_logica_id"],
                      "trabalho": bloco["trabalho"], "original": bloco["original"]}
            descricoes.append((tipo, origem, "trabalho", bloco["texto"]))
    campo = perfil["texto_documento"]
    texto = preparacao[campo]["texto"]
    descricoes.append(("documento", {"campo": f"preparacao.{campo}.texto", **_mapa(preparacao, 0, len(texto), campo)}, campo, texto))
    reps = registro["representacoes"]
    exigir(len(reps) == len(descricoes), "Cobertura vetorial incompleta.")
    usados = set()
    for ordem, (rep, (tipo, origem, campo, texto)) in enumerate(zip(reps, descricoes)):
        exigir(rep["id"] == f"{registro['execucao_id']}:representacao:{ordem}" and type(rep["ordem"]) is int
               and rep["ordem"] == ordem and rep["tipo"] == tipo and rep["origem"] == origem
               and rep["texto_sha256"] == sha256(texto) and rep["estado"] == "concluida"
               and rep["cobertura_integral"] is True and rep["omissoes"] == [], "Representação ou associação divergente.")
        inicio, teto = origem[campo]["inicio"], origem[campo]["fim"]
        componentes, pesos = [], []
        exigir(type(rep["fragmentos"]) is list and bool(rep["fragmentos"]), "Representação sem fragmentos.")
        for fragmento in rep["fragmentos"]:
            exigir(type(fragmento) is dict and set(fragmento) == {"artefato_id", "trabalho", "original", "tokens_conteudo", "peso"}, "Fragmento inválido.")
            pi, pf = _intervalo(fragmento[campo], len(preparacao[campo]["texto"]))
            exigir(pi == inicio and pf <= teto and {k: fragmento[k] for k in ("trabalho", "original")} == _mapa(preparacao, pi, pf, campo), "Fragmentos têm lacuna ou mapa divergente.")
            artefato = por_id[fragmento["artefato_id"]]
            exigir("entrada" in artefato and artefato["entrada"]["texto"] == perfil["prefixo"] + preparacao[campo]["texto"][pi:pf], "Entrada difere do recorte.")
            entrada = artefato["entrada"]
            tokens = sum(1 for especial, (a, b) in zip(entrada["special_tokens_mask"], entrada["offsets"])
                         if not especial and b > len(perfil["prefixo"]) and b > a)
            peso = tokens if perfil["agregacao"] == "media_ponderada_tokens" else 1
            exigir(type(fragmento["tokens_conteudo"]) is int and fragmento["tokens_conteudo"] == tokens and tokens > 0
                   and type(fragmento["peso"]) is int and fragmento["peso"] == peso, "Tokens ou peso do fragmento divergentes.")
            componentes.append(artefato["id"])
            pesos.append(peso)
            usados.add(artefato["id"])
            inicio = pf
        exigir(inicio == teto, "Fragmentos não cobrem toda a representação.")
        alvo = rep["artefato_id"]
        if len(componentes) == 1:
            exigir(rep["metodo"] == "direto" and alvo == componentes[0], "Vetor direto divergente.")
        else:
            exigir(rep["metodo"] == "agregado" and por_id[alvo].get("derivacao") == {
                "metodo": perfil["agregacao"], "versao": "1.0.0", "componentes_ids": componentes, "pesos": pesos}, "Agregação difere dos fragmentos.")
        usados.add(alvo)
    exigir(usados == set(por_id), "Artefatos órfãos ou omitidos.")
    validacao = registro["validacao"]
    exigir(validacao.get("pronto") is True and validacao.get("estado") == "concluida"
           and validacao.get("cobertura") == {"representacoes_total": len(reps), "artefatos_total": len(por_id),
                                              "representacoes_sem_conteudo": 0, "cobertura_integral": True}, "Relatório de cobertura divergente.")
