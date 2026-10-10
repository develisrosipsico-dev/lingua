# Avaliação da referência manual

A referência em `examples/referencia_morfologia.json` define 15 alvos em cinco
frases fornecidas no enunciado. Os lemas e classes foram definidos como
expectativas linguísticas; os intervalos são posições no texto original em
pontos de código Unicode, base zero e fim exclusivo.

São avaliados pronomes, substantivos, verbos e advérbios selecionados. Artigos,
pontuação e traços flexionais não fazem parte desta pequena referência. `guarda`
é avaliada tanto como verbo (`guardar`, `VERB`) quanto como substantivo
(`guarda`, `NOUN`). Nenhuma expectativa é substituída pela previsão do modelo.

Na pasta `lingua/` do repositório, com as dependências instaladas, execute:

```bash
../.venv/bin/python avaliacao_morfologia.py --saida-json examples/avaliacao_morfologia.json --saida-markdown docs/avaliacao_morfologia_resultado.md
```

No Windows, substitua o executável por `..\.venv\Scripts\python.exe`.
Os registros de cada frase são gerados pela preparação, segmentação e anotação
reais. O modelo recebe cada frase inteira, com seu contexto, e não palavras
isoladas. As versões, IDs e hashes reais constam no relatório JSON.

A comparação utiliza intervalos e conteúdo; um alvo com fronteiras incompatíveis
produz erro explícito. Divergências de lema ou classe são reportadas sem corrigir
a anotação ou exigir acerto total para a prontidão técnica.

As taxas de acerto são métricas calculadas nestes 15 alvos, não valores de
confiança atribuídos aos tokens nem uma estimativa de qualidade para qualquer
texto. O [relatório executado](avaliacao_morfologia_resultado.md) e o
[JSON correspondente](../examples/avaliacao_morfologia.json) mantêm os resultados
obtidos, inclusive divergências.
