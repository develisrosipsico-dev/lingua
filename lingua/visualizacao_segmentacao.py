"""Mapa de leitura dos registros já validados e selecionados pela aplicação.

As orações vêm das regras salvas. Esta projeção não executa modelos, não
resegmenta o texto e não altera a cadeia de origem nem os seus intervalos.
"""

from collections import defaultdict


_CLASSES = {
    "ADJ": "Adjetivo", "ADP": "Preposição", "ADV": "Advérbio",
    "AUX": "Verbo auxiliar", "CCONJ": "Conjunção coordenativa",
    "DET": "Determinante", "INTJ": "Interjeição", "NOUN": "Substantivo",
    "NUM": "Numeral", "PART": "Partícula", "PRON": "Pronome",
    "PROPN": "Substantivo próprio", "PUNCT": "Pontuação",
    "SCONJ": "Conjunção subordinativa", "SYM": "Símbolo",
    "VERB": "Verbo", "X": "Outra classe", "SPACE": "Espaço",
}
_DEPENDENCIAS = {
    "ROOT": "Raiz sintática", "nsubj": "Sujeito", "nsubj:pass": "Sujeito passivo",
    "csubj": "Sujeito oracional", "csubj:pass": "Sujeito oracional passivo",
    "obj": "Objeto direto", "iobj": "Objeto indireto",
    "obl": "Complemento ou modificador oblíquo", "obl:agent": "Agente da passiva",
    "advmod": "Modificador adverbial", "amod": "Modificador adjetival",
    "nmod": "Modificador nominal", "nummod": "Modificador numeral",
    "det": "Determinante", "case": "Preposição ou marca de caso",
    "aux": "Auxiliar", "aux:pass": "Auxiliar da passiva", "cop": "Cópula",
    "cc": "Conector de coordenação", "conj": "Coordenação",
    "mark": "Marcador de subordinação", "ccomp": "Complemento oracional",
    "xcomp": "Complemento oracional aberto", "advcl": "Modificador oracional adverbial",
    "acl": "Modificador oracional do nome", "acl:relcl": "Oração relativa",
    "appos": "Aposto", "punct": "Pontuação", "parataxis": "Parataxe",
    "expl": "Elemento expletivo", "fixed": "Expressão fixa",
    "flat": "Expressão sem hierarquia interna", "flat:name": "Parte de nome próprio",
    "compound": "Composição", "dep": "Relação não especificada",
}
_ATRIBUTOS = {
    "Gender": ("Gênero", {"Fem": "Feminino", "Masc": "Masculino", "Neut": "Neutro"}),
    "Number": ("Número", {"Sing": "Singular", "Plur": "Plural"}),
    "Person": ("Pessoa", {"1": "1.ª pessoa", "2": "2.ª pessoa", "3": "3.ª pessoa"}),
    "Tense": ("Tempo", {"Past": "Passado", "Pres": "Presente", "Fut": "Futuro", "Imp": "Imperfeito", "Pqp": "Mais-que-perfeito"}),
    "Mood": ("Modo", {"Ind": "Indicativo", "Sub": "Subjuntivo", "Imp": "Imperativo", "Cnd": "Condicional"}),
    "VerbForm": ("Forma verbal", {"Fin": "Finita", "Inf": "Infinitivo", "Ger": "Gerúndio", "Part": "Particípio"}),
    "Voice": ("Voz", {"Act": "Ativa", "Pass": "Passiva"}),
    "Aspect": ("Aspecto", {"Imp": "Imperfectivo", "Perf": "Perfectivo", "Prog": "Progressivo"}),
    "Degree": ("Grau", {"Pos": "Positivo", "Cmp": "Comparativo", "Sup": "Superlativo"}),
    "Definite": ("Definição", {"Def": "Definido", "Ind": "Indefinido"}),
    "PronType": ("Tipo de pronome", {"Prs": "Pessoal", "Rel": "Relativo", "Int": "Interrogativo", "Dem": "Demonstrativo", "Ind": "Indefinido", "Neg": "Negativo", "Art": "Artigo", "Tot": "Total"}),
    "NumType": ("Tipo de numeral", {"Card": "Cardinal", "Ord": "Ordinal", "Mult": "Multiplicativo", "Frac": "Fracionário"}),
    "Polarity": ("Polaridade", {"Neg": "Negativa", "Pos": "Positiva"}),
    "Poss": ("Possessivo", {"Yes": "Sim"}),
    "Reflex": ("Reflexivo", {"Yes": "Sim"}),
}
_RELACOES = {
    "coordenacao": "Coordenação com",
    "subordinacao": "Subordinação a",
    "associacao_sintatica": "Associação sintática com",
}
_ESTADOS = {
    "executada": "Identificação de orações concluída a partir das análises salvas.",
    "executada_com_pendencias": "A identificação de orações tem pendências. Consulte os avisos de cada oração.",
    "impedida": "As anotações disponíveis ainda não permitem identificar as orações.",
    "falhou": "A identificação de orações falhou nesta execução. Consulte a etapa Regras.",
    "desabilitada": "A identificação de orações está desabilitada nesta execução. Consulte a etapa Regras.",
}


def _morfologia(atributos):
    if atributos is None:
        return None
    resultado = []
    for campo, valor in sorted(atributos.items()):
        rotulo, valores = _ATRIBUTOS.get(campo, (campo, {}))
        resultado.append({
            "field": campo, "raw_value": valor, "label": rotulo,
            "value": ", ".join(valores.get(parte, parte) for parte in valor.split(",")),
        })
    return resultado


def _estado(anotacao, analise, regras):
    if anotacao is None:
        return "pendente", "Gere a análise morfológica para consultar as palavras e avançar para as orações.", "morfologia", []
    if analise is None:
        return "pendente", "Gere a análise sintática para identificar as relações entre as palavras.", "sintaxe", []
    if regras is None:
        return "pendente", "Aplique as regras linguísticas para identificar as orações deste texto.", "regras", []
    execucao = next((r for r in regras["execucoes_regras"] if r["regra_id"] == "oracao_predicado"), None)
    estado = execucao["estado"] if execucao else "desabilitada"
    pendencias = list(dict.fromkeys(p["motivo"] for p in execucao["pendencias"])) if execucao else []
    return estado, _ESTADOS[estado], "regras" if estado != "executada" else None, pendencias


def construir_visualizacao_segmentacao(segmentacao, anotacao=None, analise=None, regras=None, *, periodo_id=None):
    """Apresente a mesma cadeia validada, preservando IDs e os alcances salvos.

    A aplicação seleciona e valida a cadeia antes de chamar esta função. Dados
    parciais mostram as análises disponíveis e indicam a próxima etapa.
    """
    estado, mensagem, proxima, pendencias = _estado(anotacao, analise, regras)
    resultado = {
        "paragraphs": [], "clause_state": estado, "clause_message": mensagem,
        "next_stage": proxima, "has_morphology": anotacao is not None,
        "has_syntax": analise is not None, "clauses_total": 0, "rule_notes": pendencias,
    }
    if segmentacao is None:
        return resultado

    tokens = {t["id"]: t for t in segmentacao["tokens"]}
    ordem = {t["id"]: i for i, t in enumerate(segmentacao["tokens"])}
    morfologia = {t["token_id"]: t for t in anotacao["anotacoes_tokens"]} if anotacao else {}
    sintaxe = {t["token_id"]: t for t in analise["sintaxe"]} if analise else {}
    periodos = {p["id"]: p for p in segmentacao["periodos"]}
    original = segmentacao["preparacao"]["original"]["texto"]

    def linhas(identificadores):
        if anotacao is None and analise is None:
            return []
        selecionados = set(identificadores)
        linhas_resultado = []
        for ident in sorted(selecionados, key=ordem.__getitem__):
            token = tokens[ident]
            morf = morfologia.get(ident, {})
            sint = sintaxe.get(ident, {})
            cabeca_id = sint.get("cabeca_token_id")
            pos, dependencia = morf.get("pos"), sint.get("dependencia")
            linhas_resultado.append({
                "id": ident, "text": token["texto"], "lemma": morf.get("lema"),
                "pos": pos, "pos_label": _CLASSES.get(pos, pos),
                "morphology": _morfologia(morf.get("morfologia")),
                "dependency": dependencia, "dependency_label": _DEPENDENCIAS.get(dependencia, dependencia),
                "head_id": cabeca_id, "head_text": tokens[cabeca_id]["texto"] if cabeca_id else None,
                "head_is_self": cabeca_id == ident,
                "head_outside_clause": cabeca_id is not None and cabeca_id not in selecionados,
            })
        return linhas_resultado

    def fragmentos(trechos):
        return [original[t["original"]["inicio"]:t["original"]["fim"]] for t in trechos]

    por_periodo = defaultdict(list)
    for ocorrencia in regras["ocorrencias"] if regras else []:
        if ocorrencia["familia"] == "oracoes":
            por_periodo[ocorrencia["periodo_id"]].append(ocorrencia)

    referencias = {}
    for periodo in segmentacao["periodos"]:
        for numero, ocorrencia in enumerate(por_periodo[periodo["id"]], 1):
            referencias[ocorrencia["id"]] = {
                "anchor": f"seg-oracao-{periodo['ordem'] + 1}-{numero}", "number": numero,
            }

    for paragrafo in segmentacao["paragrafos"]:
        if periodo_id is not None and periodo_id not in paragrafo["periodos"]:
            continue
        posicao = paragrafo["original"]
        grupo = {"paragraph": paragrafo, "periods": [],
                 "original_text": original[posicao["inicio"]:posicao["fim"]]}
        for ident_periodo in paragrafo["periodos"]:
            if periodo_id is not None and ident_periodo != periodo_id:
                continue
            periodo = periodos[ident_periodo]
            exibidos, oracoes = set(), []
            for ocorrencia in por_periodo[ident_periodo]:
                alcance = ocorrencia["alcance"]
                evidencias = fragmentos(ocorrencia["evidencias"])
                trechos = fragmentos(alcance["trechos"]) if alcance is not None else []
                ids = alcance["token_ids"] if alcance is not None else list(dict.fromkeys(
                    ocorrencia["marcador_token_ids"] + ocorrencia["nucleo_token_ids"]))
                exibidos.update(ids)
                relacoes, vistos = [], set()
                for vinculo in ocorrencia["vinculos"]:
                    alvo, tipo = vinculo["alvo_id"], vinculo["tipo"]
                    if (vinculo["alvo_tipo"] == "ocorrencia" and alvo in referencias
                            and tipo in _RELACOES and (tipo, alvo) not in vistos):
                        relacoes.append({"label": _RELACOES[tipo], **referencias[alvo]})
                        vistos.add((tipo, alvo))
                notas = []
                for campo, rotulo in [("ambiguidades", "Ambiguidade"), ("alternativas", "Alternativa"),
                                       ("necessidade_contexto", "Necessidade de contexto")]:
                    notas.extend(f"{rotulo}: {texto}" for texto in ocorrencia[campo])
                oracoes.append({
                    "id": ocorrencia["id"], **referencias[ocorrencia["id"]],
                    "text": " […] ".join(trechos) if alcance is not None else "Extensão não determinada",
                    "fragments": trechos, "evidence_fragments": evidencias,
                    "scope_resolved": alcance is not None,
                    "nucleus_text": ", ".join(tokens[t]["texto"] for t in ocorrencia["nucleo_token_ids"]),
                    "tokens": linhas(ids), "relations": relacoes,
                    "explanation": ocorrencia["explicacao"], "notes": notas,
                })
            grupo["periods"].append({
                "period": periodo, "clauses": oracoes, "tokens": linhas(periodo["tokens"]),
                "original_text": original[periodo["original"]["inicio"]:periodo["original"]["fim"]],
                "unassigned_tokens": linhas([t for t in periodo["tokens"] if t not in exibidos]) if oracoes else [],
            })
            resultado["clauses_total"] += len(oracoes)
        resultado["paragraphs"].append(grupo)
    return resultado
