# Avaliação gramatical: alvos revisados

Gerado em 2026-10-16T03:57:35.595317-03:00.

Alvos selecionados em um texto real e controles independentes. Não é um corpus representativo nem mede acurácia global. Os grupos verbais são expectativas linguísticas para testes de segmentação; não são pontuados pela avaliação de tokens.

Pipeline spaCy completo. Sem ajustes nas previsões e sem download implícito.
Traços: apenas chaves anotadas. Dependência e cabeça são comparadas separadamente; a coluna conjunta exige ambas.
Tempos incluem somente estas execuções na máquina da avaliação; não medem desempenho no Mac.

| Modelo | POS | Lema | Conjunto de traços | Relação | Cabeça | Relação e cabeça |
| --- | --- | --- | --- | --- | --- | --- |
| pt_core_news_sm 3.8.0 | 28/33 | 26/33 | 25/29 | 23/32 | 23/32 | 23/32 |
| pt_core_news_md 3.8.0 | 33/33 | 26/33 | 27/29 | 30/32 | 30/32 | 30/32 |

## pt_core_news_sm 3.8.0

spaCy 3.8.16; componentes: tok2vec, morphologizer, parser, lemmatizer, attribute_ruler, ner.
Carga: 0.197s; inferência: 0.056s.

| Caso | Alvo | Campo | Esperado | Obtido |
| --- | --- | --- | --- | --- |
| documento-8 | afirmem | lema | afirmar | afirmer |
| documento-8 | afirmem | morfologia | {'Mood': 'Sub', 'Number': 'Plur', 'Person': '3', 'Tense': 'Pres', 'VerbForm': 'Fin'} | {'Mood': 'Ind', 'Number': 'Plur', 'Person': '3', 'Tense': 'Pres', 'VerbForm': 'Fin'} |
| documento-8 | afirmem | dependencia | advcl | ROOT |
| documento-8 | afirmem | cabeca_original | {'inicio': 159, 'fim': 167} | {'inicio': 42, 'fim': 49} |
| documento-8 | redefine | lema | redefinir | redefine |
| documento-8 | redefine | morfologia | {'Mood': 'Ind', 'Number': 'Sing', 'Person': '3', 'Tense': 'Pres', 'VerbForm': 'Fin'} | {'Mood': 'Sub', 'Number': 'Sing', 'Person': '3', 'Tense': 'Pres', 'VerbForm': 'Fin'} |
| documento-8 | redefine | dependencia | ROOT | acl |
| documento-8 | redefine | cabeca_original | {'inicio': 159, 'fim': 167} | {'inicio': 134, 'fim': 145} |
| documento-8 | silenciosamente | pos | ADV | ADJ |
| documento-8 | examine | lema | examinar | examine |
| documento-8 | examine | morfologia | {'Mood': 'Sub', 'Number': 'Sing', 'Person': '3', 'Tense': 'Pres', 'VerbForm': 'Fin'} | {'Mood': 'Ind', 'Number': 'Sing', 'Person': '3', 'Tense': 'Pres', 'VerbForm': 'Fin'} |
| documento-8 | meramente | pos | ADV | ADJ |
| documento-8 | meramente | dependencia | advmod | amod |
| documento-8 | meramente | cabeca_original | {'inicio': 559, 'fim': 570} | {'inicio': 542, 'fim': 548} |
| documento-8 | premissa | pos | NOUN | DET |
| documento-8 | premissa | lema | premissa | premisse |
| documento-8 | premissa | dependencia | nsubj | ROOT |
| documento-8 | premissa | cabeca_original | {'inicio': 966, 'fim': 974} | {'inicio': 957, 'fim': 965} |
| documento-8 | continue | dependencia | advcl | xcomp |
| documento-8 | continue | cabeca_original | {'inicio': 1082, 'fim': 1092} | {'inicio': 957, 'fim': 965} |
| documento-8 | orientando | dependencia | xcomp | advcl |
| documento-8 | orientando | cabeca_original | {'inicio': 966, 'fim': 974} | {'inicio': 957, 'fim': 965} |
| documento-8 | convertida | lema | converter | convertir |
| documento-8 | convertida | dependencia | ROOT | conj |
| documento-8 | convertida | cabeca_original | {'inicio': 1082, 'fim': 1092} | {'inicio': 957, 'fim': 965} |
| concessiva-curta | afirmem | lema | afirmar | afirmer |
| concessiva-curta | afirmem | morfologia | {'Mood': 'Sub', 'Number': 'Plur', 'Person': '3', 'Tense': 'Pres', 'VerbForm': 'Fin'} | {'Mood': 'Ind', 'Number': 'Plur', 'Person': '3', 'Tense': 'Pres', 'VerbForm': 'Fin'} |
| subjuntivo-complemento | examine | lema | examinar | examine |
| adverbio-silenciosamente | silenciosamente | pos | ADV | NOUN |
| adverbio-meramente | meramente | pos | ADV | NOUN |
| adverbio-meramente | meramente | dependencia | advmod | ROOT |
| adverbio-meramente | meramente | cabeca_original | {'inicio': 23, 'fim': 34} | {'inicio': 13, 'fim': 22} |
| adverbio-meramente | operacional | dependencia | ROOT | amod |
| adverbio-meramente | operacional | cabeca_original | {'inicio': 23, 'fim': 34} | {'inicio': 13, 'fim': 22} |

## pt_core_news_md 3.8.0

spaCy 3.8.16; componentes: tok2vec, morphologizer, parser, lemmatizer, attribute_ruler, ner.
Carga: 0.577s; inferência: 0.064s.

| Caso | Alvo | Campo | Esperado | Obtido |
| --- | --- | --- | --- | --- |
| documento-8 | afirmem | lema | afirmar | afirmer |
| documento-8 | afirmem | morfologia | {'Mood': 'Sub', 'Number': 'Plur', 'Person': '3', 'Tense': 'Pres', 'VerbForm': 'Fin'} | {'Mood': 'Ind', 'Number': 'Plur', 'Person': '3', 'Tense': 'Pres', 'VerbForm': 'Fin'} |
| documento-8 | examine | lema | examinar | examine |
| documento-8 | continue | dependencia | advcl | ROOT |
| documento-8 | continue | cabeca_original | {'inicio': 1082, 'fim': 1092} | {'inicio': 966, 'fim': 974} |
| documento-8 | convertida | lema | converter | convertir |
| documento-8 | convertida | dependencia | ROOT | acl |
| documento-8 | convertida | cabeca_original | {'inicio': 1082, 'fim': 1092} | {'inicio': 989, 'fim': 999} |
| concessiva-curta | afirmem | lema | afirmar | afirmer |
| concessiva-curta | afirmem | morfologia | {'Mood': 'Sub', 'Number': 'Plur', 'Person': '3', 'Tense': 'Pres', 'VerbForm': 'Fin'} | {'Mood': 'Ind', 'Number': 'Plur', 'Person': '3', 'Tense': 'Pres', 'VerbForm': 'Fin'} |
| subjuntivo-complemento | examine | lema | examinar | examine |
| ambiguidade-guarda | guarda | lema | guarda | Guarda |
| ambiguidade-guarda | guarda | lema | guardar | Guarda |

Grupos verbais da referência são controles para testes do detector; não são pontuados como dependências nesta avaliação.
Uma referência pequena, criada após observar erros, serve para regressão. A escolha de modelo exige avaliação adicional em textos independentes.
