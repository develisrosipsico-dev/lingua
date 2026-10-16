"""Avalie alvos manuais gramaticais com modelos spaCy instalados, sem corrigir dados."""

import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import time
from zoneinfo import ZoneInfo


REFERENCIA = Path(__file__).resolve().parent / 'examples' / 'referencia_gramatical.json'
CAMPOS = ('pos', 'lema', 'morfologia', 'dependencia', 'cabeca_original')
COORDENADAS = {'unidade': 'pontos_de_codigo_unicode', 'base': 0, 'fim_exclusivo': True}


def _intervalo(intervalo, texto):
    if not isinstance(intervalo, dict):
        raise ValueError('Intervalo original ausente ou inválido.')
    inicio, fim = intervalo.get('inicio'), intervalo.get('fim')
    if type(inicio) is not int or type(fim) is not int or not 0 <= inicio < fim <= len(texto):
        raise ValueError('Intervalo original fora do texto.')
    return inicio, fim


def validar_referencia(referencia):
    """Valide coordenadas e escopo explícito; não presuma uma anotação integral."""
    if not isinstance(referencia, dict) or referencia.get('schema_version') != 'lingua.referencia_gramatical.v1':
        raise ValueError('Formato de referência gramatical inválido.')
    if referencia.get('coordenadas') != COORDENADAS:
        raise ValueError('Coordenadas da referência incompatíveis.')
    casos = referencia.get('casos')
    if not isinstance(casos, list) or not casos:
        raise ValueError('A referência deve conter casos.')
    ids = set()
    for caso in casos:
        if not isinstance(caso, dict) or not isinstance(caso.get('id'), str) or not caso['id'] or caso['id'] in ids:
            raise ValueError('Identificador de caso inválido ou duplicado.')
        ids.add(caso['id'])
        texto, alvos = caso.get('texto'), caso.get('alvos')
        if not isinstance(texto, str) or not texto or not isinstance(alvos, list) or not alvos:
            raise ValueError('Cada caso requer texto e alvos.')
        fim_anterior = 0
        for alvo in alvos:
            if not isinstance(alvo, dict):
                raise ValueError('Alvo inválido.')
            inicio, fim = _intervalo(alvo.get('original'), texto)
            if inicio < fim_anterior or alvo.get('texto') != texto[inicio:fim]:
                raise ValueError('Alvos sobrepostos, fora de ordem ou com texto incompatível.')
            fim_anterior = fim
            for campo in ('pos', 'lema', 'dependencia'):
                if campo in alvo and (not isinstance(alvo[campo], str) or not alvo[campo]):
                    raise ValueError('Valor gramatical inválido.')
            if not any(c in alvo for c in CAMPOS):
                raise ValueError('Alvo sem expectativa gramatical.')
            if 'cabeca_original' in alvo:
                _intervalo(alvo['cabeca_original'], texto)
            if 'morfologia' in alvo and (
                not isinstance(alvo['morfologia'], dict) or
                any(not isinstance(k, str) or not k or not isinstance(v, str) or not v
                    for k, v in alvo['morfologia'].items())
            ):
                raise ValueError('Traços morfológicos inválidos.')
        for grupo in caso.get('grupos_verbais', []):
            inicio, fim = _intervalo(grupo.get('original'), texto)
            if grupo.get('texto') != texto[inicio:fim] or not isinstance(grupo.get('verbos'), list):
                raise ValueError('Grupo verbal incompatível com o original.')
    return referencia


def comparar_tokens(texto, previsoes, caso):
    """Compare limites exatos, mantendo ausências como erros e previsões intactas.

    `previsoes` contém texto, original, pos, lema, morfologia (dict), dependencia
    e cabeca_original. Um alvo sem token exatamente alinhado falha alinhamento;
    não se escolhe um subtoken nem se desloca o intervalo silenciosamente.
    """
    if texto != caso['texto']:
        raise ValueError('As previsões não correspondem ao texto da referência.')
    tokens = {}
    for token in previsoes:
        chave = _intervalo(token.get('original'), texto)
        if token.get('texto') != texto[chave[0]:chave[1]] or chave in tokens:
            raise ValueError('Previsão com texto/intervalo incompatível ou duplicado.')
        tokens[chave] = token
    resultados = []
    for alvo in caso['alvos']:
        chave = _intervalo(alvo['original'], texto)
        token = tokens.get(chave)
        morfologia = token.get('morfologia') if token and isinstance(token.get('morfologia'), dict) else {}
        esperado = {c: deepcopy(alvo[c]) for c in CAMPOS if c in alvo}
        obtido = {c: deepcopy(token.get(c)) for c in CAMPOS if c in alvo} if token else None
        acertos = {}
        for campo, valor in esperado.items():
            if campo == 'morfologia':
                # Vazio não é uma restrição; somente traços explicitamente anotados.
                if valor:
                    acertos[campo] = token is not None and isinstance(token.get(campo), dict) and all(
                        token[campo].get(k) == v for k, v in valor.items())
            else:
                acertos[campo] = token is not None and token.get(campo) == valor
        if 'dependencia' in esperado and 'cabeca_original' in esperado:
            acertos['dependencia_e_cabeca'] = acertos['dependencia'] and acertos['cabeca_original']
        resultados.append({
            'texto': alvo['texto'], 'original': deepcopy(alvo['original']),
            'alinhado': token is not None, 'esperado': esperado, 'obtido': obtido,
            'acertos': acertos,
            'tracos': {k: {'esperado': v, 'obtido': morfologia.get(k),
                           'acerto': token is not None and morfologia.get(k) == v}
                       for k, v in alvo.get('morfologia', {}).items()},
        })
    return resultados


def resumir(casos):
    resultados = [r for caso in casos for r in caso['resultados']]
    metricas = {}
    for campo in (*CAMPOS, 'dependencia_e_cabeca'):
        anotados = [r['acertos'][campo] for r in resultados if campo in r['acertos']]
        metricas[campo] = {'acertos': sum(anotados), 'avaliados': len(anotados),
                           'taxa_nos_alvos': sum(anotados) / len(anotados) if anotados else None}
    tracos = [v['acerto'] for r in resultados for v in r['tracos'].values()]
    metricas['tracos_individuais'] = {'acertos': sum(tracos), 'avaliados': len(tracos),
                                    'taxa_nos_alvos': sum(tracos)/len(tracos) if tracos else None}
    return {'casos': len(casos), 'alvos': len(resultados),
            'alvos_alinhados': sum(r['alinhado'] for r in resultados), 'metricas': metricas}


def tokens_spacy(doc):
    return [{'texto': t.text, 'original': {'inicio': t.idx, 'fim': t.idx + len(t)},
             'pos': t.pos_, 'lema': t.lemma_, 'morfologia': t.morph.to_dict(),
             'dependencia': t.dep_,
             'cabeca_original': {'inicio': t.head.idx, 'fim': t.head.idx + len(t.head)}}
            for t in doc if not t.is_space]


def avaliar_referencia(referencia, modelos=('pt_core_news_sm',)):
    """Rode os pipelines completos, apenas se os pacotes já estiverem instalados.

    Nunca baixa modelos. Registra nome, versão, componentes e tempos reais.
    A comparação descreve esses alvos e não permite concluir acurácia global.
    """
    validar_referencia(referencia)
    import spacy
    instalados = {str(m) for m in spacy.util.get_installed_models()}
    modelos = list(dict.fromkeys(modelos))
    if not modelos or any(m not in instalados for m in modelos):
        ausentes = sorted(set(modelos) - instalados)
        raise ValueError('Modelos não instalados: ' + ', '.join(ausentes))
    saida = []
    for modelo in modelos:
        inicio = time.perf_counter()
        nlp = spacy.load(modelo)
        carga = time.perf_counter() - inicio
        casos = []
        for caso in referencia['casos']:
            inicio = time.perf_counter()
            doc = nlp(caso['texto'])
            duracao = time.perf_counter() - inicio
            casos.append({'id': caso['id'], 'tempo_segundos': duracao,
                          'tokens_modelo': len(doc),
                          'resultados': comparar_tokens(caso['texto'], tokens_spacy(doc), caso)})
        saida.append({'modelo': modelo, 'versao_modelo': nlp.meta.get('version'),
                      'versao_spacy': spacy.__version__, 'componentes': list(nlp.pipe_names),
                      'tempo_carga_segundos': carga, 'tempo_inferencia_segundos': sum(c['tempo_segundos'] for c in casos),
                      'resumo': resumir(casos), 'casos': casos})
    canonico = json.dumps(referencia, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return {'schema_version': 'lingua.avaliacao_gramatical.v1',
            'gerado_em': datetime.now(ZoneInfo('America/Sao_Paulo')).isoformat(),
            'referencia_sha256': hashlib.sha256(canonico).hexdigest(),
            'escopo': referencia.get('escopo'), 'referencia': deepcopy(referencia), 'modelos': saida}


def formatar_markdown(relatorio):
    linhas = ['# Avaliação gramatical: alvos revisados', '',
              f"Gerado em {relatorio['gerado_em']}.", '', str(relatorio.get('escopo', '')), '',
              'Pipeline spaCy completo. Sem ajustes nas previsões e sem download implícito.',
              'Traços: apenas chaves anotadas. Dependência e cabeça são comparadas separadamente; a coluna conjunta exige ambas.',
              'Tempos incluem somente estas execuções na máquina da avaliação; não medem desempenho no Mac.', '',
              '| Modelo | POS | Lema | Conjunto de traços | Relação | Cabeça | Relação e cabeça |',
              '| --- | --- | --- | --- | --- | --- | --- |']
    def escape(v):
        return str(v).replace('|', '\\|').replace('\n', '<br>')
    for modelo in relatorio['modelos']:
        metricas = modelo['resumo']['metricas']
        valores = [modelo['modelo'] + ' ' + str(modelo['versao_modelo'])]
        for campo in ('pos', 'lema', 'morfologia', 'dependencia', 'cabeca_original', 'dependencia_e_cabeca'):
            m = metricas[campo]
            valores.append(f"{m['acertos']}/{m['avaliados']}")
        linhas.append('| ' + ' | '.join(valores) + ' |')
    for modelo in relatorio['modelos']:
        linhas += ['', f"## {modelo['modelo']} {modelo['versao_modelo']}", '',
                   f"spaCy {modelo['versao_spacy']}; componentes: {', '.join(modelo['componentes'])}.",
                   f"Carga: {modelo['tempo_carga_segundos']:.3f}s; inferência: {modelo['tempo_inferencia_segundos']:.3f}s.", '',
                   '| Caso | Alvo | Campo | Esperado | Obtido |', '| --- | --- | --- | --- | --- |']
        for caso in modelo['casos']:
            for r in caso['resultados']:
                if not r['alinhado']:
                    linhas.append('| ' + ' | '.join(map(escape, (caso['id'], r['texto'], 'alinhamento', r['original'], 'ausente'))) + ' |')
                for campo, acerto in r['acertos'].items():
                    if acerto or campo == 'dependencia_e_cabeca':
                        continue
                    obtido = r['obtido'].get(campo) if r['obtido'] else None
                    valores = (caso['id'], r['texto'], campo, r['esperado'][campo], obtido)
                    linhas.append('| ' + ' | '.join(map(escape, valores)) + ' |')
    linhas += ['', 'Grupos verbais da referência são controles para testes do detector; não são pontuados como dependências nesta avaliação.',
               'Uma referência pequena, criada após observar erros, serve para regressão. A escolha de modelo exige avaliação adicional em textos independentes.', '']
    return '\n'.join(linhas)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--referencia', type=Path, default=REFERENCIA)
    parser.add_argument('--modelo', action='append', help='Nome de pacote instalado; repita para comparar.')
    parser.add_argument('--saida-json', type=Path)
    parser.add_argument('--saida-markdown', type=Path)
    args = parser.parse_args()
    try:
        ref = json.loads(args.referencia.read_text(encoding='utf-8'))
        relatorio = avaliar_referencia(ref, args.modelo or ['pt_core_news_sm'])
    except (ValueError, OSError) as erro:
        parser.error(str(erro))
    if args.saida_json:
        args.saida_json.write_text(json.dumps(relatorio, ensure_ascii=False, allow_nan=False, indent=2) + '\n', encoding='utf-8')
    if args.saida_markdown:
        args.saida_markdown.write_text(formatar_markdown(relatorio), encoding='utf-8')
    print(json.dumps([{'modelo': m['modelo'], **m['resumo']} for m in relatorio['modelos']], ensure_ascii=False))


if __name__ == '__main__':
    main()
