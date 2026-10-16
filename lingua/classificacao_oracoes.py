"""Subtipos oracionais derivados das previsões salvas, sem nova inferência.

Este módulo não corrige a árvore nem altera o histórico. Um subtipo só é
proposto quando dependência, função do marcador e hierarquia são compatíveis.
Os rótulos continuam sendo hipóteses revisáveis do processamento automático.
"""

from collections import defaultdict
from copy import deepcopy

from diagnosticos_gramaticais import diagnosticar_gramatica, hierarquia_oracoes


_RELATIVOS = {"que", "quem", "qual", "quais", "cujo", "cuja", "cujos", "cujas", "onde"}
_NOMINAIS = {"NOUN", "PROPN", "PRON"}
_ADVERBIAIS = {
    ("embora",): "concessiva", ("conquanto",): "concessiva",
    ("ainda", "que"): "concessiva", ("mesmo", "que"): "concessiva",
    ("caso",): "condicional", ("quando",): "temporal",
    ("assim", "que"): "temporal", ("logo", "que"): "temporal",
    ("para", "que"): "final", ("a", "fim", "de", "que"): "final",
}
_FINAIS = {".", "!", "?", "…", "..."}
_SENTIDOS = {
    "concessiva": "uma concessão: uma circunstância que não impede o acontecimento ao qual se liga",
    "condicional": "uma condição para o acontecimento ao qual se liga",
    "temporal": "uma circunstância de tempo relacionada ao acontecimento",
    "final": "uma finalidade para o acontecimento ao qual se liga",
}


def classificar_oracoes(segmentacao, anotacao=None, analise=None, regras=None):
    """Retorne classificações auditáveis da cadeia previamente validada.

    A entrada pode ser parcial. Evidências mantêm IDs e coordenadas das fontes;
    a saída inteira é JSON e não compartilha objetos mutáveis com as entradas.
    Substantivas e relações semanticamente ambíguas permanecem sem subtipo.
    """
    resultado = {
        "schema_version": "lingua.classificacao_oracoes.v1",
        "origem_ids": {
            "segmentacao_id": segmentacao.get("segmentacao_id") if segmentacao else None,
            "anotacao_id": anotacao.get("anotacao_id") if anotacao else None,
            "analise_id": analise.get("analise_id") if analise else None,
            "execucao_id": regras.get("execucao_id") if regras else None,
        },
        "estado": "pendente", "oracoes": [],
        "limitacoes": [
            "A classificação deriva das dependências e formas registradas; não certifica acerto gramatical nem corrige a fonte.",
            "Os subtipos cobertos são relativas adjetivas e algumas adverbiais com marcador explícito. Outros casos permanecem indeterminados.",
            "A distinção restritiva/explicativa considera a delimitação escrita; contexto e intenção podem exigir revisão.",
            "Antecedentes e vínculos apresentados são os previstos na árvore, sem substituir a leitura humana.",
        ],
    }
    if any(fonte is None for fonte in (segmentacao, anotacao, analise, regras)):
        return resultado
    tokens = {t["id"]: t for t in segmentacao["tokens"]}
    ordem = {t["id"]: i for i, t in enumerate(segmentacao["tokens"])}
    morf = {t["token_id"]: t for t in anotacao["anotacoes_tokens"]}
    sintaxe = {t["token_id"]: t for t in analise["sintaxe"]}
    por_periodo = defaultdict(list)
    for o in regras["ocorrencias"]:
        if o["familia"] == "oracoes":
            por_periodo[o["periodo_id"]].append(o)
    diagnosticos = diagnosticar_gramatica(segmentacao, anotacao, analise, regras)
    conflitos = {o for a in diagnosticos["avisos"]
                 if a["codigo"] in {"marcador_subordinativo_em_raiz", "hierarquia_oracional_conflitante"}
                 for o in a["oracao_ids"]}
    periodos_suspeitos = {a["periodo_id"] for a in diagnosticos["avisos"]
                         if a["codigo"] == "raiz_nao_predicativa_com_verbo_finito"}

    def retrato(ident):
        t, m, s = tokens[ident], morf.get(ident, {}), sintaxe.get(ident, {})
        return deepcopy({
            "token_id": ident, "texto": t["texto"], "pos": m.get("pos"),
            "morfologia": m.get("morfologia"), "dependencia": s.get("dependencia"),
            "cabeca_token_id": s.get("cabeca_token_id"),
            "original": t["original"], "trabalho": t["trabalho"],
        })

    def evidenciar(item, identificadores):
        existentes = {e["token_id"] for e in item["evidencias"]}
        item["evidencias"].extend(retrato(i) for i in sorted(set(identificadores) - existentes, key=ordem.__getitem__)
                                  if i in tokens)
        item["evidencias"].sort(key=lambda e: ordem[e["token_id"]])

    def pertence(ident, componentes, nucleos_periodo):
        """Um marcador não atravessa o predicado de outra oração."""
        vistos = set()
        while ident in sintaxe and ident not in vistos:
            if ident in componentes:
                return True
            if ident in nucleos_periodo:
                return False
            vistos.add(ident)
            pai = sintaxe[ident].get("cabeca_token_id")
            if pai == ident:
                return False
            ident = pai
        return False

    def descendentes(raiz, ids):
        conjunto = {raiz}
        filhos = defaultdict(list)
        for ident in ids:
            pai = sintaxe.get(ident, {}).get("cabeca_token_id")
            if pai is not None and pai != ident:
                filhos[pai].append(ident)
        fila = [raiz]
        while fila:
            for filho in filhos[fila.pop()]:
                if filho not in conjunto:
                    conjunto.add(filho)
                    fila.append(filho)
        return conjunto

    def pontuacao_relativa(raiz, relativos, periodo):
        # O limite inclui os descendentes subordinados da relativa. Assim uma
        # vírgula interna, antes de uma concessiva, não cria uma explicativa.
        ids = periodo["tokens"]
        arvore = descendentes(raiz, ids)
        conteudo = [i for i in ids if i in arvore and morf.get(i, {}).get("pos") != "PUNCT"]
        if not conteudo:
            return None, [], "Não foi possível delimitar o conteúdo da relativa."
        inicio = min(ordem[i] for i in conteudo)
        anteriores = [i for i in ids if ordem[i] < inicio]
        abertura = anteriores[-1] if anteriores else None
        if abertura is None or tokens[abertura]["texto"] not in {",", "—", "–", "("}:
            return "restritiva", relativos, "A relativa modifica o antecedente nominal sem delimitador de abertura que a isole do restante do período."
        fim = max(ordem[i] for i in conteudo)
        depois = [i for i in ids if ordem[i] > fim]
        delimitadores = []
        for ident in depois:
            forma = tokens[ident]["texto"]
            if forma in {",", "—", "–", ")"} | _FINAIS:
                delimitadores.append(ident)
                break
            if morf.get(ident, {}).get("pos") != "PUNCT":
                break
        permitido = {",": {","} | _FINAIS, "—": {"—"}, "–": {"–"}, "(": {")"}}
        if delimitadores and tokens[delimitadores[0]]["texto"] in permitido[tokens[abertura]["texto"]]:
            return "explicativa", relativos + [abertura] + delimitadores, "A relativa retoma o antecedente nominal e está isolada por delimitadores externos ao seu conteúdo."
        return None, relativos + [abertura], "Há delimitador de abertura, mas o fechamento da relativa não ficou sustentado na árvore e na pontuação."

    for periodo in segmentacao["periodos"]:
        oracoes = por_periodo[periodo["id"]]
        hierarquias = hierarquia_oracoes(oracoes, sintaxe)
        nucleos_periodo = {i for o in oracoes for i in o["nucleo_token_ids"]}
        for o in oracoes:
            hierarquia = hierarquias[o["id"]]
            componentes = set(o["nucleo_token_ids"])
            item = {
                "oracao_id": o["id"], "periodo_id": periodo["id"],
                "classe": None, "subtipo": None, "rotulo": None,
                "estado": "indeterminada", "explicacao": "As evidências disponíveis não sustentam um subtipo coberto pelo classificador.",
                "evidencias": [], "referencia": None, "marcador_token_ids": [],
                "nucleo_token_ids": list(o["nucleo_token_ids"]), "hierarquia": deepcopy(hierarquia),
            }
            evidenciar(item, componentes)
            resultado["oracoes"].append(item)
            if (o["id"] in conflitos or hierarquia["conflito"]
                    or periodo["id"] in periodos_suspeitos):
                item.update(estado="requer_revisao", explicacao="A árvore ou a hierarquia contém um conflito relevante; o subtipo não é afirmado antes da revisão.")
                continue
            if hierarquia["posicao"] != "subordinada":
                item.update(estado="nao_aplicavel" if hierarquia["posicao"] != "indeterminada" else "indeterminada",
                            explicacao="Este agrupamento não está registrado como subordinado com hierarquia determinada.")
                continue
            superiores = [i for i in componentes if sintaxe.get(i, {}).get("cabeca_token_id") not in componentes]
            if len(superiores) != 1:
                item.update(estado="requer_revisao", explicacao="O agrupamento não tem um único núcleo superior ligado à oração de referência.")
                continue
            superior = superiores[0]
            dep = sintaxe[superior].get("dependencia", "")
            pai = sintaxe[superior].get("cabeca_token_id")
            if pai in tokens:
                item["referencia"] = retrato(pai)
                evidenciar(item, [pai])
            proprios = [i for i in periodo["tokens"] if pertence(i, componentes, nucleos_periodo)]
            relativos = [i for i in proprios
                         if tokens[i]["texto"].casefold() in _RELATIVOS
                         and morf.get(i, {}).get("pos") in {"PRON", "DET", "ADV"}
                         and sintaxe.get(i, {}).get("dependencia", "").split(":")[0] not in {"mark", "case", "punct"}
                         and ("Rel" in (morf.get(i, {}).get("morfologia") or {}).get("PronType", "").split(",")
                              or dep == "acl:relcl")]
            if dep in {"acl", "acl:relcl"} and relativos and morf.get(pai, {}).get("pos") in _NOMINAIS:
                item["classe"] = "adjetiva"
                item["marcador_token_ids"] = relativos
                subtipo, usados, explicacao = pontuacao_relativa(superior, relativos, periodo)
                evidenciar(item, usados)
                item["explicacao"] = explicacao + " O antecedente exibido é o registrado na dependência do núcleo da relativa."
                if subtipo:
                    item.update(estado="classificada", subtipo=subtipo,
                                rotulo="Oração subordinada adjetiva " + subtipo)
                continue
            marcadores = [i for i in proprios if morf.get(i, {}).get("pos") == "SCONJ"
                          and sintaxe.get(i, {}).get("dependencia") == "mark"
                          and sintaxe.get(i, {}).get("cabeca_token_id") in componentes | {
                              i for i in proprios if sintaxe.get(i, {}).get("dependencia", "").split(":")[0] in {"aux", "cop"}
                          }]
            item["marcador_token_ids"] = marcadores
            evidenciar(item, marcadores)
            if dep != "advcl":
                item["explicacao"] = "A relação prevista não é uma relativa nominal nem uma adverbial sustentada; o marcador isolado não determina o subtipo."
                continue
            formas = [tokens[i]["texto"].casefold() for i in periodo["tokens"]]
            candidatos = []
            ids = periodo["tokens"]
            for expressao, subtipo in _ADVERBIAIS.items():
                for pos in range(len(ids) - len(expressao) + 1):
                    grupo = ids[pos:pos + len(expressao)]
                    if (tuple(formas[pos:pos + len(expressao)]) == expressao
                            and set(grupo).issubset(proprios) and set(grupo) & set(marcadores)):
                        candidatos.append((subtipo, grupo))
            tipos = {subtipo for subtipo, _ in candidatos}
            if len(tipos) == 1:
                subtipo = next(iter(tipos))
                usados = sorted({i for _, grupo in candidatos for i in grupo}, key=ordem.__getitem__)
                item.update(classe="adverbial", subtipo=subtipo, estado="classificada",
                            rotulo="Oração subordinada adverbial " + subtipo,
                            marcador_token_ids=usados,
                            explicacao="O marcador " +
                            " + ".join(repr(tokens[i]["texto"]) for i in usados) +
                            " introduz " + _SENTIDOS[subtipo] +
                            ". Os vínculos previstos sustentam esta classificação. A oração de referência pode também ser subordinada.")
                evidenciar(item, usados)
            elif len(tipos) > 1:
                item["explicacao"] = "Os marcadores disponíveis apontam para subtipos diferentes; a relação requer revisão contextual."
            elif marcadores:
                item["explicacao"] = "A relação advcl é adverbial, mas o marcador não basta para afirmar um subtipo coberto: formas como 'se', 'como' e 'desde que' admitem leituras diferentes."
    resultado["estado"] = "disponivel"
    return resultado
