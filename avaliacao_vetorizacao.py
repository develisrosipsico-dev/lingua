"""Avaliação rastreável de recuperação e recursos, sem carregar modelos.

Recall, MRR e nDCG usam referências fornecidas pelo avaliador. Resultados
numéricos não certificam qualidade fora desse conjunto. Tempo e memória só
aparecem como medições quando fornecidos pelo chamador.
"""

import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

from vetorizacao import codificar_vetor, validar_vetorizacao, verificar_compatibilidade


SCHEMA_VERSION = "1.0.0"


class ErroAvaliacao(ValueError):
    """Registro, seleção, referências ou medição inválidos."""


def _exigir(condicao, mensagem):
    if not condicao:
        raise ErroAvaliacao(mensagem)


def _canonico(valor):
    try:
        return json.dumps(valor, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError, UnicodeError) as erro:
        raise ErroAvaliacao("A avaliação precisa conter JSON finito e serializável.") from erro


def _hash(valor):
    return hashlib.sha256(_canonico(valor).encode("utf-8")).hexdigest()


def _validar(registro):
    try:
        validar_vetorizacao(registro)
    except (ValueError, TypeError, KeyError) as erro:
        raise ErroAvaliacao(f"Registro vetorial inválido: {erro}") from erro


def _origem(registro):
    return {campo: deepcopy(registro[campo]) for campo in (
        "execucao_id", "documento_id", "contexto_execucao_id", "contexto_sha256",
        "configuracao_sha256", "espaco_vetorial_id",
    )} | {"registro_sha256": _hash(registro)}


def _ids(valores, nome):
    _exigir(type(valores) is list and bool(valores)
            and all(type(valor) is str and valor.strip() for valor in valores)
            and len(set(valores)) == len(valores),
            f"{nome} deve ser uma lista não vazia de IDs textuais, sem repetição.")
    return valores[:]


def _vetores(registro, ids):
    representacoes = {r["id"]: r for r in registro["representacoes"]}
    artefatos = {a["id"]: a for a in registro["artefatos"]}
    vetores = {}
    for identificador in ids:
        _exigir(identificador in representacoes, f"Representação inexistente: {identificador}.")
        artefato_id = representacoes[identificador]["artefato_id"]
        _exigir(artefato_id is not None, f"Representação sem vetor: {identificador}.")
        valores = artefatos[artefato_id]["valores"]
        norma = math.hypot(*valores)
        _exigir(norma > 0 and math.isfinite(norma), "A recuperação exige vetores com norma finita e positiva.")
        # Renormalizar ambas as pontas para cosseno real, inclusive FP16.
        vetores[identificador] = [valor / norma for valor in valores]
    return vetores


def _referencias(referencias, consultas, corpus):
    _exigir(type(referencias) is dict and bool(referencias),
            "referencias deve mapear consultas para relevâncias não vazias.")
    _exigir(all(type(k) is str and k.strip() for k in referencias), "IDs de consulta devem ser textos não vazios.")
    _exigir(all(k in consultas for k in referencias), "Uma consulta de referência não pertence à execução selecionada.")
    normalizadas = {}
    for consulta_id, relevantes in referencias.items():
        if type(relevantes) is list:
            relevantes = {identificador: 1 for identificador in _ids(relevantes, "relevantes")}
        _exigir(type(relevantes) is dict and bool(relevantes), "Cada consulta precisa de ao menos um conteúdo relevante.")
        _exigir(all(type(k) is str and k.strip() and k in corpus for k in relevantes),
                "Toda referência relevante precisa pertencer ao corpus selecionado.")
        for grau in relevantes.values():
            try:
                valido = type(grau) in (int, float) and grau > 0 and math.isfinite(grau)
            except OverflowError:
                valido = False
            _exigir(valido, "Graus de relevância devem ser números finitos e positivos, sem booleanos.")
        normalizadas[consulta_id] = dict(relevantes)
    return normalizadas


def _ganhos(relevantes):
    """Ganhos 2**grau-1 com escala comum, sem overflow para graus grandes."""
    maior = max(relevantes.values())
    log2 = math.log(2)
    return {identificador: math.exp((grau - maior) * log2) * -math.expm1(-grau * log2)
            for identificador, grau in relevantes.items()}


def avaliar_recuperacao(registro_consultas, registro_conteudos, referencias,
                       *, ks=(1, 3, 10), conteudo_ids=None):
    """Avalie cosseno exato contra relevâncias explícitas.

    Por padrão o corpus contém somente representações de período. Uma seleção
    explícita define sua própria ordem; empates preservam a ordem desse corpus.
    As médias são macro, com o mesmo peso por consulta. Recall usa o total de
    referências positivas; MRR considera a primeira relevância positiva.
    nDCG usa ganho 2**grau-1 e desconto log2(posição+1).
    """
    _validar(registro_consultas)
    _validar(registro_conteudos)
    try:
        compatibilidade = verificar_compatibilidade(registro_consultas, registro_conteudos, operacao="recuperacao")
    except ValueError as erro:
        raise ErroAvaliacao(str(erro)) from erro
    _exigir(compatibilidade["compativel"] is True,
            "Não existe contrato explícito de recuperação para os espaços selecionados.")
    _exigir(type(ks) in (tuple, list) and bool(ks)
            and all(type(k) is int and k > 0 for k in ks) and len(set(ks)) == len(ks),
            "ks deve conter inteiros positivos distintos.")
    ks = list(ks)
    if conteudo_ids is None:
        conteudo_ids = [r["id"] for r in registro_conteudos["representacoes"] if r["tipo"] == "periodo"]
    conteudo_ids = _ids(conteudo_ids, "conteudo_ids")
    por_consulta = {r["id"]: r for r in registro_consultas["representacoes"]}
    relevantes = _referencias(referencias, por_consulta, set(conteudo_ids))
    # A ordem das consultas vem do registro, não da ordem de um objeto JSON.
    consulta_ids = [r["id"] for r in registro_consultas["representacoes"] if r["id"] in relevantes]
    consultas = _vetores(registro_consultas, consulta_ids)
    conteudos = _vetores(registro_conteudos, conteudo_ids)
    resultados = []
    for consulta_id in consulta_ids:
        vetor = consultas[consulta_id]
        classificacao = []
        for ordem, conteudo_id in enumerate(conteudo_ids):
            outro = conteudos[conteudo_id]
            _exigir(len(vetor) == len(outro), "Dimensões incompatíveis para cosseno.")
            cosseno = math.fsum(a * b for a, b in zip(vetor, outro))
            # Erro de arredondamento não pode colocar um cosseno fora de [-1,1].
            cosseno = max(-1.0, min(1.0, cosseno))
            classificacao.append((conteudo_id, cosseno, ordem))
        classificacao.sort(key=lambda item: (-item[1], item[2]))
        referencia = relevantes[consulta_id]
        ganhos = _ganhos(referencia)
        ideais = sorted(ganhos.values(), reverse=True)
        metricas = {}
        for k in ks:
            selecionados = classificacao[:k]
            posicoes = [pos for pos, (identificador, _, _) in enumerate(selecionados, 1) if identificador in referencia]
            dcg = math.fsum(ganhos.get(identificador, 0) / math.log2(pos + 1)
                           for pos, (identificador, _, _) in enumerate(selecionados, 1))
            idcg = math.fsum(ganho / math.log2(pos + 1) for pos, ganho in enumerate(ideais[:k], 1))
            metricas[str(k)] = {
                "recall": len(posicoes) / len(referencia),
                "mrr": 1 / posicoes[0] if posicoes else 0.0,
                "ndcg": min(1.0, max(0.0, dcg / idcg)),
            }
        resultados.append({
            "consulta_id": consulta_id, "relevantes": deepcopy(referencia),
            "ranking": [{"conteudo_id": identificador, "posicao": pos, "cosseno": cosseno,
                         "relevancia": referencia.get(identificador, 0)}
                        for pos, (identificador, cosseno, _) in enumerate(classificacao[:max(ks)], 1)],
            "metricas": metricas,
        })
    medias = {str(k): {metrica: math.fsum(r["metricas"][str(k)][metrica] for r in resultados) / len(resultados)
                      for metrica in ("recall", "mrr", "ndcg")} for k in ks}
    relatorio = {
        "schema_version": SCHEMA_VERSION, "tipo": "avaliacao_recuperacao",
        "consultas": _origem(registro_consultas), "conteudos": _origem(registro_conteudos),
        "compatibilidade": compatibilidade,
        "configuracao": {"metrica": "cosseno_com_normas_nas_duas_pontas", "ks": ks,
                         "desempate": "ordem_corpus", "media": "macro_por_consulta",
                         "ganho_ndcg": "2**grau-1", "desconto_ndcg": "log2(posicao+1)",
                         "ranking_retido": "ate_maior_k", "corpus_ids": conteudo_ids,
                         "consulta_ids": consulta_ids},
        "referencias": deepcopy(relevantes), "referencias_sha256": _hash(relevantes),
        "quantidades": {"consultas": len(consulta_ids), "conteudos": len(conteudo_ids)},
        "medias": medias, "resultados": resultados,
        "limitacao": "As métricas descrevem somente o corpus e as relevâncias fornecidos; não certificam outros usos.",
    }
    relatorio["configuracao_avaliacao_sha256"] = _hash(relatorio["configuracao"])
    _canonico(relatorio)
    return relatorio


def caracterizar_execucao(registro, *, tempo_total_segundos=None, pico_memoria_bytes=None,
                         memoria_gpu_bytes=None, tempo_carregamento_segundos=None):
    """Conte bytes vetoriais únicos e associe medições fornecidas, sem estimá-las."""
    _validar(registro)
    medicoes = {"tempo_total_segundos": tempo_total_segundos,
                "tempo_carregamento_segundos": tempo_carregamento_segundos,
                "pico_memoria_bytes": pico_memoria_bytes, "memoria_gpu_bytes": memoria_gpu_bytes}
    for campo, valor in medicoes.items():
        if valor is None:
            continue
        if campo.endswith("_bytes"):
            _exigir(type(valor) is int and valor >= 0, f"{campo} deve ser inteiro não negativo.")
        else:
            try:
                valido = type(valor) in (int, float) and valor >= 0 and math.isfinite(valor)
            except OverflowError:
                valido = False
            _exigir(valido, f"{campo} deve ser número finito e não negativo.")
    artefatos = []
    formatos = {}
    for artefato in registro["artefatos"]:
        tamanho = len(codificar_vetor(artefato["valores"], artefato["formato"]))
        artefatos.append({"id": artefato["id"], "dimensao": artefato["dimensao"],
                         "formato": artefato["formato"], "bytes": tamanho,
                         "sha256_bytes": artefato["sha256_bytes"]})
        resumo = formatos.setdefault(artefato["formato"], {"artefatos": 0, "bytes": 0})
        resumo["artefatos"] += 1
        resumo["bytes"] += tamanho
    relatorio = {
        "schema_version": SCHEMA_VERSION, "tipo": "caracterizacao_execucao",
        "origem": _origem(registro), "perfil": deepcopy(registro["perfil"]),
        "processamento": deepcopy(registro["processamento"]),
        "representacoes": {"total": len(registro["representacoes"]),
                           "com_vetor": sum(r["artefato_id"] is not None for r in registro["representacoes"])},
        "vetores": {"artefatos_unicos": len(artefatos), "bytes_total": sum(a["bytes"] for a in artefatos),
                    "dimensoes": sorted({a["dimensao"] for a in artefatos}),
                    "formatos": formatos, "artefatos": artefatos},
        "medicoes": medicoes, "medicoes_fornecidas": [k for k, v in medicoes.items() if v is not None],
        "escopo_bytes": "Somente bytes dos vetores únicos; exclui metadados, índices e modelo.",
        "escopo_medicoes": "Valores fornecidos pelo chamador; este módulo não mede nem estima tempo ou memória.",
    }
    _canonico(relatorio)
    return relatorio


def _ler_json(caminho):
    def constante_invalida(valor):
        raise ErroAvaliacao(f"Constante JSON não finita: {valor}.")
    with open(caminho, encoding="utf-8", newline="") as arquivo:
        return json.load(arquivo, parse_constant=constante_invalida)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    comandos = parser.add_subparsers(dest="comando", required=True)
    recuperar = comandos.add_parser("recuperar", help="Avaliar recuperação com relevâncias controladas.")
    recuperar.add_argument("consultas")
    recuperar.add_argument("conteudos")
    recuperar.add_argument("referencias")
    recuperar.add_argument("--ks", type=int, nargs="+", default=[1, 3, 10])
    recuperar.add_argument("--conteudo-id", action="append", dest="conteudo_ids")
    recursos = comandos.add_parser("caracterizar", help="Caracterizar bytes e medições fornecidas.")
    recursos.add_argument("registro")
    recursos.add_argument("--tempo-total-segundos", type=float)
    recursos.add_argument("--tempo-carregamento-segundos", type=float)
    recursos.add_argument("--pico-memoria-bytes", type=int)
    recursos.add_argument("--memoria-gpu-bytes", type=int)
    for comando in (recuperar, recursos):
        comando.add_argument("--saida", help="Arquivo novo de relatório; omitir para escrever no terminal.")
    args = parser.parse_args(argv)
    try:
        if args.comando == "recuperar":
            relatorio = avaliar_recuperacao(_ler_json(args.consultas), _ler_json(args.conteudos),
                                           _ler_json(args.referencias), ks=args.ks, conteudo_ids=args.conteudo_ids)
        else:
            relatorio = caracterizar_execucao(_ler_json(args.registro), tempo_total_segundos=args.tempo_total_segundos,
                                             tempo_carregamento_segundos=args.tempo_carregamento_segundos,
                                             pico_memoria_bytes=args.pico_memoria_bytes, memoria_gpu_bytes=args.memoria_gpu_bytes)
        texto = json.dumps(relatorio, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.saida:
            with Path(args.saida).open("x", encoding="utf-8", newline="") as arquivo:
                arquivo.write(texto)
        else:
            print(texto, end="")
    except (OSError, ValueError, TypeError) as erro:
        parser.exit(2, f"Erro de avaliação: {erro}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
