"""Indícios revisáveis sobre as previsões salvas, sem inferência ou correção.

Os avisos não integram a validade estrutural nem bloqueiam a vetorização.
Ausência de avisos não equivale a uma análise gramatical correta.
"""

from collections import defaultdict


def hierarquia_oracoes(oracoes, sintaxe):
    """Separe a posição no período dos vínculos locais entre orações.

    Coordenação compartilha o nível de subordinação. Receber um filho não
    transforma uma subordinada em principal do período. A projeção é derivada
    exclusivamente dos vínculos e dependências registrados, sem reescrevê-los.
    """
    por_id = {o["id"]: o for o in oracoes}
    pais, filhos, coordenadas = (defaultdict(set) for _ in range(3))
    for o in oracoes:
        for v in o["vinculos"]:
            alvo = v["alvo_id"]
            if v["alvo_tipo"] != "ocorrencia" or alvo not in por_id:
                continue
            if v["tipo"] == "subordinacao":
                pais[o["id"]].add(alvo)
                filhos[alvo].add(o["id"])
            elif v["tipo"] == "coordenacao":
                coordenadas[o["id"]].add(alvo)
                coordenadas[alvo].add(o["id"])
    componentes = {}
    for ident in por_id:
        if ident in componentes:
            continue
        grupo, pendentes = set(), [ident]
        while pendentes:
            atual = pendentes.pop()
            if atual not in grupo:
                grupo.add(atual)
                pendentes.extend(coordenadas[atual] - grupo)
        for atual in grupo:
            componentes[atual] = grupo

    def ordem(identificadores):
        return [ident for ident in por_id if ident in identificadores]

    resultado = {}
    for ident, o in por_id.items():
        grupo = componentes[ident]
        pais_grupo = set().union(*(pais[i] for i in grupo))
        filhos_grupo = set().union(*(filhos[i] for i in grupo)) - grupo
        raiz = any(sintaxe.get(t, {}).get("dependencia") == "ROOT"
                   for i in grupo for t in por_id[i]["nucleo_token_ids"])
        # Uma ligação subordinativa dentro do próprio nível coordenado ou uma
        # raiz subordinada contradizem os dados necessários à classificação.
        conflito = bool(pais_grupo & grupo) or (raiz and bool(pais_grupo))
        grupos_pais = {frozenset(componentes[p]) for p in pais_grupo - grupo}
        conflito = conflito or len(grupos_pais) > 1
        visitados, pendentes = set(grupo), list(pais_grupo - grupo)
        while pendentes:
            atual = pendentes.pop()
            if atual in grupo:
                conflito = True
                break
            if atual in visitados:
                continue
            visitados.add(atual)
            pendentes.extend(set().union(*(pais[i] for i in componentes[atual])) - visitados)
            # Detectar o retorno ao grupo antes de removê-lo pelos visitados.
            if set().union(*(pais[i] for i in componentes[atual])) & grupo:
                conflito = True
                break
        if conflito:
            posicao = "indeterminada"
        elif pais_grupo:
            posicao = "subordinada"
        elif raiz and len(grupo) > 1:
            posicao = "coordenada"
        elif raiz and filhos_grupo:
            posicao = "principal"
        elif raiz:
            posicao = "absoluta" if len(oracoes) == 1 else "independente"
        else:
            posicao = "indeterminada"
        resultado[ident] = {
            "posicao": posicao, "coordenada": len(grupo) > 1,
            "subordinada_a": ordem(pais_grupo - grupo),
            "subordinacao_compartilhada": not bool(pais[ident]) and bool(pais_grupo - grupo),
            "rege": ordem(filhos[ident]), "coordenada_com": ordem(grupo - {ident}),
            "conflito": conflito,
        }
    return resultado


def diagnosticar_gramatica(segmentacao, anotacao=None, analise=None, regras=None):
    """Retorne avisos reproduzíveis da cadeia informada, inclusive histórica.

    A aplicação valida previamente os contratos. Dados parciais são aceitos:
    nenhum modelo é executado e nenhum registro recebido é modificado.
    """
    resultado = {
        "schema_version": "lingua.diagnosticos_gramaticais.v1",
        "origem_ids": {"segmentacao_id": segmentacao.get("segmentacao_id") if segmentacao else None,
                       "anotacao_id": anotacao.get("anotacao_id") if anotacao else None,
                       "analise_id": analise.get("analise_id") if analise else None,
                       "execucao_id": regras.get("execucao_id") if regras else None},
        "estado": "pendente", "avisos": [],
        "limitacao": "Avisos heurísticos não corrigem previsões nem medem acurácia. A ausência de avisos não certifica correção gramatical.",
    }
    if segmentacao is None or anotacao is None or analise is None:
        return resultado
    tokens = {t["id"]: t for t in segmentacao["tokens"]}
    morf = {t["token_id"]: t for t in anotacao["anotacoes_tokens"]}
    sintaxe = {t["token_id"]: t for t in analise["sintaxe"]}
    por_periodo = defaultdict(list)
    for o in regras["ocorrencias"] if regras else []:
        if o["familia"] == "oracoes":
            por_periodo[o["periodo_id"]].append(o)

    def avisar(codigo, mensagem, periodo, ids, oracoes, evidencia, limitacao):
        resultado["avisos"].append({
            "codigo": codigo, "mensagem": mensagem, "periodo_id": periodo["id"],
            "token_ids": list(dict.fromkeys(ids)), "oracao_ids": list(dict.fromkeys(oracoes)),
            "evidencia": evidencia, "limitacao": limitacao,
        })

    def retrato(ident):
        m, s = morf.get(ident, {}), sintaxe.get(ident, {})
        return {"token_id": ident, "texto": tokens[ident]["texto"],
                "pos": m.get("pos"), "morfologia": m.get("morfologia"),
                "dependencia": s.get("dependencia"), "cabeca_token_id": s.get("cabeca_token_id")}

    for periodo in segmentacao["periodos"]:
        ids = periodo["tokens"]
        oracoes = por_periodo[periodo["id"]]
        raizes = [i for i in ids if sintaxe.get(i, {}).get("dependencia") == "ROOT"]
        finitos = [i for i in ids if morf.get(i, {}).get("pos") in {"VERB", "AUX"}
                   and "Fin" in (morf.get(i, {}).get("morfologia") or {}).get("VerbForm", "").split(",")]
        for raiz in raizes:
            copula = any(sintaxe.get(i, {}).get("cabeca_token_id") == raiz
                         and sintaxe.get(i, {}).get("dependencia", "").split(":")[0] == "cop" for i in ids)
            if finitos and morf.get(raiz, {}).get("pos") not in {"VERB", "AUX"} and not copula:
                avisar("raiz_nao_predicativa_com_verbo_finito",
                       "A raiz prevista não é verbal nem tem cópula, mas o período contém verbo finito. Revise o predicado e seus vínculos.",
                       periodo, [raiz] + finitos, [], [retrato(i) for i in [raiz] + finitos],
                       "Raízes nominais podem ser legítimas; a combinação é um indício para revisão, não uma rejeição do período.")

        for ident in ids:
            m, s = morf.get(ident, {}), sintaxe.get(ident, {})
            if (tokens[ident]["texto"].casefold() in {"embora", "caso", "se"}
                    and m.get("pos") == "SCONJ" and s.get("dependencia") in {"mark", "case"}
                    and s.get("cabeca_token_id") in raizes):
                raiz = s["cabeca_token_id"]
                associados = [o["id"] for o in oracoes if raiz in o["nucleo_token_ids"]]
                avisar("marcador_subordinativo_em_raiz",
                       "Um possível marcador concessivo ou condicional foi ligado à raiz do período. A posição da oração requer revisão.",
                       periodo, [ident, raiz], associados, [retrato(ident), retrato(raiz)],
                       "O marcador não identifica sozinho a principal nem autoriza corrigir a árvore; usos citados e outras leituras dependem do contexto.")

        if regras is None:
            continue
        cobertos = {i for o in oracoes if o["alcance"] is not None for i in o["alcance"]["token_ids"]}
        fora = [i for i in ids if i not in cobertos and morf.get(i, {}).get("pos")
                not in {None, "PUNCT", "SPACE", "SYM"}]
        if fora and (oracoes or finitos):
            avisar("tokens_relevantes_fora_das_oracoes",
                   "Há palavras ou marcadores fora dos alcances oracionais resolvidos. Revise as fronteiras e os vínculos antes de interpretar.",
                   periodo, fora, [], [retrato(i) for i in fora],
                   "Uma lacuna pode decorrer de alcance ainda não resolvido; as palavras não são atribuídas automaticamente à oração mais próxima.")
        for ident, hierarquia in hierarquia_oracoes(oracoes, sintaxe).items():
            if hierarquia["conflito"]:
                o = next(o for o in oracoes if o["id"] == ident)
                avisar("hierarquia_oracional_conflitante",
                       "Os vínculos previstos não sustentam uma hierarquia oracional única. A classificação requer revisão.",
                       periodo, o["nucleo_token_ids"], [ident],
                       {"hierarquia": hierarquia, "vinculos": o["vinculos"]},
                       "A classificação exibida é uma projeção dos vínculos salvos; o aviso não altera a análise de origem.")
    resultado["estado"] = "com_avisos" if resultado["avisos"] else "sem_avisos_detectados"
    if regras is None:
        resultado["etapas_pendentes"] = ["regras"]
    return resultado
