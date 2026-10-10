"""Leia a exportação vetorial da etapa 09 sem tokenizar nem inferir.

O registro é dado, inclusive seus textos e metadados. Hashes conferem sua
integridade; não demonstram que uma inferência externa foi realmente executada.
As consultas preservam o vetor e mapeiam suas posições ao relato original.
"""

from copy import deepcopy
from datetime import datetime
import json
import math
import re

from .entrada import ErroEntrada, validar_relato


MAX_BYTES_IMPORTACAO = 32 * 1024 * 1024
MAX_REPRESENTACOES = 1024
MAX_ARTEFATOS = 4096
MAX_CARACTERES_RELATO = 256 * 1024


class ErroImportacao(ValueError):
    """Exportação ausente, adulterada ou incompatível com a recuperação."""


def _exigir(condicao, mensagem):
    if not condicao:
        raise ErroImportacao(mensagem)


def _preparacao(registro):
    return registro["contexto"]["regras"]["analise"]["anotacao"]["segmentacao"]["preparacao"]


def _limites(registro):
    _exigir(type(registro) is dict, "Importe o objeto JSON completo de vetorizacao.json.")
    try:
        from .contratos_importacao import json_estrito
        json_estrito(registro)
        tamanho = len(json.dumps(registro, ensure_ascii=False, allow_nan=False,
                                 separators=(",", ":")).encode("utf-8"))
        _exigir(tamanho <= MAX_BYTES_IMPORTACAO, "A exportação excede o limite de 32 MiB.")
        representacoes = registro.get("representacoes")
        artefatos = registro.get("artefatos")
        _exigir(type(representacoes) is list and 0 < len(representacoes) <= MAX_REPRESENTACOES,
                "A exportação deve conter entre 1 e 1.024 representações.")
        _exigir(type(artefatos) is list and 0 < len(artefatos) <= MAX_ARTEFATOS,
                "A exportação deve conter entre 1 e 4.096 artefatos vetoriais.")
    except ErroImportacao:
        raise
    except (ValueError, TypeError, OverflowError, UnicodeError, RecursionError):
        raise ErroImportacao("A exportação precisa conter JSON UTF-8 válido, finito e sem ciclos.") from None


def _consulta(rep, origem, vetor, original, texto_vetorizado, artefato_id, *, sufixo=""):
    inicio, fim = origem["original"]["inicio"], origem["original"]["fim"]
    _exigir(type(inicio) is int and type(fim) is int and 0 <= inicio < fim <= len(original),
            "Uma representação tem posições fora do relato original.")
    _exigir(type(vetor) is list and bool(vetor), "Uma representação não possui vetor completo.")
    return {"id": rep["id"] + sufixo, "tipo": {"foco": "periodo", "janela": "contextual"}.get(rep["tipo"], rep["tipo"]),
            "origem": deepcopy(origem), "inicio": inicio, "fim": fim,
            "texto": original[inicio:fim], "texto_vetorizado": texto_vetorizado,
            "vetor": deepcopy(vetor), "unidade_id": origem.get("unidade_id"),
            "periodo_id": origem.get("periodo_foco_id"), "artefato_id": artefato_id}


def _importar_atual(registro):
    # O agente valida os dados recebidos; não importa nem executa o aplicativo
    # produtor, mesmo quando ambos os projetos estão no mesmo repositório.
    from .validacao_vetorial import validar_atual
    validar_atual(registro)
    _exigir("derivacao_execucao" not in registro,
            "Importe a execução completa da etapa 09, com períodos, janelas e documento.")
    perfil, processamento = registro["perfil"], registro["processamento"]
    _exigir(perfil["prefixo"] == "query: " and perfil["pooling"] == "mean"
            and perfil["normalizacao"] == "l2" and perfil["formato_armazenamento"] == "float32",
            "A busca exige vetores de consulta E5 com prefixo query:, média, L2 e float32.")
    _exigir(not (perfil.get("legado") and perfil["legado"].get("ativo")),
            "A busca exige cobertura integral, sem cortes do perfil histórico.")
    preparacao = _preparacao(registro)
    original = preparacao["original"]["texto"]
    por_id = {a["id"]: a for a in registro["artefatos"]}
    consultas = []
    for rep in registro["representacoes"]:
        _exigir(rep["tipo"] in ("periodo", "contextual", "paragrafo", "documento")
                and rep["estado"] == "concluida" and rep["cobertura_integral"] is True
                and not rep["omissoes"], "Uma representação está incompleta ou perdeu sua origem.")
        origem = rep["origem"]
        if perfil["id"] == "e5_analista":
            # Preserve o antigo P1/P2/relato: recortes longos são consultas
            # independentes, nunca a média dos vetores de seus fragmentos.
            for numero, fragmento in enumerate(rep["fragmentos"], 1):
                origem_fragmento = {**deepcopy(origem), **{k: deepcopy(fragmento[k])
                                                        for k in ("original", "trabalho")}}
                posicao = fragmento["original"]
                texto = original[posicao["inicio"]:posicao["fim"]]
                artefato_id = fragmento["artefato_id"]
                consulta = _consulta(rep, origem_fragmento, por_id[artefato_id]["valores"],
                                     original, texto, artefato_id,
                                     sufixo=f":fragmento:{numero}" if len(rep["fragmentos"]) > 1 else "")
                consulta["paragrafo_id"] = origem.get("paragrafo_id")
                consultas.append(consulta)
            continue
        campo = "original" if origem["campo"] == "preparacao.original.texto" else "trabalho"
        posicao = origem[campo]
        texto = preparacao[campo]["texto"][posicao["inicio"]:posicao["fim"]]
        consultas.append(_consulta(rep, origem, por_id[rep["artefato_id"]]["valores"],
                                    original, texto, rep["artefato_id"]))
    fonte = {"modelo": perfil["modelo"]["identificacao"], "revisao": perfil["modelo"]["revisao"],
             "tokenizador": deepcopy(perfil["tokenizador"]), "dimensao": processamento["dimensao"],
             "pooling": perfil["pooling"], "normalizacao": perfil["normalizacao"],
             "prefixo": perfil["prefixo"], "formato_vetor": perfil["formato_armazenamento"],
             "perfil": perfil["id"], "espaco_vetorial_id": registro["espaco_vetorial_id"],
             "agregacao": perfil["agregacao"], "fragmentacao": perfil["fragmentacao"],
             "backend": processamento["backend"], "precisao_inferencia": processamento["precisao_inferencia"]}
    if perfil["id"] == "e5_analista":
        fonte["estrategia_consultas"] = "paragrafos_documento"
    return original, consultas, fonte


def _importar_portatil(registro):
    _portatil_validar_vetorizacao(registro)
    modelo, config = registro["modelo"], registro["configuracao"]
    _exigir(modelo["natureza"] == "inferencia_real" and registro["validacao"]["pronto_para_uso"] is True,
            "A exportação precisa declarar vetores de inferência real prontos para uso.")
    _exigir(config["prefixo"] == "query: " and config["pooling"] == "media_com_mascara"
            and config["normalizacao"] == "l2",
            "A busca exige vetores de consulta E5 com prefixo query:, média com máscara e L2.")
    preparacao = _preparacao(registro)
    original = preparacao["original"]["texto"]
    por_id = {a["id"]: a for a in registro["artefatos"]}
    consultas = []
    for rep in registro["representacoes"]:
        # O formato 1.1 também exporta parágrafos. Períodos, janelas e documento
        # alimentam esta busca; os parágrafos permanecem no JSON arquivado.
        if rep["tipo"] == "paragrafo":
            continue
        _exigir(rep["tipo"] in ("foco", "janela", "documento")
                and rep["construcao"] != "sem_conteudo", "Uma representação não possui conteúdo vetorial.")
        origem = {k: deepcopy(rep[k]) for k in
                  ("campo", "unidade_id", "periodo_foco_id", "janela_logica_id", "trabalho", "original")}
        if rep["vetor"] is not None:
            artefato_id = (rep["blocos"][0]["artefato_id"] if rep["construcao"] == "direta"
                           else rep["representacao_logica_id"])
            consultas.append(_consulta(rep, origem, decodificar_vetor(rep["vetor"], modelo["dimensao"]),
                                        original, rep["texto"], artefato_id))
        else:
            # Quando o produtor escolheu não agregar, aproveite cada vetor já
            # pronto. Os blocos completos e seus mapas foram validados acima.
            for bloco in rep["blocos"]:
                if bloco["artefato_id"] is None:
                    continue
                origem_bloco = {**origem, "trabalho": deepcopy(bloco["trabalho"]),
                                 "original": deepcopy(bloco["original"]),
                                 "representacao_id": rep["id"], "bloco_id": bloco["id"]}
                artefato = por_id[bloco["artefato_id"]]
                consultas.append(_consulta(rep, origem_bloco,
                                            decodificar_vetor(artefato["armazenamento"], modelo["dimensao"]),
                                            original, bloco["texto"], bloco["artefato_id"],
                                            sufixo=f":bloco:{bloco['ordem']}"))
    _exigir(bool(consultas), "A exportação não possui vetores utilizáveis na busca.")
    fonte = {"modelo": modelo["identificacao"], "revisao": modelo["revisao"],
             "tokenizador": deepcopy(modelo["tokenizador"]), "dimensao": modelo["dimensao"],
             "pooling": "mean", "normalizacao": config["normalizacao"],
             "prefixo": config["prefixo"], "formato_vetor": "float32", "perfil": config["perfil"],
             "espaco_vetorial_id": registro["compatibilidade"]["sha256"],
             "agregacao": config["agregacao"], "fragmentacao": config["politica_textos_longos"],
             "backend": modelo["ambiente"]["backend"],
             "precisao_inferencia": modelo["ambiente"]["precisao_calculo"]}
    return original, consultas, fonte


def importar_vetorizacao(registro):
    """Valide e normalize uma execução completa, sem gerar nenhum vetor.

    Retorna relato validado, consultas, metadados de fonte e cópia do JSON.
    Vetores agregados são aproveitados exatamente como foram exportados.
    """
    _limites(registro)
    try:
        _exigir(registro.get("etapa") == "09_vetorizacao", "Importe a exportação da etapa 09 de vetorização.")
        if "perfil" in registro:
            _exigir(registro.get("schema_version") == "1.0.0", "Versão vetorial não suportada.")
            original, consultas, fonte = _importar_atual(registro)
        else:
            original, consultas, fonte = _importar_portatil(registro)
        _exigir(len(original) <= MAX_CARACTERES_RELATO, "O relato excede o limite de 256 KiB de caracteres.")
        relato = validar_relato(original)
        fonte.update({k: deepcopy(registro[k]) for k in
                      ("execucao_id", "contexto_execucao_id", "documento_id", "schema_version", "contexto_sha256")})
        fonte["coordenadas"] = deepcopy(_preparacao(registro)["coordenadas"])
        return {"relato": relato, "consultas": consultas, "fonte": fonte, "registro": deepcopy(registro)}
    except ErroImportacao:
        raise
    except ErroEntrada as erro:
        raise ErroImportacao(str(erro)) from None
    except (ValueError, TypeError, KeyError, AttributeError, ImportError, OverflowError, RecursionError):
        raise ErroImportacao("A exportação vetorial está incompleta ou falhou na conferência de integridade.") from None


def conferir_compatibilidade(importado, manifesto):
    """Confira o espaço esperado pelo índice; query/passage são complementares.

    Nome de perfil e backend não definem incompatibilidade. O modelo, revisão,
    tokenizador e operação vetorial precisam corresponder ao índice de Freud.
    """
    try:
        _exigir(type(importado) is dict and type(manifesto) is dict,
                "A origem vetorial e o manifesto do índice precisam estar disponíveis.")
        fonte = importado["fonte"]
        modelo, revisao, dimensao = (manifesto[k] for k in ("modelo", "revisao", "dimensao"))
        _exigir(type(modelo) is str and bool(modelo) and type(revisao) is str
                and re.fullmatch(r"[0-9a-f]{40}", revisao) is not None
                and type(dimensao) is int and dimensao > 0, "O manifesto não identifica o espaço E5.")
        _exigir(fonte["modelo"] == modelo and fonte["revisao"] == revisao,
                "Os vetores importados usam modelo ou revisão diferentes do índice de Freud.")
        tokenizador = manifesto.get("tokenizador", {"identificacao": modelo, "revisao": revisao})
        if type(tokenizador) is str:
            tokenizador = {"identificacao": tokenizador, "revisao": manifesto.get("tokenizador_revisao", revisao)}
        _exigir(fonte["tokenizador"] == tokenizador,
                "O tokenizador dos vetores importados difere do tokenizador do índice de Freud.")
        _exigir(type(fonte["dimensao"]) is int and fonte["dimensao"] == dimensao,
                "A dimensão dos vetores importados difere do índice de Freud.")
        _exigir(fonte["pooling"] == manifesto.get("pooling", "mean")
                and fonte["normalizacao"] == "l2" and manifesto.get("normalizacao") == "L2"
                and fonte["formato_vetor"] == manifesto.get("tipo_vetor") == "float32",
                "A busca exige a mesma operação de média, normalização L2 e float32 do índice.")
        _exigir(fonte["prefixo"] == manifesto.get("prefixo_consultas") == "query: "
                and manifesto.get("prefixo_passagens") == "passage: ",
                "Os vetores importados devem usar query: para pesquisar o índice passage: de Freud.")
    except ErroImportacao:
        raise
    except (KeyError, TypeError, ValueError):
        raise ErroImportacao("Os metadados vetoriais não permitem conferir a compatibilidade com o índice.") from None


# Validador do formato portátil recebido com o pacote do Agente Analista.
# Adaptado aqui para manter os módulos do Lingua intactos. Somente stdlib;
# nenhuma fábrica, tokenização de modelo ou função de inferência é carregada.
from .contratos_importacao import (
    canonico, sha256, hash_json, json_estrito, exigir, identificador,
    configuracao_vetorizacao, descricao_modelo, perfil_compatibilidade,
    codificar_vetor, decodificar_vetor, ErroVetorizacao, ErroEntrada as ErroOrigemVetorial,
    ErroLimite,
)
from .validacao_vetorial import (
    mapear_intervalos, validar_contexto_exportado as validar_unidades_contexto,
)

ErroContexto = ErroVetorizacao

_portatil_SCHEMA_VERSION = "1.1.0"


_portatil_SCHEMA_VERSIONS = ("1.0.0", _portatil_SCHEMA_VERSION)


_portatil_MODULO_VERSION = "1.1.0"


_portatil_ETAPA = "09_vetorizacao"


_portatil_MAX_REPRESENTACOES = 25_000


_portatil_MAX_BLOCOS = 50_000


_portatil_MAX_TOKENS_TOTAL = 2_000_000


_portatil_MAX_BYTES_VETORES = 256 * 1024 * 1024


_portatil_MAX_CARACTERES_ENTRADAS = 32 * 1024 * 1024


def _portatil_data(valor, *, gerar=False):
    if gerar and valor is None:
        valor = datetime.now(timezone.utc).isoformat()
    if gerar and isinstance(valor, datetime):
        valor = valor.isoformat()
    exigir(type(valor) is str, "registrado_em deve ser ISO 8601 com fuso.")
    try:
        instante = datetime.fromisoformat(valor.replace("Z", "+00:00"))
        exigir(instante.utcoffset() is not None, "registrado_em precisa de fuso horário.")
    except (ValueError, OverflowError) as erro:
        if isinstance(erro, ErroVetorizacao):
            raise
        raise ErroVetorizacao("registrado_em inválido.") from erro
    return valor


def _portatil_fonte(contexto):
    try:
        relatorio = validar_unidades_contexto(contexto)
    except (ErroContexto, TypeError, KeyError, RecursionError) as erro:
        raise ErroOrigemVetorial(f"Origem da etapa 08 inválida: {erro}") from erro
    if relatorio.get("pronto_para_etapa_09") is not True:
        raise ErroOrigemVetorial("A execução contextual não está pronta para a etapa 09.")
    return contexto["regras"]["analise"]["anotacao"]["segmentacao"]["preparacao"]


def _portatil_limites():
    return {"max_representacoes": _portatil_MAX_REPRESENTACOES, "max_blocos": _portatil_MAX_BLOCOS,
            "max_tokens_total": _portatil_MAX_TOKENS_TOTAL, "max_bytes_vetores": _portatil_MAX_BYTES_VETORES,
            "max_caracteres_entradas": _portatil_MAX_CARACTERES_ENTRADAS}


def _portatil_alvos(contexto, preparacao, execucao_id, *, incluir_paragrafos=True):
    resultado = []
    for unidade in contexto["unidades"]:
        for tipo in ("foco", "janela"):
            bloco = unidade[tipo]
            resultado.append({"tipo": tipo, "unidade_id": unidade["id"],
                              "periodo_foco_id": unidade["periodo_foco_id"],
                              "janela_logica_id": unidade["janela_logica_id"] if tipo == "janela" else None,
                              "campo": tipo + ".texto", "texto": bloco["texto"],
                              "sha256_texto": bloco["sha256_texto"],
                              "trabalho": deepcopy(bloco["trabalho"]), "original": deepcopy(bloco["original"])})
    if incluir_paragrafos:
        segmentacao = contexto["regras"]["analise"]["anotacao"]["segmentacao"]
        for paragrafo in segmentacao["paragrafos"]:
            intervalo = paragrafo["trabalho"]
            texto = preparacao["trabalho"]["texto"][intervalo["inicio"]:intervalo["fim"]]
            resultado.append({"tipo": "paragrafo", "paragrafo_id": paragrafo["id"],
                              "unidade_id": None, "periodo_foco_id": None,
                              "janela_logica_id": None, "campo": "segmentacao.paragrafos.texto",
                              "texto": texto, "sha256_texto": sha256(texto),
                              "trabalho": deepcopy(intervalo), "original": deepcopy(paragrafo["original"])})
    resultado.append({"tipo": "documento", "unidade_id": None, "periodo_foco_id": None,
                      "janela_logica_id": None, "campo": "preparacao.trabalho.texto",
                      "texto": preparacao["trabalho"]["texto"], "sha256_texto": preparacao["trabalho"]["sha256"],
                      "trabalho": {"inicio": 0, "fim": len(preparacao["trabalho"]["texto"])},
                      "original": {"inicio": 0, "fim": len(preparacao["original"]["texto"])}})
    if len(resultado) > _portatil_MAX_REPRESENTACOES:
        raise ErroLimite("Quantidade de representações excede o limite; nenhuma origem foi omitida.")
    if sum(len(a["texto"]) for a in resultado) > _portatil_MAX_CARACTERES_ENTRADAS:
        raise ErroLimite("Textos excedem o limite contextual da etapa 09; nenhum texto foi truncado.")
    for i, alvo in enumerate(resultado):
        alvo.update(id=f"{execucao_id}:representacao:{i}", ordem=i)
    return resultado


def _portatil_entrada(texto, prefixo, tokenizacao):
    exigir(type(tokenizacao) is dict and set(tokenizacao) == {"input_ids", "offset_mapping", "special_tokens_mask"},
            "Tokenização deve fornecer IDs, offsets e máscara de tokens especiais.")
    ids, offsets, especiais = (tokenizacao[k] for k in ("input_ids", "offset_mapping", "special_tokens_mask"))
    exigir(type(ids) is list and bool(ids) and all(type(v) is int and 0 <= v < 2 ** 63 for v in ids), "IDs de tokens inválidos.")
    exigir(type(offsets) is list and type(especiais) is list and len(ids) == len(offsets) == len(especiais),
            "Comprimentos da tokenização inconsistentes.")
    efetivo = prefixo + texto
    conteudo = 0
    anterior = 0
    for par, especial in zip(offsets, especiais):
        exigir(type(par) is list and len(par) == 2 and all(type(v) is int for v in par), "Offsets devem ser pares Unicode inteiros.")
        inicio, fim = par
        exigir(0 <= inicio <= fim <= len(efetivo), "Offset fora da entrada efetiva.")
        exigir(type(especial) is int and especial in (0, 1), "Máscara de especiais inválida.")
        if especial:
            exigir(par == [0, 0], "Tokens especiais precisam de offset vazio.")
        elif fim > inicio:
            exigir(inicio >= anterior, "Offsets de tokens não seguem a ordem textual.")
            # Tokenizadores podem compartilhar offsets de um caractere em
            # subpeças; não exigimos que os intervalos sejam disjuntos.
            anterior = inicio
            if fim > len(prefixo) and efetivo[max(inicio, len(prefixo)):fim].strip():
                conteudo += 1
    exigir(not texto.strip() or conteudo > 0,
            "Tokenização omitiu todo o conteúdo canônico; ausência de vetor não pode ocultar texto.")
    return {"texto": efetivo, "input_ids": ids[:], "offset_mapping": deepcopy(offsets),
            "special_tokens_mask": especiais[:], "sha256_texto": sha256(efetivo),
            "sha256_token_ids": hash_json(ids), "tokens_total": len(ids), "tokens_conteudo": conteudo}


def _portatil_artefato_id(entrada, geracao):
    return hash_json({"versao": "1.0.0", "geracao_sha256": geracao["sha256"],
                      "entrada_sha256": entrada["sha256_texto"], "token_ids_sha256": entrada["sha256_token_ids"]})


def _portatil_agregar(blocos, artefatos, dimensao):
    ativos = [b for b in blocos if b["peso_tokens"] > 0]
    if not ativos:
        return None
    linhas = [(b["peso_tokens"], decodificar_vetor(artefatos[b["artefato_id"]]["armazenamento"], dimensao)) for b in ativos]
    total = sum(peso for peso, _ in linhas)
    valores = [math.fsum(peso * vetor[i] for peso, vetor in linhas) / total for i in range(dimensao)]
    norma = math.sqrt(math.fsum(v * v for v in valores))
    exigir(norma > 0 and math.isfinite(norma), "Agregação sem norma válida; vetor zero não foi fabricado.")
    return codificar_vetor([v / norma for v in valores], dimensao)


def _portatil_concluir_representacao(alvo, blocos, artefatos, contexto, preparacao, compatibilidade, configuracao, dimensao):
    ativos = [b for b in blocos if b["peso_tokens"] > 0]
    if not ativos:
        construcao, vetor = "sem_conteudo", None
    elif len(blocos) == 1:
        construcao, vetor = "direta", deepcopy(artefatos[ativos[0]["artefato_id"]]["armazenamento"])
    elif configuracao["agregar"]:
        construcao, vetor = "agregada", _portatil_agregar(blocos, artefatos, dimensao)
    else:
        construcao, vetor = "blocos", None
    selecao = {"versao": "1.0.0", "documento_id": contexto["documento_id"],
               "sha256_documento_original": preparacao["original"]["sha256"],
               "tipo": alvo["tipo"], "campo": alvo["campo"], "original": deepcopy(alvo["original"]),
               "sha256_texto": alvo["sha256_texto"], "janela_logica_id": alvo["janela_logica_id"],
               "compatibilidade_sha256": compatibilidade["sha256"],
               "plano": [{"relativo": b["relativo"], "sha256_texto": b["sha256_texto"],
                         "sha256_entrada": b["entrada_modelo"]["sha256_texto"],
                         "sha256_token_ids": b["entrada_modelo"]["sha256_token_ids"], "peso_tokens": b["peso_tokens"]} for b in blocos]}
    return {**deepcopy(alvo), "construcao": construcao, "blocos": blocos, "vetor": vetor,
            "selecao_logica": selecao, "representacao_logica_id": hash_json(selecao)}


def _portatil_relatorio(modelo, representacoes, artefatos):
    reais = modelo["natureza"] == "inferencia_real"
    return {"estado": "valido", "pronto_para_uso": reais and any(r["vetor"] is not None or any(b["artefato_id"] for b in r["blocos"]) for r in representacoes),
            "inferencia_real": reais, "cobertura": {
                "representacoes_total": len(representacoes), "blocos_total": sum(len(r["blocos"]) for r in representacoes),
                "artefatos_total": len(artefatos), "gerados": sum(a["origem_calculo"] == "gerado" for a in artefatos),
                "reutilizados": sum(a["origem_calculo"] == "reutilizado" for a in artefatos),
                "sem_conteudo": sum(r["construcao"] == "sem_conteudo" for r in representacoes)},
            "verificacoes": ["origem08_validada", "campos_hashes_e_associacoes", "prefixos_ids_e_offsets", "blocos_cobertura_e_pesos", "bytes_dimensao_finitude_norma", "perfis_e_identidades", "agregacao_recalculada_sem_modelo"],
            "limites": ["Integridade estrutural não comprova qualidade semântica nem autentica a inferência.",
                        "A média agregada perde ordem e relações globais entre blocos.",
                        "Query e passage são papéis complementares na recuperação; equivalência exige perfis iguais."]}


def _portatil_validar_vetorizacao(registro):
    """Confira o registro inteiro e os vetores sem chamar um gerador."""
    exigir(type(registro) is dict, "Registro vetorial deve ser objeto JSON.")
    json_estrito(registro)
    campos = {"schema_version", "etapa", "execucao_id", "contexto_execucao_id", "documento_id", "registrado_em", "contexto", "contexto_sha256", "coordenadas", "modelo", "configuracao", "compatibilidade", "geracao", "processamento", "representacoes", "artefatos", "validacao"}
    exigir(set(registro) == campos and registro["schema_version"] in _portatil_SCHEMA_VERSIONS and registro["etapa"] == _portatil_ETAPA, "Contrato ou versão vetorial não suportado.")
    modulo_version = "1.0.0" if registro["schema_version"] == "1.0.0" else _portatil_MODULO_VERSION
    contexto = registro["contexto"]
    preparacao = _portatil_fonte(contexto)
    identificacao = identificador(registro["execucao_id"], "execucao_id")
    _portatil_data(registro["registrado_em"])
    exigir(identificacao != contexto["execucao_id"], "A etapa 09 precisa de ID próprio.")
    exigir(registro["contexto_execucao_id"] == contexto["execucao_id"] and registro["documento_id"] == contexto["documento_id"]
            and registro["contexto_sha256"] == hash_json(contexto) and canonico(registro["coordenadas"]) == canonico(contexto["coordenadas"]), "Origem contextual ou seu hash divergente.")
    modelo = descricao_modelo(registro["modelo"])
    informado = registro["configuracao"]
    exigir(type(informado) is dict and all(k in informado for k in ("perfil", "max_tokens", "tamanho_lote", "agregar")), "Configuração ausente.")
    config = configuracao_vetorizacao({k: informado[k] for k in ("perfil", "max_tokens", "tamanho_lote", "agregar")}, limite_tokens=modelo["limite_tokens"])
    exigir(canonico(config) == canonico(informado), "Configuração ou política adulterada.")
    compatibilidade = perfil_compatibilidade(modelo, config)
    descricao_geracao = {"compatibilidade": compatibilidade, "ambiente": modelo["ambiente"], "tamanho_lote": config["tamanho_lote"], "modulo_version": modulo_version}
    geracao = {"descricao": descricao_geracao, "sha256": hash_json(descricao_geracao)}
    exigir(canonico(registro["compatibilidade"]) == canonico(compatibilidade) and canonico(registro["geracao"]) == canonico(geracao), "Perfis ou assinaturas de geração inconsistentes.")
    exigir(canonico(registro["processamento"]) == canonico({"modulo": {"nome": "vetorizacao", "versao": modulo_version}, "limites_recursos": _portatil_limites()}), "Versão ou limites de processamento inconsistentes.")
    alvos = _portatil_alvos(contexto, preparacao, identificacao,
                    incluir_paragrafos=registro["schema_version"] != "1.0.0")
    reps = registro["representacoes"]
    lista_artefatos = registro["artefatos"]
    exigir(type(reps) is list and len(reps) == len(alvos) and type(lista_artefatos) is list, "Cobertura de representações incorreta.")
    exigir(len(lista_artefatos) <= _portatil_MAX_BLOCOS and len(lista_artefatos) * modelo["dimensao"] * 4 <= _portatil_MAX_BYTES_VETORES,
            "Artefatos excedem limites de recursos.")
    artefatos = {}
    for a in lista_artefatos:
        exigir(type(a) is dict and set(a) == {"id", "entrada_modelo", "armazenamento", "origem_calculo"}, "Formato de artefato inválido.")
        identificador(a["id"], "artefato.id")
        exigir(a["id"] not in artefatos and a["origem_calculo"] in ("gerado", "reutilizado"), "Artefato duplicado ou origem de cálculo inválida.")
        decodificar_vetor(a["armazenamento"], modelo["dimensao"])
        artefatos[a["id"]] = a
    esperadas, pendentes, vistos, conjunto_vistos = [], [], [], set()
    tokens_total, blocos_total, bytes_total = 0, 0, len(artefatos) * modelo["dimensao"] * 4
    for alvo, rep in zip(alvos, reps):
        exigir(type(rep) is dict and type(rep.get("blocos")) is list, "Representação sem blocos válidos.")
        blocos, proximo = rep["blocos"], 0
        exigir(blocos_total + len(blocos) <= _portatil_MAX_BLOCOS, "Registro excede o limite de blocos.")
        exigir(bool(blocos) or not alvo["texto"].strip(), "Texto com conteúdo sem cobertura de blocos.")
        for i, b in enumerate(blocos):
            exigir(type(b) is dict and set(b) == {"id", "ordem", "relativo", "trabalho", "original", "texto", "sha256_texto", "entrada_modelo", "peso_tokens", "artefato_id"}, "Contrato de bloco inválido.")
            relativo = b["relativo"]
            exigir(type(relativo) is dict and set(relativo) == {"inicio", "fim"} and all(type(v) is int for v in relativo.values()), "Intervalo relativo inválido.")
            inicio, fim = relativo["inicio"], relativo["fim"]
            exigir(inicio == proximo and inicio < fim <= len(alvo["texto"]), "Blocos possuem lacuna, sobreposição ou limite inválido.")
            texto = alvo["texto"][inicio:fim]
            e = b["entrada_modelo"]
            exigir(type(e) is dict and all(k in e for k in ("input_ids", "offset_mapping", "special_tokens_mask")), "Entrada do bloco ausente.")
            entrada = _portatil_entrada(texto, config["prefixo"], {k: e[k] for k in ("input_ids", "offset_mapping", "special_tokens_mask")})
            exigir(canonico(e) == canonico(entrada) and entrada["tokens_total"] <= config["max_tokens"], "Entrada, IDs, hash ou limite de tokens inconsistente.")
            artefato_id = _portatil_artefato_id(entrada, geracao) if entrada["tokens_conteudo"] else None
            esperado = {"id": f"{alvo['id']}:bloco:{i}", "ordem": i, "relativo": relativo,
                        "trabalho": {"inicio": alvo["trabalho"]["inicio"] + inicio, "fim": alvo["trabalho"]["inicio"] + fim},
                        "original": b["original"], "texto": texto, "sha256_texto": sha256(texto),
                        "entrada_modelo": entrada, "peso_tokens": entrada["tokens_conteudo"], "artefato_id": artefato_id}
            exigir(canonico(b) == canonico(esperado), "Bloco, origem, peso ou identidade adulterados.")
            if artefato_id is not None:
                exigir(artefato_id in artefatos and canonico(artefatos[artefato_id]["entrada_modelo"]) == canonico(entrada), "Associação de artefato incorreta.")
                if artefato_id not in conjunto_vistos:
                    conjunto_vistos.add(artefato_id)
                    vistos.append(artefato_id)
            proximo = fim
            blocos_total += 1
            tokens_total += entrada["tokens_total"]
            exigir(tokens_total <= _portatil_MAX_TOKENS_TOTAL, "Registro excede o limite de tokens.")
            pendentes.append(b)
        exigir(not blocos or proximo == len(alvo["texto"]), "Blocos não cobrem o texto integral.")
        esperadas.append(_portatil_concluir_representacao(alvo, blocos, artefatos, contexto, preparacao, compatibilidade, config, modelo["dimensao"]))
        if any(b["peso_tokens"] > 0 for b in blocos) and (len(blocos) == 1 or config["agregar"]):
            bytes_total += modelo["dimensao"] * 4
    exigir(vistos == [a["id"] for a in lista_artefatos], "Artefatos sem origem, omitidos, duplicados ou reordenados.")
    exigir(blocos_total <= _portatil_MAX_BLOCOS and tokens_total <= _portatil_MAX_TOKENS_TOTAL and bytes_total <= _portatil_MAX_BYTES_VETORES, "Registro excede limites de recursos.")
    originais = mapear_intervalos(preparacao, [(b["trabalho"]["inicio"], b["trabalho"]["fim"]) for b in pendentes])
    exigir(all(canonico(b["original"]) == canonico(o) for b, o in zip(pendentes, originais)), "Mapeamento original dos blocos inconsistente.")
    exigir(canonico(reps) == canonico(esperadas), "Representações, vetores, agregação ou identidade lógica inconsistentes.")
    relatorio = _portatil_relatorio(modelo, esperadas, lista_artefatos)
    exigir(canonico(registro["validacao"]) == canonico(relatorio), "Relatório de validação inconsistente.")
    return deepcopy(relatorio)
