"""Detectores determinísticos sobre os registros da etapa 06.

Não importam spaCy, não retokenizam e não alteram a fonte. As propostas usam
somente IDs; o mecanismo público constrói os recortes e o mapa ao original.
"""
from collections import defaultdict

from catalogo_regras import REGRA_IDS


_VERBAIS = {"VERB", "AUX"}
_AUXILIARES = {"aux", "cop"}
_SUBORDINADAS = {"xcomp", "ccomp", "advcl", "acl", "csubj"}
_ELOCUCAO = {"dizer", "afirmar", "contar", "declarar", "responder", "perguntar", "relatar", "escrever"}
_NEGATIVOS = {"não", "nunca", "nem"}
_TEMPORAIS = {"ontem", "hoje", "amanhã"}
_MODAIS = {"poder": "possibilidade_indeterminada", "dever": "obrigacao",
           "precisar": "necessidade", "necessitar": "necessidade",
           "conseguir": "capacidade", "saber": "capacidade",
           "querer": "desejo", "desejar": "desejo", "duvidar": "duvida"}


def _base(dependencia):
    return dependencia.lower().split(":", 1)[0]


def _vinculo(tipo, identificador, alvo_tipo="token"):
    return {"tipo": tipo, "alvo_tipo": alvo_tipo, "alvo_id": identificador}


def _proposta(familia, categoria, marcador, nucleo, alcance, explicacao,
              *, vinculos=None, ambiguidades=None, alternativas=None, contexto=None):
    return {"familia": familia, "categoria": categoria,
            "marcador_token_ids": list(marcador), "nucleo_token_ids": list(nucleo),
            "alcance_token_ids": None if alcance is None else list(alcance),
            "vinculos": list(vinculos or []), "explicacao": explicacao,
            "ambiguidades": list(ambiguidades or []), "alternativas": list(alternativas or []),
            "necessidade_contexto": list(contexto or [])}


class _Indice:
    """Índices compartilhados, com ownership de oração e subárvores em ordem DFS."""

    def __init__(self, analise):
        self.analise = analise
        self.segmentacao = analise["anotacao"]["segmentacao"]
        anotacoes = {a["token_id"]: a for a in analise["anotacao"]["anotacoes_tokens"]}
        sintaxe = {s["token_id"]: s for s in analise["sintaxe"]}
        self.lista = [dict(token, **{
            "lema": anotacoes[token["id"]]["lema"],
            "pos": anotacoes[token["id"]]["pos"],
            "morfologia": anotacoes[token["id"]]["morfologia"],
            "dependencia": sintaxe[token["id"]]["dependencia"],
            "cabeca_token_id": sintaxe[token["id"]]["cabeca_token_id"],
        }) for token in self.segmentacao["tokens"]]
        self.tokens = {t["id"]: t for t in self.lista}
        self.posicao = {t["id"]: i for i, t in enumerate(self.lista)}
        self.filhos = defaultdict(list)
        self.periodos = defaultdict(list)
        self.dependencia = {}
        for token in self.lista:
            ident = token["id"]
            self.periodos[token["periodo_id"]].append(ident)
            self.dependencia[ident] = _base(token["dependencia"])
            if token["cabeca_token_id"] != ident:
                self.filhos[token["cabeca_token_id"]].append(ident)
        self.nucleos = {t["id"] for t in self.lista if t["pos"] in _VERBAIS
                        and self.dependencia[t["id"]] not in _AUXILIARES}
        self.nucleos.update(t["cabeca_token_id"] for t in self.lista
                            if self.dependencia[t["id"]] == "cop")
        self.pertence = {}
        self.proprios = defaultdict(list)
        self.entrada, self.saida, self.dfs = {}, {}, []
        for token in self.lista:
            if token["cabeca_token_id"] != token["id"]:
                continue
            pilha = [(token["id"], None, False)]
            while pilha:
                ident, nucleo, fechar = pilha.pop()
                if fechar:
                    self.saida[ident] = len(self.dfs)
                    continue
                if ident in self.nucleos:
                    nucleo = ident
                self.pertence[ident] = nucleo
                if nucleo is not None:
                    self.proprios[nucleo].append(ident)
                self.entrada[ident] = len(self.dfs)
                self.dfs.append(ident)
                pilha.append((ident, nucleo, True))
                pilha.extend((filho, nucleo, False) for filho in reversed(self.filhos[ident]))
        for nucleo in self.proprios:
            self.proprios[nucleo].sort(key=self.posicao.__getitem__)
        self.pos_ausentes_periodo = {periodo: [i for i in ids if self.tokens[i]["pos"] is None]
                                     for periodo, ids in self.periodos.items()}
        self.citacao = {}
        self._indexar_aspas()
        self._correlativas = None
        self._supressao_correlativa = None

    def ordenar(self, ids):
        return sorted(set(ids), key=self.posicao.__getitem__)

    def descendentes(self, ident):
        return self.ordenar(self.dfs[self.entrada[ident]:self.saida[ident]])

    def pai_oracional(self, ident):
        cabeca = self.tokens[ident]["cabeca_token_id"]
        return None if cabeca == ident else self.pertence.get(cabeca)

    def _indexar_aspas(self):
        # Somente marcas inteiras, pareadas. Apóstrofos dentro de palavras não
        # são aspas; marcas não fechadas não recebem interpretação segura.
        pares = {"“": "”", "«": "»", "‘": "’", '"': '"', "'": "'"}
        fechamentos = set(pares.values())
        pilha = []
        for token in self.lista:
            texto, ident = token["texto"], token["id"]
            if pilha and texto == pilha[-1]["fechamento"]:
                atual = pilha.pop()
                atual["fechado"] = True
                atual["fim"] = self.posicao[ident]
                continue
            if texto in pares:
                atual = {"inicio": self.posicao[ident], "fim": None,
                         "fechamento": pares[texto], "fechado": False,
                         "aninhada": bool(pilha), "marcador_id": ident}
                pilha.append(atual)
                continue
            if pilha:
                self.citacao[ident] = pilha[-1]
            elif texto in fechamentos:
                # Sem abertura reconhecida não há região citada inventada.
                continue

    def contexto_citacao(self, ident):
        return self.citacao.get(ident)

    def alcance(self, nucleo, marcador=None, *, constituinte=False):
        if nucleo is None:
            return None
        alcance = self.descendentes(nucleo) if constituinte else self.proprios.get(nucleo, [])
        if not alcance:
            return None
        if marcador is not None:
            citacao = self.contexto_citacao(marcador)
            if citacao is not None:
                if not citacao["fechado"] or self.contexto_citacao(nucleo) is not citacao:
                    return None
                alcance = [i for i in alcance if self.contexto_citacao(i) is citacao]
        return alcance or None

    def anotacao_ausente(self, ids, campos, motivo):
        faltas = defaultdict(list)
        for ident in ids:
            token = self.tokens[ident]
            for campo in campos:
                if token.get(campo) is None:
                    faltas[campo].append(ident)
        return [{"token_ids": self.ordenar(identificadores), "campos": [campo], "motivo": motivo}
                for campo, identificadores in faltas.items()]

    def contexto_enunciativo(self, marcador, nucleo):
        vinculos, ambiguidades, contexto = [], [], []
        citacao = self.contexto_citacao(marcador)
        if citacao is not None:
            vinculos.append(_vinculo("delimitador_citacao", citacao["marcador_id"]))
            if citacao["fechado"]:
                ambiguidades.append("Marcador em trecho citado; não atribuir seu conteúdo automaticamente ao narrador.")
                if nucleo is None or self.contexto_citacao(nucleo) is not citacao:
                    contexto.append("Distinguir menção metalinguística do marcador e conteúdo de fala citada; alcance não resolvido.")
            else:
                ambiguidades.append("Aspas sem fechamento reconhecido; delimitação da citação indeterminada.")
                contexto.append("Resolver os limites e a atribuição da expressão citada.")
            if citacao["aninhada"]:
                contexto.append("Resolver a atribuição de aspas aninhadas, fora da cobertura inicial.")
        if nucleo is not None:
            atual = nucleo
            # Segue só a cadeia de agrupamentos, nunca a árvore inteira.
            while atual is not None:
                pai = self.pai_oracional(atual)
                if pai is None:
                    break
                if self.dependencia[atual] in {"ccomp", "parataxis"} and self.tokens[pai]["lema"] in _ELOCUCAO:
                    vinculos.append(_vinculo("discurso_relatado", pai))
                    ambiguidades.append("Conteúdo em complemento de verbo de elocução; atribuição ao narrador não estabelecida.")
                    contexto.append("Identificar a fonte e a atribuição do discurso relatado na etapa 08.")
                    break
                atual = pai
        return vinculos, ambiguidades, contexto

    def correlativas(self):
        if self._correlativas is not None:
            return self._correlativas
        resultados, usados = [], set()
        for ids in self.periodos.values():
            for i, ident in enumerate(ids[:-1]):
                nao, so = self.tokens[ident], self.tokens[ids[i + 1]]
                if nao["texto"].lower() != "não" or so["texto"].lower() not in {"só", "somente", "apenas"}:
                    continue
                if nao["pos"] not in {"ADV", "PART"} or so["pos"] != "ADV":
                    continue
                if self.dependencia[ident] not in {"advmod", "neg"}:
                    continue
                primeiro = self.pertence.get(ident)
                if primeiro is None:
                    continue
                for j in range(i + 2, len(ids) - 1):
                    mas_id, tambem_id = ids[j], ids[j + 1]
                    mas, tambem = self.tokens[mas_id], self.tokens[tambem_id]
                    if mas_id in usados:
                        continue
                    if mas["texto"].lower() != "mas" or tambem["texto"].lower() != "também":
                        continue
                    if mas["pos"] != "CCONJ" or self.dependencia[mas_id] != "cc" or tambem["pos"] != "ADV":
                        continue
                    segundo = self.pertence.get(mas_id)
                    if segundo is None or self.pertence.get(tambem_id) != segundo:
                        continue
                    if primeiro != segundo and not (
                        self.dependencia[segundo] == "conj" and self.pai_oracional(segundo) == primeiro
                        or self.dependencia[primeiro] == "conj" and self.pai_oracional(primeiro) == segundo
                    ):
                        continue
                    if self.contexto_citacao(ident) is not self.contexto_citacao(mas_id):
                        continue
                    alcance1, alcance2 = self.alcance(primeiro, ident), self.alcance(segundo, mas_id)
                    if alcance1 is None or alcance2 is None:
                        continue
                    marcador = [ident, so["id"], mas_id, tambem_id]
                    resultados.append((marcador, self.ordenar([primeiro, segundo]),
                                       self.ordenar(alcance1 + alcance2)))
                    usados.add(mas_id)
                    break
        self._correlativas = resultados
        return resultados


def construir_contexto(analise):
    """Construa os índices uma única vez por execução (após validar a etapa 06)."""
    return _Indice(analise)


def _supressos(indice, habilitadas):
    if "negacao_correlativa" not in habilitadas:
        return set()
    if indice._supressao_correlativa is None:
        indice._supressao_correlativa = frozenset(ident for marcador, _, _ in indice.correlativas()
                                                for ident in (marcador[0], marcador[2]))
    return indice._supressao_correlativa


def _oracoes(indice, habilitadas):
    ocorrencias = []
    pendencias = indice.anotacao_ausente(
        [t["id"] for t in indice.lista], ["pos"],
        "A identificação de núcleos verbais depende da classe gramatical recebida.")
    for ident in indice.ordenar(indice.nucleos):
        token = indice.tokens[ident]
        auxiliares = [filho for filho in indice.filhos[ident]
                      if indice.dependencia[filho] in _AUXILIARES]
        marcador = indice.ordenar([ident] + auxiliares)
        pai = indice.pai_oracional(ident)
        dep = indice.dependencia[ident]
        vinculos = [_vinculo("auxiliar" if indice.dependencia[a] == "aux" else "copula", a)
                    for a in auxiliares]
        if pai is not None:
            tipo = "coordenacao" if dep == "conj" else "subordinacao" if dep in _SUBORDINADAS else "associacao_sintatica"
            vinculos.append(_vinculo(tipo, pai))
        alcance = indice.alcance(ident)
        origem = "predicado copular" if any(indice.dependencia[a] == "cop" for a in auxiliares) else "núcleo verbal"
        explicacao = f"{token['texto']!r} é {origem} na anotação recebida (POS={token['pos']}, dependência={token['dependencia']})."
        if auxiliares:
            explicacao += " Auxiliares/cópulas dependentes integram este agrupamento."
        if pai is not None:
            explicacao += f" A dependência {token['dependencia']} liga este núcleo a outro agrupamento; não é fundido automaticamente."
        ocorrencias.append(_proposta("oracoes", "oracao", marcador, [ident], alcance, explicacao, vinculos=vinculos))
    return ocorrencias, pendencias


def _correlativa(indice, habilitadas):
    ocorrencias = []
    pendencias = []
    for token in indice.lista:
        if token["texto"].lower() in {"não", "só", "somente", "apenas", "mas", "também"}:
            pendencias.extend(indice.anotacao_ausente([token["id"]], ["pos"],
                             "A construção correlativa exige classes adverbial e coordenativa."))
    for marcador, nucleo, alcance in indice.correlativas():
        vinculos, ambiguidades, contexto = indice.contexto_enunciativo(marcador[0], nucleo[0])
        vinculos += [_vinculo("correlato", ident) for ident in nucleo]
        ocorrencias.append(_proposta(
            "negacao", "adicao_correlativa", marcador, nucleo, alcance,
            "Os pares não+só/somente/apenas e mas+também estão completos no mesmo período e ligados a predicados correlacionados. A construção propõe adição, sem classificar seu não como negação simples ou seu mas como contraste.",
            vinculos=vinculos, ambiguidades=ambiguidades, contexto=contexto))
    return ocorrencias, pendencias


def _negador(indice, ident, habilitadas):
    token = indice.tokens[ident]
    palavra, dep = token["texto"].lower(), indice.dependencia[ident]
    if palavra not in _NEGATIVOS or ident in _supressos(indice, habilitadas):
        return None
    citacao = indice.contexto_citacao(ident)
    classes = {"ADV"} if palavra == "nunca" else {"ADV", "PART"}
    permitido = token["pos"] in classes and dep in {"advmod", "neg", "root"}
    if palavra == "nem":
        permitido = token["pos"] in {"ADV", "PART", "CCONJ"} and dep in {"advmod", "neg", "cc", "root"}
    if not permitido and citacao is None:
        return None
    cabeca = token["cabeca_token_id"]
    nucleo = indice.pertence.get(ident)
    constituinte = False
    # Um marcador ligado diretamente a nome/adjetivo pode negar esse
    # constituinte; não promovemos essa negação à oração inteira.
    if permitido and cabeca != ident and indice.tokens[cabeca]["pos"] is None:
        nucleo = None
    elif permitido and cabeca != ident and cabeca not in indice.nucleos and indice.dependencia[cabeca] not in _AUXILIARES:
        nucleo, constituinte = cabeca, True
    alcance = indice.alcance(nucleo, ident, constituinte=constituinte) if permitido else None
    if citacao is not None and (nucleo is None or indice.contexto_citacao(nucleo) is not citacao):
        nucleo, alcance = None, None
    vinculos, ambiguidades, contexto = indice.contexto_enunciativo(ident, nucleo)
    if constituinte:
        ambiguidades.append("Negação ligada a constituinte não verbal; não se afirma negação de toda a oração.")
    if alcance is None:
        contexto.append("Determinar o alcance do marcador; não há predicado/constituinte suficiente nas anotações disponíveis.")
    if nucleo is not None:
        vinculos.append(_vinculo("modifica", nucleo))
    categoria = {"não": "negacao_simples", "nunca": "negacao_temporal", "nem": "negacao_aditiva"}[palavra]
    explicacao = (f"O marcador literal {token['texto']!r} tem POS={token['pos']} e dependência {token['dependencia']}; "
                  "o alcance proposto segue sua ligação sintática, sem inferir polaridade por contagem.")
    if not permitido:
        explicacao = f"O marcador literal {token['texto']!r} foi encontrado dentro de aspas; sua menção é registrada sem afirmar negação do narrador."
    return _proposta("negacao", categoria, [ident], [] if nucleo is None else [nucleo], alcance,
                     explicacao, vinculos=vinculos, ambiguidades=ambiguidades, contexto=contexto)


def _negacao(indice, habilitadas, palavra):
    ocorrencias, pendencias = [], []
    for token in indice.lista:
        if token["texto"].lower() != palavra:
            continue
        ident = token["id"]
        pendencias.extend(indice.anotacao_ausente([ident], ["pos"], "A função do marcador negativo requer POS."))
        proposta = _negador(indice, ident, habilitadas)
        if proposta is not None:
            ocorrencias.append(proposta)
    return ocorrencias, pendencias


def _multiplos(indice, habilitadas):
    grupos, pendencias = defaultdict(list), []
    for token in indice.lista:
        if token["texto"].lower() not in _NEGATIVOS:
            continue
        ident = token["id"]
        pendencias.extend(indice.anotacao_ausente([ident], ["pos"], "O agrupamento depende da função de cada negador."))
        proposta = _negador(indice, ident, habilitadas)
        if proposta is None or not proposta["nucleo_token_ids"]:
            continue
        citacao = indice.contexto_citacao(ident)
        chave = (proposta["nucleo_token_ids"][0], None if citacao is None else citacao["marcador_id"])
        grupos[chave].append(proposta)
    ocorrencias = []
    for (nucleo, _), propostas in grupos.items():
        if len(propostas) < 2:
            continue
        marcadores = indice.ordenar(p["marcador_token_ids"][0] for p in propostas)
        primeira = propostas[0]
        ocorrencias.append(_proposta(
            "negacao", "multiplos_marcadores", marcadores, [nucleo], primeira["alcance_token_ids"],
            "Dois ou mais marcadores negativos estão ligados ao mesmo núcleo na mesma região enunciativa. A contagem não determina uma polaridade global.",
            vinculos=primeira["vinculos"], ambiguidades=primeira["ambiguidades"], contexto=primeira["necessidade_contexto"]))
    return ocorrencias, pendencias


def _tempo_flexao(indice, habilitadas):
    ocorrencias, pendencias = [], []
    for token in indice.lista:
        ident = token["id"]
        if token["pos"] is None:
            pendencias.extend(indice.anotacao_ausente([ident], ["pos"], "A seleção de evidências verbais depende de POS."))
        if token["pos"] not in _VERBAIS:
            continue
        pendencias.extend(indice.anotacao_ausente([ident], ["morfologia"], "A evidência temporal verbal requer morfologia."))
        morfologia = token["morfologia"]
        if morfologia is None:
            continue
        evidencias = [f"{chave}={morfologia[chave]}" for chave in ("Tense", "Mood", "VerbForm") if chave in morfologia]
        if not evidencias:
            continue
        nucleo = indice.pertence.get(ident)
        vinculos, ambiguidades, contexto = indice.contexto_enunciativo(ident, nucleo)
        if nucleo is not None:
            vinculos.append(_vinculo("predicado_associado", nucleo))
        ocorrencias.append(_proposta(
            "tempo", "flexao_verbal", [ident], [] if nucleo is None else [nucleo], indice.alcance(nucleo, ident),
            f"A anotação morfológica de {token['texto']!r} registra " + ", ".join(evidencias) + ". Os valores recebidos são evidências linguísticas preservadas, sem correção do modelo ou conversão em data.",
            vinculos=vinculos, ambiguidades=ambiguidades, contexto=contexto))
    return ocorrencias, pendencias


def _quando_temporal(indice, ident):
    token = indice.tokens[ident]
    nucleo = indice.pertence.get(ident)
    return (token["pos"] in {"SCONJ", "ADV"} and indice.dependencia[ident] in {"mark", "advmod"}
            and nucleo is not None and indice.dependencia[nucleo] == "advcl")


def _tempo_relativo(indice, habilitadas):
    ocorrencias, pendencias = [], []
    for token in indice.lista:
        palavra, ident = token["texto"].lower(), token["id"]
        if palavra not in _TEMPORAIS | {"quando"}:
            continue
        pendencias.extend(indice.anotacao_ausente([ident], ["pos"], "A expressão temporal precisa de função adverbial ou subordinativa."))
        if palavra == "quando":
            if not _quando_temporal(indice, ident):
                continue
            categoria = "construcao_temporal"
            explicacao = "Quando é marcador adverbial/subordinativo de uma oração advcl; propõe uma relação temporal entre predicados sem produzir datas."
        else:
            if token["pos"] != "ADV" or indice.dependencia[ident] not in {"advmod", "obl", "npadvmod"}:
                continue
            categoria = "referencia_relativa"
            explicacao = f"{token['texto']!r} é referência temporal relativa com POS=ADV e dependência {token['dependencia']}; seu valor relativo é preservado sem usar a data técnica de processamento."
        nucleo = indice.pertence.get(ident)
        vinculos, ambiguidades, contexto = indice.contexto_enunciativo(ident, nucleo)
        if nucleo is not None:
            vinculos.append(_vinculo("predicado_associado", nucleo))
        if palavra in _TEMPORAIS:
            contexto.append("Determinar referência temporal apropriada ao enunciado para eventual resolução contextual; nenhuma data absoluta foi calculada.")
        ocorrencias.append(_proposta("tempo", categoria, [ident], [] if nucleo is None else [nucleo],
                                     indice.alcance(nucleo, ident), explicacao, vinculos=vinculos,
                                     ambiguidades=ambiguidades, contexto=contexto))
    return ocorrencias, pendencias


def _conteudo_modal(indice, ident):
    filhos = indice.filhos[ident]
    complementos = [i for i in filhos if indice.dependencia[i] in {"xcomp", "ccomp", "obj", "iobj", "obl", "csubj"}]
    if not complementos:
        return None, []
    alcance = indice.ordenar(i for comp in complementos for i in indice.descendentes(comp))
    return alcance or None, complementos


def _limitacoes_modal(indice, ident, alcance):
    vinculos, ambiguidades, contexto = indice.contexto_enunciativo(ident, indice.pertence.get(ident))
    for filho in indice.filhos[ident]:
        token = indice.tokens[filho]
        if token["texto"].lower() in _NEGATIVOS and indice.dependencia[filho] in {"advmod", "neg", "cc"}:
            vinculos.append(_vinculo("marcador_negacao", filho))
            ambiguidades.append("O marcador modal é modificado por negação; não inferir a modalidade positiva do conteúdo.")
    if alcance:
        for filho in alcance:
            if indice.tokens[filho]["texto"].lower() in _NEGATIVOS and indice.dependencia[filho] in {"advmod", "neg", "cc"}:
                vinculos.append(_vinculo("negacao_no_conteudo", filho))
                ambiguidades.append("O conteúdo complemental contém negação; sua relação com a modalidade requer leitura contextual.")
                break
    else:
        contexto.append("Identificar conteúdo ao qual se aplica o marcador modal; nenhum período inteiro foi usado como substituto.")
    return vinculos, ambiguidades, contexto


def _modalidade_verbal(indice, habilitadas):
    ocorrencias, pendencias = [], []
    for token in indice.lista:
        ident = token["id"]
        if token["pos"] is None:
            pendencias.extend(indice.anotacao_ausente([ident], ["pos"], "A seleção de marcadores verbais requer POS."))
        if token["pos"] not in _VERBAIS:
            continue
        pendencias.extend(indice.anotacao_ausente([ident], ["lema"], "O catálogo de modalidades verbais requer lema."))
        lema = token["lema"].lower() if token["lema"] is not None else None
        if lema not in _MODAIS and lema != "ter":
            continue
        alcance, complementos = _conteudo_modal(indice, ident)
        # Alguns parsers ligam o modal como auxiliar ao verbo de conteúdo.
        if indice.dependencia[ident] == "aux":
            nucleo_conteudo = token["cabeca_token_id"]
            complementos = [nucleo_conteudo]
            alcance = [i for i in indice.descendentes(nucleo_conteudo) if i != ident]
        if lema in {"conseguir", "saber"}:
            verbais_infinitivos = [i for i in complementos if indice.tokens[i]["pos"] in _VERBAIS
                                  and indice.dependencia[i] == "xcomp"]
            if not verbais_infinitivos:
                # Saber que alguém saiu é conhecimento proposicional; não
                # comprova capacidade. Conseguir que... também não é o padrão
                # de habilidade infinitiva coberto por esta primeira versão.
                continue
            infinitivos = []
            for comp in verbais_infinitivos:
                morfologia = indice.tokens[comp]["morfologia"]
                if morfologia is None or "VerbForm" not in morfologia:
                    pendencias.append({"token_ids": [comp], "campos": ["morfologia.VerbForm"],
                                       "motivo": "A leitura de capacidade de saber/conseguir exige complemento xcomp infinitivo anotado."})
                elif morfologia["VerbForm"] == "Inf":
                    infinitivos.append(comp)
            if not infinitivos:
                continue
        marcador = [ident]
        if lema == "ter":
            # Que/de precisa ser marcador do complemento verbal, não qualquer
            # ocorrência posterior na frase ("tem um livro que...").
            verbais = [i for i in complementos if indice.tokens[i]["pos"] in _VERBAIS
                       and indice.dependencia[i] in {"xcomp", "ccomp"}]
            palavras = [i for comp in verbais for i in indice.filhos[comp]
                        if indice.tokens[i]["texto"].lower() in {"que", "de"}
                        and indice.dependencia[i] in {"mark", "case"}]
            if not palavras:
                continue
            marcador = indice.ordenar([ident] + palavras)
            categoria = "obrigacao"
        else:
            categoria = _MODAIS[lema]
        vinculos, ambiguidades, contexto = _limitacoes_modal(indice, ident, alcance)
        vinculos += [_vinculo("conteudo_modal", i) for i in complementos]
        alternativas = []
        if lema == "poder":
            alternativas = ["possibilidade", "permissao", "capacidade"]
            ambiguidades.append("A sintaxe de poder não distingue, por si, possibilidade, permissão e capacidade.")
            contexto.append("Distinguir a leitura de poder com contexto na etapa 08.")
        elif lema == "dever":
            alternativas = ["obrigacao", "inferencia_epistemica"]
            ambiguidades.append("Dever pode propor obrigação ou inferência epistêmica; a construção não resolve a leitura.")
        if categoria == "desejo":
            contexto.append("A ocorrência registra desejo expresso pela construção, sem atribuir conceito teórico ou estado ao narrador.")
        alcance = alcance or None
        citacao = indice.contexto_citacao(ident)
        if citacao is not None:
            if not citacao["fechado"]:
                alcance = None
            elif alcance is not None:
                alcance = [i for i in alcance if indice.contexto_citacao(i) is citacao] or None
        ocorrencias.append(_proposta(
            "modalidade", categoria, marcador, [ident], alcance,
            f"O marcador {token['texto']!r} tem lema={lema}, POS={token['pos']} e ligação sintática com o conteúdo anotado; o catálogo propõe {categoria}.",
            vinculos=vinculos, ambiguidades=ambiguidades, alternativas=alternativas, contexto=contexto))
    return ocorrencias, pendencias


def _modalidade_lexical(indice, habilitadas):
    ocorrencias, pendencias = [], []
    adverbios = {"talvez": "duvida", "possivelmente": "possibilidade", "provavelmente": "possibilidade"}
    adjetivos = {"necessário": "necessidade", "obrigatório": "obrigacao", "capaz": "capacidade"}
    for token in indice.lista:
        ident, palavra = token["id"], token["texto"].lower()
        lema = token["lema"].lower() if token["lema"] is not None else None
        if token["pos"] == "ADJ" and ident in indice.nucleos and lema is None:
            # O lexema de qualquer predicado adjetival desconhecido pode
            # pertencer ao catálogo. Não limitamos a inspeção a formas
            # singulares, nem usamos a superfície para substituir o lema.
            pendencias.extend(indice.anotacao_ausente([ident], ["lema"], "A seleção de predicados adjetivais modais requer lema recebido, inclusive flexões plurais."))
            continue
        if palavra not in adverbios and lema not in adjetivos:
            continue
        pendencias.extend(indice.anotacao_ausente([ident], ["pos"], "A modalidade lexical requer classe adverbial/adjetival."))
        complementos = []
        if palavra in adverbios:
            if token["pos"] != "ADV" or indice.dependencia[ident] not in {"advmod", "discourse"}:
                continue
            categoria, nucleo = adverbios[palavra], indice.pertence.get(ident)
            alcance = indice.alcance(nucleo, ident)
        else:
            if token["pos"] != "ADJ" or ident not in indice.nucleos:
                continue
            categoria, nucleo = adjetivos[lema], ident
            alcance, complementos = _conteudo_modal(indice, ident)
        vinculos, ambiguidades, contexto = _limitacoes_modal(indice, ident, alcance)
        if nucleo is not None:
            vinculos.append(_vinculo("predicado_associado", nucleo))
        vinculos += [_vinculo("conteudo_modal", i) for i in complementos]
        ocorrencias.append(_proposta(
            "modalidade", categoria, [ident], [] if nucleo is None else [nucleo], alcance,
            f"{token['texto']!r} satisfaz o padrão lexical {categoria} com POS={token['pos']} e dependência {token['dependencia']}; a proposta se limita à construção linguística observada.",
            vinculos=vinculos, ambiguidades=ambiguidades, contexto=contexto))
    return ocorrencias, pendencias


def _conector_simples(indice, habilitadas):
    ocorrencias, pendencias = [], []
    supressos = _supressos(indice, habilitadas)
    for token in indice.lista:
        ident, palavra = token["id"], token["texto"].lower()
        if palavra not in {"mas", "porque", "se", "quando"} or ident in supressos:
            continue
        pendencias.extend(indice.anotacao_ausente([ident], ["pos"], "A função de conector depende de POS e relação sintática."))
        dep, nucleo = indice.dependencia[ident], indice.pertence.get(ident)
        if nucleo is None:
            continue
        dep_nucleo = indice.dependencia[nucleo]
        pai = indice.pai_oracional(nucleo)
        categoria = None
        if palavra == "mas" and token["pos"] == "CCONJ" and dep == "cc":
            cabeca = token["cabeca_token_id"]
            # V1 cobre contraste entre predicados. Não descreve oposição
            # nominal ("João, mas não Pedro") como oração marcada inteira.
            if cabeca in indice.nucleos and indice.dependencia[cabeca] == "conj":
                categoria = "contraste"
        elif palavra == "porque" and token["pos"] == "SCONJ" and dep == "mark" and dep_nucleo in {"advcl", "ccomp", "root"}:
            categoria = "causa"
        elif palavra == "se" and token["pos"] == "SCONJ" and dep == "mark" and dep_nucleo in {"advcl", "root"}:
            categoria = "condicao"
        elif palavra == "quando" and _quando_temporal(indice, ident):
            categoria = "tempo"
        if categoria is None:
            continue
        vinculos, ambiguidades, contexto = indice.contexto_enunciativo(ident, nucleo)
        vinculos.append(_vinculo("oracao_marcada", nucleo))
        if pai is not None:
            vinculos.append(_vinculo("relacao_com", pai))
        else:
            contexto.append("Determinar a unidade relacionada/antecedente no contexto; não há vínculo sintático suficiente para identificá-la.")
        if palavra == "porque" and dep_nucleo == "ccomp":
            ambiguidades.append("Porque em complemento pode depender de discurso relatado; a atribuição causal requer contexto.")
        ocorrencias.append(_proposta(
            "conectores", categoria, [ident], [nucleo], indice.alcance(nucleo, ident),
            f"{token['texto']!r} tem POS={token['pos']}, dependência {token['dependencia']} e núcleo ligado por {indice.tokens[nucleo]['dependencia']}; essas condições sustentam a proposta de {categoria}.",
            vinculos=vinculos, ambiguidades=ambiguidades, contexto=contexto))
    return ocorrencias, pendencias


def _por_isso(indice, habilitadas):
    ocorrencias, pendencias = [], []
    for ids in indice.periodos.values():
        for i in range(len(ids) - 1):
            por_id, isso_id = ids[i:i + 2]
            por, isso = indice.tokens[por_id], indice.tokens[isso_id]
            if por["texto"].lower() != "por" or isso["texto"].lower() != "isso":
                continue
            pendencias.extend(indice.anotacao_ausente([por_id, isso_id], ["pos"], "A expressão por isso exige funções de preposição e demonstrativo."))
            if (por["pos"] != "ADP" or isso["pos"] not in {"PRON", "ADV"}
                    or indice.dependencia[por_id] != "case" or por["cabeca_token_id"] != isso_id
                    or indice.dependencia[isso_id] not in {"obl", "advmod"}):
                continue
            nucleo = indice.pertence.get(isso_id)
            if nucleo is None:
                continue
            # V1 cobre uso discursivo anteposto. Depois do predicado, por
            # isso pode ser um argumento ("passei por isso", "lutei por isso")
            # que estas anotações não distinguem de retomada consequencial.
            if indice.posicao[por_id] >= indice.posicao[nucleo]:
                continue
            pai = indice.pai_oracional(nucleo)
            vinculos, ambiguidades, contexto = indice.contexto_enunciativo(isso_id, nucleo)
            vinculos.append(_vinculo("consequente", nucleo))
            if pai is not None and indice.posicao[pai] < indice.posicao[por_id]:
                vinculos.append(_vinculo("antecedente_proposto", pai))
                ambiguidades.append("O núcleo anterior é candidato a antecedente, sem comprovar causalidade pragmática.")
            else:
                contexto.append("Resolver o antecedente de por isso na etapa 08; não foi inventada unidade causal, inclusive entre períodos.")
            ocorrencias.append(_proposta(
                "conectores", "consequencia", [por_id, isso_id], [nucleo], indice.alcance(nucleo, isso_id),
                "Os tokens adjacentes por isso estão ligados por case e obl/advmod ao predicado; a expressão propõe consequência, com antecedente sujeito a resolução contextual.",
                vinculos=vinculos, ambiguidades=ambiguidades, contexto=contexto))
    return ocorrencias, pendencias


_DETECTORES = {
    "oracao_predicado": _oracoes, "negacao_correlativa": _correlativa,
    "negacao_nao": lambda indice, regras: _negacao(indice, regras, "não"),
    "negacao_nunca": lambda indice, regras: _negacao(indice, regras, "nunca"),
    "negacao_nem": lambda indice, regras: _negacao(indice, regras, "nem"),
    "negacao_multiplos": _multiplos, "tempo_flexao": _tempo_flexao,
    "tempo_relativo": _tempo_relativo, "modalidade_verbal": _modalidade_verbal,
    "modalidade_lexical": _modalidade_lexical, "conector_simples": _conector_simples,
    "conector_por_isso": _por_isso,
}



def _candidatos_estruturais(indice, regra_id):
    """Candidatos cuja delimitação usa POS de outros tokens no período."""
    palavras_negacao = {"negacao_nao": {"não"}, "negacao_nunca": {"nunca"},
                        "negacao_nem": {"nem"}, "negacao_multiplos": _NEGATIVOS}
    for token in indice.lista:
        ident, palavra, pos = token["id"], token["texto"].lower(), token["pos"]
        lema = token["lema"].lower() if token["lema"] is not None else None
        candidato = False
        if regra_id == "oracao_predicado":
            candidato = ident in indice.nucleos or pos is None
        elif regra_id in palavras_negacao:
            candidato = (palavra in palavras_negacao[regra_id]
                         and (pos in {"ADV", "PART", "CCONJ", None} or indice.contexto_citacao(ident) is not None))
        elif regra_id == "negacao_correlativa":
            candidato = palavra in {"não", "só", "somente", "apenas", "mas", "também"} and pos in {"ADV", "PART", "CCONJ", None}
        elif regra_id == "tempo_flexao":
            candidato = pos in _VERBAIS or pos is None
        elif regra_id == "tempo_relativo":
            candidato = palavra in _TEMPORAIS | {"quando"} and pos in {"ADV", "SCONJ", None}
        elif regra_id == "modalidade_verbal":
            candidato = pos in _VERBAIS and (lema in _MODAIS or lema == "ter" or lema is None)
        elif regra_id == "modalidade_lexical":
            candidato = ((palavra in {"talvez", "possivelmente", "provavelmente"} and pos in {"ADV", None})
                         or (lema in {"necessário", "obrigatório", "capaz"} and pos in {"ADJ", None}))
        elif regra_id == "conector_simples":
            candidato = palavra in {"mas", "porque", "se", "quando"} and pos in {"CCONJ", "SCONJ", "ADV", None}
        elif regra_id == "conector_por_isso":
            proxima = indice.posicao[ident] + 1
            candidato = (palavra == "por" and proxima < len(indice.lista)
                         and indice.lista[proxima]["texto"].lower() == "isso"
                         and indice.lista[proxima]["periodo_id"] == token["periodo_id"])
        if candidato:
            yield ident

def detectar_regra(regra_id, analise, *, regras_habilitadas=None, contexto=None):
    """Retorne propostas e impedimentos; erros operacionais não viram lista vazia.

    ``contexto`` pode ser reutilizado somente com a análise que o originou.
    Regra correlativa habilitada suprime apenas marcadores incompatíveis,
    independentemente da ordem em que os detectores são chamados.
    """
    if regra_id not in _DETECTORES:
        raise ValueError(f"Detector indisponível para a regra {regra_id!r}.")
    indice = construir_contexto(analise) if contexto is None else contexto
    if not isinstance(indice, _Indice) or indice.analise is not analise:
        raise ValueError("O contexto deve pertencer à mesma análise recebida.")
    habilitadas = set(REGRA_IDS if regras_habilitadas is None else regras_habilitadas)
    ocorrencias, pendencias = _DETECTORES[regra_id](indice, habilitadas)
    # O alcance depende também das classes dos possíveis predicados e
    # fronteiras, não apenas da classe do marcador. Uma regra habilitada
    # isoladamente deve diagnosticar essa dependência sem confiar que outra
    # regra fará a verificação. POS ausente nunca significa "não verbal".
    periodos_afetados = set()
    for ident in _candidatos_estruturais(indice, regra_id):
        periodo = indice.tokens[ident]["periodo_id"]
        if indice.pos_ausentes_periodo[periodo]:
            periodos_afetados.add(periodo)
    for periodo in sorted(periodos_afetados):
        pendencias.append({
            "token_ids": indice.ordenar(indice.pos_ausentes_periodo[periodo]),
            "campos": ["pos"],
            "motivo": "A determinação do núcleo e das fronteiras do alcance depende de POS dos possíveis predicados do período; ausência não equivale a classe não verbal.",
        })
    for proposta in ocorrencias:
        periodo = indice.tokens[proposta["marcador_token_ids"][0]]["periodo_id"]
        if periodo in periodos_afetados:
            proposta["alcance_token_ids"] = None
            proposta["necessidade_contexto"].append("Alcance impedido por POS ausente nas anotações de origem; completar a anotação antes de definir fronteiras oracionais.")
    return {"ocorrencias": ocorrencias, "pendencias": pendencias}
