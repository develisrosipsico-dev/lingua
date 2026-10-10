"""Compare backends sobre uma amostra explícita, sem autorizar similaridade.

As strings recebidas já são as entradas efetivas do modelo. Não acrescentamos
prefixos, não fragmentamos e não truncamos. O relatório documenta uma avaliação
amostral; ele não altera os contratos de comparação da etapa 09.
"""
from copy import deepcopy
import hashlib
import json
import math
import struct


class ErroCompatibilidadeEmbeddings(ValueError):
    """Entrada, descrição, tokenização ou saída inválida para a avaliação."""


_CAMPOS_TOKENS = ("input_ids", "attention_mask", "special_tokens_mask", "offsets")
_CAMPOS_IDENTIDADE = (
    ("modelo", "identificacao"), ("modelo", "revisao"),
    ("tokenizador", "identificacao"), ("tokenizador", "revisao"),
    ("dimensao",), ("pooling",),
)
_CAMPOS_CONFIGURACAO = (
    "limite_tokens", "limite_tokens_selecionado", "precisao_inferencia",
    "normalizacao_interna", "dispositivo", "prefixo_automatico", "truncamento_automatico",
)


def _numero_finito(valor):
    try:
        return type(valor) in (int, float) and math.isfinite(valor)
    except OverflowError:
        return False


def _json_exato(valor):
    """Tipos JSON estritos: bool não pode substituir um inteiro."""
    def conferir(item):
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) is list:
            for filho in item:
                conferir(filho)
            return
        if type(item) is dict and all(type(chave) is str for chave in item):
            for filho in item.values():
                conferir(filho)
            return
        raise ErroCompatibilidadeEmbeddings("A descrição/tokenização deve conter apenas valores JSON finitos.")
    try:
        conferir(valor)
        return json.dumps(valor, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (RecursionError, UnicodeError) as exc:
        raise ErroCompatibilidadeEmbeddings("Dados JSON inválidos ou recursivos.") from exc


def _descricao(adaptador, nome):
    try:
        descricao = adaptador.descrever()
    except Exception as exc:
        raise ErroCompatibilidadeEmbeddings(f"Falha ao descrever backend {nome}: {exc}") from exc
    if type(descricao) is not dict:
        raise ErroCompatibilidadeEmbeddings(f"Descrição do backend {nome} deve ser um objeto JSON.")
    _json_exato(descricao)
    for campo in ("modelo", "tokenizador"):
        entidade = descricao.get(campo)
        if type(entidade) is not dict or any(
            type(entidade.get(k)) is not str or not entidade[k].strip()
            for k in ("identificacao", "revisao")
        ):
            raise ErroCompatibilidadeEmbeddings(f"Backend {nome} deve declarar {campo}, identificação e revisão.")
    for campo in ("dimensao", "limite_tokens"):
        if type(descricao.get(campo)) is not int or descricao[campo] <= 0:
            raise ErroCompatibilidadeEmbeddings(f"Backend {nome}: {campo} deve ser inteiro positivo.")
    if "limite_tokens_selecionado" in descricao and (
        type(descricao["limite_tokens_selecionado"]) is not int
        or not 0 < descricao["limite_tokens_selecionado"] <= descricao["limite_tokens"]
    ):
        raise ErroCompatibilidadeEmbeddings(f"Backend {nome}: limite selecionado inválido ou maior que a capacidade.")
    if type(descricao.get("pooling")) is not str or not descricao["pooling"].strip():
        raise ErroCompatibilidadeEmbeddings(f"Backend {nome} deve declarar pooling.")
    for campo in ("prefixo_automatico", "truncamento_automatico"):
        if campo in descricao and descricao[campo] is not False:
            raise ErroCompatibilidadeEmbeddings(f"Backend {nome}: {campo} impede comparação de entradas exatas.")
    return deepcopy(descricao)


def _tokens(adaptador, texto, descricao, nome, indice):
    try:
        dados = adaptador.tokenizar(texto)
    except Exception as exc:
        raise ErroCompatibilidadeEmbeddings(f"Falha na tokenização {nome}, entrada {indice}: {exc}") from exc
    if type(dados) is not dict or any(type(dados.get(k)) is not list for k in _CAMPOS_TOKENS):
        raise ErroCompatibilidadeEmbeddings(f"Tokenização {nome}, entrada {indice}: campos obrigatórios ausentes.")
    _json_exato(dados)
    quantidade = len(dados["input_ids"])
    if not quantidade or any(len(dados[k]) != quantidade for k in _CAMPOS_TOKENS):
        raise ErroCompatibilidadeEmbeddings(f"Tokenização {nome}, entrada {indice}: comprimentos inconsistentes.")
    if any(type(v) is not int or v < 0 for v in dados["input_ids"]):
        raise ErroCompatibilidadeEmbeddings(f"Tokenização {nome}, entrada {indice}: IDs inválidos.")
    for campo in ("attention_mask", "special_tokens_mask"):
        if any(type(v) is not int or v not in (0, 1) for v in dados[campo]):
            raise ErroCompatibilidadeEmbeddings(f"Tokenização {nome}, entrada {indice}: {campo} inválida.")
    if not any(dados["attention_mask"]):
        raise ErroCompatibilidadeEmbeddings(f"Tokenização {nome}, entrada {indice}: atenção vazia.")
    for posicao, par in enumerate(dados["offsets"]):
        if (type(par) is not list or len(par) != 2 or any(type(v) is not int for v in par)
                or not 0 <= par[0] <= par[1] <= len(texto)):
            raise ErroCompatibilidadeEmbeddings(f"Tokenização {nome}, entrada {indice}: offsets inválidos.")
        if dados["special_tokens_mask"][posicao] and par != [0, 0]:
            raise ErroCompatibilidadeEmbeddings(f"Tokenização {nome}, entrada {indice}: especial com offset inválido.")
    if "token_type_ids" in dados and (
        type(dados["token_type_ids"]) is not list or len(dados["token_type_ids"]) != quantidade
        or any(type(v) is not int or v < 0 for v in dados["token_type_ids"])
    ):
        raise ErroCompatibilidadeEmbeddings(f"Tokenização {nome}, entrada {indice}: token_type_ids inválidos.")
    limite = descricao.get("limite_tokens_selecionado", descricao["limite_tokens"])
    if quantidade > limite:
        raise ErroCompatibilidadeEmbeddings(
            f"Entrada {indice} excede o limite do backend {nome}: "
            f"{quantidade} tokens > {limite}; nenhum truncamento foi aplicado."
        )
    return deepcopy(dados)


def _gerar(adaptador, textos, descricao, nome):
    try:
        vetores = adaptador.gerar(list(textos))
    except Exception as exc:
        raise ErroCompatibilidadeEmbeddings(f"Falha na geração do backend {nome}: {exc}") from exc
    if type(vetores) is not list or len(vetores) != len(textos):
        raise ErroCompatibilidadeEmbeddings(f"Backend {nome} retornou quantidade incorreta de vetores.")
    for indice, vetor in enumerate(vetores):
        if type(vetor) is not list or len(vetor) != descricao["dimensao"]:
            raise ErroCompatibilidadeEmbeddings(f"Backend {nome}, entrada {indice}: dimensão incorreta.")
        if any(not _numero_finito(v) for v in vetor):
            raise ErroCompatibilidadeEmbeddings(f"Backend {nome}, entrada {indice}: vetor não finito.")
        if not any(v != 0 for v in vetor):
            raise ErroCompatibilidadeEmbeddings(f"Backend {nome}, entrada {indice}: vetor zero.")
    return deepcopy(vetores)


def _normalizar(vetor):
    escala = max(abs(v) for v in vetor)
    escalado = [v / escala for v in vetor]
    norma_escalada = math.hypot(*escalado)
    norma = escala * norma_escalada
    return [v / norma_escalada for v in escalado], norma if math.isfinite(norma) else None


def _bytes_float32(vetor):
    try:
        return b"".join(struct.pack("<f", v) for v in vetor)
    except (OverflowError, struct.error):
        return None


def _erros(a, b):
    diferencas = [x - y for x, y in zip(a, b)]
    if any(not _numero_finito(v) for v in diferencas):
        return None, None
    maximo = max(abs(v) for v in diferencas)
    if maximo == 0:
        return 0.0, 0.0
    rms = maximo * math.sqrt(math.fsum((v / maximo) ** 2 for v in diferencas) / len(diferencas))
    return maximo, rms


def _verificacao_opcional(a, b, caminho):
    def obter(descricao):
        valor = descricao
        for chave in caminho:
            if type(valor) is not dict or chave not in valor:
                return False, None
            valor = valor[chave]
        return True, deepcopy(valor)
    presente_a, va = obter(a)
    presente_b, vb = obter(b)
    return {"campo": ".".join(caminho), "caminho": list(caminho),
        "presente_a": presente_a, "presente_b": presente_b, "a": va, "b": vb,
        "igual": _json_exato(va) == _json_exato(vb)
        if presente_a and presente_b and va is not None and vb is not None else None}


def _configuracoes(a, b):
    verificacoes = [_verificacao_opcional(a, b, (campo,)) for campo in _CAMPOS_CONFIGURACAO]
    # O snapshot inteiro permanece nas descrições. Aqui comparamos cada caminho
    # declarado, inclusive nomes de arquivos e SHA, sem inventar dados ausentes.
    verificacoes.append(_verificacao_opcional(a, b, ("artefatos",)))
    def caminhos(valor, caminho):
        if type(valor) is dict and valor:
            for chave, filho in valor.items():
                yield from caminhos(filho, caminho + (chave,))
        else:
            yield caminho
    caminhos_a = set(caminhos(a.get("artefatos", {}), ("artefatos",)))
    caminhos_b = set(caminhos(b.get("artefatos", {}), ("artefatos",)))
    for caminho in sorted(caminhos_a | caminhos_b):
        if caminho != ("artefatos",):
            verificacoes.append(_verificacao_opcional(a, b, caminho))
    return verificacoes


def avaliar_compatibilidade_backends(adaptador_a, adaptador_b, entradas, *, limiar_cosseno=0.99):
    """Avalie entradas efetivas exatas sem conceder compatibilidade global.

    O limiar é estrito: todos os cossenos devem ser maiores que ele. Metadados e
    os quatro campos obrigatórios da tokenização também precisam coincidir.
    ``token_type_ids`` é comparado quando ambos o fornecem; ausência assimétrica
    fica registrada, sem inventar valores. Bytes FP32 são diagnóstico de saída,
    não prova de igualdade de pesos ou de execução em outros dispositivos.
    """
    if type(entradas) is not list or not entradas or any(type(t) is not str for t in entradas):
        raise ErroCompatibilidadeEmbeddings("entradas deve ser uma lista não vazia de strings.")
    if not _numero_finito(limiar_cosseno) or not -1 <= limiar_cosseno <= 1:
        raise ErroCompatibilidadeEmbeddings("limiar_cosseno deve ser número finito entre -1 e 1.")
    textos = list(entradas)
    try:
        hashes = [hashlib.sha256(t.encode("utf-8")).hexdigest() for t in textos]
    except UnicodeError as exc:
        raise ErroCompatibilidadeEmbeddings("Entrada não pode ser codificada em UTF-8.") from exc
    a, b = _descricao(adaptador_a, "a"), _descricao(adaptador_b, "b")
    verificacoes = []
    for caminho in _CAMPOS_IDENTIDADE:
        va, vb = a, b
        for chave in caminho:
            va, vb = va[chave], vb[chave]
        verificacoes.append({"campo": ".".join(caminho), "a": deepcopy(va),
                             "b": deepcopy(vb), "igual": _json_exato(va) == _json_exato(vb)})
    if "pooling_configuracao" in a and "pooling_configuracao" in b:
        verificacoes.append({"campo": "pooling_configuracao", "a": a["pooling_configuracao"],
            "b": b["pooling_configuracao"],
            "igual": _json_exato(a["pooling_configuracao"]) == _json_exato(b["pooling_configuracao"])})
    tokenizacoes = [(_tokens(adaptador_a, t, a, "a", i), _tokens(adaptador_b, t, b, "b", i))
                   for i, t in enumerate(textos)]
    # Todos os limites são conferidos antes da primeira inferência.
    vetores_a = _gerar(adaptador_a, textos, a, "a")
    vetores_b = _gerar(adaptador_b, textos, b, "b")
    if (_json_exato(_descricao(adaptador_a, "a")) != _json_exato(a)
            or _json_exato(_descricao(adaptador_b, "b")) != _json_exato(b)):
        raise ErroCompatibilidadeEmbeddings("A descrição de um backend mudou durante a avaliação.")
    resultados = []
    for indice, (texto, (ta, tb), va, vb) in enumerate(zip(textos, tokenizacoes, vetores_a, vetores_b)):
        campos_iguais = {k: ta[k] == tb[k] for k in _CAMPOS_TOKENS}
        tipos = {"presente_a": "token_type_ids" in ta, "presente_b": "token_type_ids" in tb,
                 "igual": ta["token_type_ids"] == tb["token_type_ids"]
                 if "token_type_ids" in ta and "token_type_ids" in tb else None}
        tokens_iguais = all(campos_iguais.values()) and tipos["igual"] is not False
        numerica = {"estado": "dimensoes_diferentes", "cosseno": None,
                    "erro_maximo_absoluto": None, "erro_rms": None, "bytes_float32_iguais": False}
        if a["dimensao"] == b["dimensao"]:
            ua, norma_a = _normalizar(va)
            ub, norma_b = _normalizar(vb)
            cosseno = max(-1.0, min(1.0, math.fsum(x * y for x, y in zip(ua, ub))))
            maximo, rms = _erros(va, vb)
            maximo_l2, rms_l2 = _erros(ua, ub)
            ba, bb = _bytes_float32(va), _bytes_float32(vb)
            numerica = {"estado": "calculada", "normalizacao_comparacao": "l2_ambos",
                "norma_a": norma_a, "norma_b": norma_b, "cosseno": cosseno,
                "acima_limiar": cosseno > limiar_cosseno,
                "erro_maximo_absoluto": maximo, "erro_rms": rms,
                "erro_maximo_absoluto_normalizado": maximo_l2, "erro_rms_normalizado": rms_l2,
                "bytes_float32_iguais": ba == bb if ba is not None and bb is not None else None,
                "sha256_float32_a": hashlib.sha256(ba).hexdigest() if ba is not None else None,
                "sha256_float32_b": hashlib.sha256(bb).hexdigest() if bb is not None else None,
                "bytes_float32_normalizados_iguais": _bytes_float32(ua) == _bytes_float32(ub)}
        resultados.append({"indice": indice, "texto": texto, "sha256_utf8": hashes[indice],
            "tokenizacao": {"a": ta, "b": tb, "campos_iguais": campos_iguais,
                             "token_type_ids": tipos, "identica_campos_verificados": tokens_iguais},
            "numerica": numerica})
    metadados_iguais = all(v["igual"] for v in verificacoes)
    tokens_iguais = all(r["tokenizacao"]["identica_campos_verificados"] for r in resultados)
    acima_limiar = all(r["numerica"].get("acima_limiar", False) for r in resultados)
    aprovado = metadados_iguais and tokens_iguais and acima_limiar
    cossenos = [r["numerica"]["cosseno"] for r in resultados if r["numerica"]["cosseno"] is not None]
    relatorio = {"schema_version": "1.0.0", "tipo": "avaliacao_compatibilidade_backends",
        "estado": "compatibilidade_aproximada_amostral" if aprovado else "divergencia_na_amostra",
        "descricoes": {"a": a, "b": b}, "limiar_cosseno": limiar_cosseno,
        "criterio_cosseno": "estritamente_maior", "verificacoes_metadata": verificacoes,
        "verificacoes_configuracao": _configuracoes(a, b),
        "entradas": resultados, "resumo": {"entradas_total": len(textos),
            "metadados_compativeis": metadados_iguais, "tokenizacoes_verificadas_iguais": tokens_iguais,
            "todos_cossenos_acima_limiar": acima_limiar,
            "cosseno_minimo": min(cossenos) if cossenos else None,
            "cosseno_medio": math.fsum(cossenos) / len(cossenos) if cossenos else None},
        "compatibilidade_global": "nao_comprovada", "autoriza_operacoes_similaridade": False,
        "limitacoes": ["Conclusão limitada às entradas e configurações registradas nesta amostra.",
            "Igualdade de bytes FP32 nesta amostra não prova identidade de pesos, tokenizador ou execução futura.",
            "O relatório não cria nem altera contratos de comparação ou registros vetoriais.",
            "Campos opcionais ausentes em um backend não são inventados nem equiparados implicitamente."]}
    relatorio["limitacoes"].append(
        "Diferenças de precisão, dispositivo, limites ou artefatos ficam registradas; "
        "a classificação amostral não demonstra equivalência global dessas configurações."
    )
    _json_exato(relatorio)
    return relatorio


def _main():
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser(description="Comparação amostral de dois adaptadores reais, sem autorizar similaridade.")
    parser.add_argument("--entradas", required=True, help="Arquivo JSON com lista das entradas efetivas, incluindo prefixos.")
    parser.add_argument("--perfil-a", default="e5_simetrico")
    parser.add_argument("--perfil-b", default="e5_simetrico")
    parser.add_argument("--opcoes-a", default="{}", help="Opções JSON; modo offline é o padrão.")
    parser.add_argument("--opcoes-b", default="{}", help="Opções JSON; modo offline é o padrão.")
    parser.add_argument("--limiar-cosseno", type=float, default=0.99)
    parser.add_argument("--saida", help="Arquivo JSON do relatório; se omitido, usa stdout.")
    args = parser.parse_args()
    try:
        from modelo_embeddings import criar_adaptador
        from perfis_vetorizacao import carregar_perfil
        entradas = json.loads(Path(args.entradas).read_text(encoding="utf-8"))
        a = criar_adaptador(carregar_perfil(args.perfil_a, opcoes=json.loads(args.opcoes_a)))
        b = criar_adaptador(carregar_perfil(args.perfil_b, opcoes=json.loads(args.opcoes_b)))
        resultado = avaliar_compatibilidade_backends(a, b, entradas, limiar_cosseno=args.limiar_cosseno)
        texto = json.dumps(resultado, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.saida:
            with Path(args.saida).open("x", encoding="utf-8", newline="") as arquivo:
                arquivo.write(texto)
        else:
            print(texto, end="")
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Erro: {exc}\n")


if __name__ == "__main__":
    _main()
