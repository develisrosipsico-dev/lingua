"""Avaliação explícita de uma pequena referência, separada da prontidão técnica."""

import argparse
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from anotacao import anotar_segmentacao, validar_anotacao
from preparacao import preparar_texto
from segmentacao import segmentar_preparacao


REFERENCIA = Path(__file__).resolve().parent / "examples" / "referencia_morfologia.json"


def validar_referencia(referencia):
    if not isinstance(referencia, dict) or referencia.get("schema_version") != "1.0.0":
        raise ValueError("Formato da referência manual inválido.")
    if referencia.get("coordenadas") != {"unidade":"pontos_de_codigo_unicode","base":0,"fim_exclusivo":True}:
        raise ValueError("Coordenadas da referência incompatíveis.")
    casos = referencia.get("casos")
    if not isinstance(casos, list) or not casos:
        raise ValueError("A referência deve conter casos anotados.")
    ids = set()
    for caso in casos:
        if not isinstance(caso, dict) or not isinstance(caso.get("id"), str) or not caso["id"] or caso["id"] in ids:
            raise ValueError("Identificadores de casos inválidos ou repetidos.")
        ids.add(caso["id"])
        texto, alvos = caso.get("texto"), caso.get("alvos")
        if not isinstance(texto, str) or not isinstance(alvos, list) or not alvos:
            raise ValueError("Cada caso deve conter texto e alvos.")
        anterior = 0
        for alvo in alvos:
            if not isinstance(alvo, dict) or not isinstance(alvo.get("original"), dict):
                raise ValueError("Alvo ou intervalo inválido.")
            inicio, fim = alvo["original"].get("inicio"), alvo["original"].get("fim")
            if type(inicio) is not int or type(fim) is not int or not anterior <= inicio < fim <= len(texto):
                raise ValueError("Limites dos alvos inválidos, sobrepostos ou fora de ordem.")
            if alvo.get("texto") != texto[inicio:fim] or any(not isinstance(alvo.get(k), str) or not alvo[k] for k in ("lema","pos")):
                raise ValueError("Texto, lema ou classe da referência inválido.")
            anterior = fim
    return referencia


def comparar_anotacoes(registro, caso):
    """Compare previsões preservadas, sem corrigi-las ou executar outro modelo."""
    validar_anotacao(registro)
    if registro["segmentacao"]["preparacao"]["original"]["texto"] != caso["texto"]:
        raise ValueError("A anotação não corresponde ao texto da referência.")
    tokens = {(t["original"]["inicio"],t["original"]["fim"]):t for t in registro["anotacoes_tokens"]}
    resultados = []
    for alvo in caso["alvos"]:
        intervalo = alvo["original"]
        token = tokens.get((intervalo["inicio"],intervalo["fim"]))
        if token is None or token["texto"] != alvo["texto"]:
            raise ValueError(f"O alvo {alvo['texto']!r} não possui token com os limites esperados.")
        resultados.append({
            "texto": alvo["texto"], "token_id": token["token_id"], "original": deepcopy(intervalo),
            "esperado": {"lema":alvo["lema"],"pos":alvo["pos"]},
            "obtido": {"lema":token["lema"],"pos":token["pos"]},
            "acerto_lema": token["lema"] == alvo["lema"], "acerto_pos": token["pos"] == alvo["pos"],
        })
    return resultados


def avaliar_referencia(referencia):
    validar_referencia(referencia)
    casos = []
    for caso in referencia["casos"]:
        preparado = preparar_texto(caso["texto"], str(uuid4()))
        segmentado = segmentar_preparacao(preparado)
        anotado = anotar_segmentacao(segmentado)
        casos.append({
            "id":caso["id"], "texto":caso["texto"],
            "documento_id":anotado["documento_id"], "preparacao_id":anotado["preparacao_id"],
            "segmentacao_id":anotado["segmentacao_id"], "anotacao_id":anotado["anotacao_id"],
            "sha256_original":preparado["original"]["sha256"],
            "processamento":deepcopy(anotado["processamento"]),
            "resultados":comparar_anotacoes(anotado,caso),
        })
    resultados = [r for caso in casos for r in caso["resultados"]]
    total = len(resultados)
    acertos_lema = sum(r["acerto_lema"] for r in resultados)
    acertos_pos = sum(r["acerto_pos"] for r in resultados)
    return {
        "schema_version":"1.0.0", "etapa":"avaliacao_linguistica_05",
        "gerado_em":datetime.now(ZoneInfo("America/Sao_Paulo")).isoformat(),
        "referencia":deepcopy(referencia), "casos":casos,
        "resumo":{
            "frases":len(casos), "tokens_avaliados":total, "acertos_lema":acertos_lema,
            "acertos_pos":acertos_pos, "taxa_acerto_lema":acertos_lema/total,
            "taxa_acerto_pos":acertos_pos/total,
            "tokens_com_divergencia":sum(not(r["acerto_lema"] and r["acerto_pos"]) for r in resultados),
        },
    }


def formatar_markdown(relatorio):
    resumo = relatorio["resumo"]
    linhas = [
        "# Resultado da referência manual de morfologia", "",
        f"Avaliação gerada em {relatorio['gerado_em']}.", "",
        f"Foram avaliados {resumo['tokens_avaliados']} tokens selecionados em {resumo['frases']} frases.",
        f"Lemas: {resumo['acertos_lema']}/{resumo['tokens_avaliados']} acertos. "
        f"Classes: {resumo['acertos_pos']}/{resumo['tokens_avaliados']} acertos.", "",
        "Os resultados se restringem aos alvos anotados; artigos, pontuação e traços flexionais não foram avaliados.",
        "Divergências permanecem na saída do modelo e não alteram a prontidão técnica dos registros.", "",
        "| Frase | Token | Lema esperado | Lema obtido | POS esperada | POS obtida | Resultado |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    def cell(valor):
        return "null" if valor is None else str(valor).replace("|", "\\|").replace("\n", "<br>")
    for caso in relatorio["casos"]:
        for resultado in caso["resultados"]:
            status = "Acerto" if resultado["acerto_lema"] and resultado["acerto_pos"] else "Divergência"
            dados = [caso["id"],resultado["texto"],resultado["esperado"]["lema"],resultado["obtido"]["lema"],
                     resultado["esperado"]["pos"],resultado["obtido"]["pos"],status]
            linhas.append("| " + " | ".join(cell(v) for v in dados) + " |")
    modelo = relatorio["casos"][0]["processamento"]
    linhas += ["", f"Modelo: {modelo['modelo']['nome']} {modelo['modelo']['versao']}; spaCy {modelo['versao']}.",
               "Componentes: " + ", ".join(modelo["componentes_executados"]) + ".", ""]
    return "\n".join(linhas)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--referencia", type=Path, default=REFERENCIA)
    parser.add_argument("--saida-json", type=Path)
    parser.add_argument("--saida-markdown", type=Path)
    args = parser.parse_args()
    relatorio = avaliar_referencia(json.loads(args.referencia.read_text(encoding="utf-8")))
    if args.saida_json:
        args.saida_json.write_text(json.dumps(relatorio,ensure_ascii=False,allow_nan=False,indent=2)+"\n",encoding="utf-8")
    if args.saida_markdown:
        args.saida_markdown.write_text(formatar_markdown(relatorio),encoding="utf-8")
    print(json.dumps(relatorio["resumo"],ensure_ascii=False))


if __name__ == "__main__":
    main()
