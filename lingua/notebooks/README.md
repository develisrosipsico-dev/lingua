# Vetorizar um livro de gramática no Colab

Abra `vetorizar_livro_gramatica_colab.ipynb` em **Google Colab → Arquivo → Fazer
upload de notebook**. O notebook é autossuficiente: incorpora o código de
extração e exportação e não clona nem modifica os aplicativos.

1. Selecione um ambiente com GPU. O alvo é A100; a GPU e sua VRAM reais são
   verificadas, sem presumir que a memória RAM da máquina seja de 80 GB.
2. Preencha os dados bibliográficos e escolha upload do PDF selecionável ou
   um caminho acessível. `USAR_DRIVE=True` permite manter checkpoints entre
   sessões; a pasta temporária do Colab pode desaparecer ao encerrar o ambiente.
3. Execute a extração e confira texto e imagens das páginas. Confirme os grupos
   que realmente são cabeçalhos/rodapés em `GRUPOS_MARGEM_CONFIRMADOS`.
   A extração original é preservada; nenhum grupo é apagado sem essa seleção.
4. Confira a lista de palavras partidas e preencha `RECOMPOR_PALAVRAS` apenas
   com formas unidas verificadas. Compostos e notação gramatical não são unidos
   automaticamente. As normalizações mantêm uma trilha até os blocos originais.
5. Marque `EXTRACAO_CONFERIDA=True`, gere os fragmentos e confira a auditoria.
   Depois marque `FRAGMENTOS_CONFERIDOS=True` antes de carregar o E5.
6. Execute os vetores, as consultas opcionais e a exportação/download do ZIP.

Os dados bibliográficos de Cunha/Cintra já estão preenchidos; ajuste-os se usar
outro livro. Somente o PDF conhecido pelo SHA-256 recebe o recorte 41–770 usado
na revisão anterior. Para vetorizar todas as páginas, desative
`USAR_CORPO_CUNHA_RECONHECIDO`; para escolher outro intervalo, preencha
`PAGINAS_INCLUIR`. As exclusões ficam registradas. No PDF examinado, confira
especialmente as páginas 54 e 55: pouco texto extraído pode exigir outra fonte
textual ou OCR pontual, que este notebook não executa.

Títulos reconhecidos são reunidos com conteúdo e, quando possível, regras com
seus exemplos. Títulos órfãos ficam auditados, sem vetores isolados. Esse
tratamento não resolve automaticamente a leitura de tabelas ou toda a diagramação.

O E5 Large é baixado na revisão fixada. O notebook reutiliza o PyTorch CUDA
do Colab em um ambiente Python isolado e registra as versões efetivamente
usadas. O ambiente é criado com `with_pip=False`, sem depender do `ensurepip`
do Colab. O pip do Python principal instala os pacotes no destino com
`--python`; reexecutar a célula repara inclusive um ambiente parcialmente
criado, preservando PDF e checkpoints. Inferência e armazenamento são float32, com 1.024 dimensões, mean
pooling e normalização L2. O livro usa `passage: ` e as consultas opcionais
usam `query: `. O teto padrão é 490 tokens, incluindo prefixo, seção e tokens
especiais, dentro do limite de 512 do modelo. A contagem é refeita com o
tokenizador antes da inferência. Falta de memória CUDA reduz
o lote; falhas de outra natureza interrompem preservando checkpoints válidos.

Os testes de recuperação combinam BM25 e E5, agrupam resultados repetidos e
mostram vizinhos da mesma seção. São uma inspeção, sem gabarito ou medida de
acurácia gramatical. As consultas e seus vetores também acompanham o pacote
quando essa etapa é executada.

O ZIP final inclui textos brutos e preparados, referências por página e bloco,
normalizações, auditoria, configuração, código, matriz `.npy`, perfil, histórico
de execução e manifesto SHA-256. PDF, pesos e
checkpoints não são incluídos. Página do PDF e rótulo de página permanecem
distintos; os números impressos exigem conferência própria.

Esta entrega prepara somente o acervo do livro. O importador e o botão por
período no Língua serão implementados posteriormente. O ZIP não é o
`vetorizacao.json` de um relato do Agente Analista.

Cada combinação de PDF, fonte de código, páginas e correções recebe uma pasta.
A retomada verifica hashes e exige o mesmo perfil e versões de bibliotecas;
uma mudança incompatível interrompe, sem misturar lotes. O hardware observado
é registrado no histórico, separado da assinatura do perfil.

O notebook foi validado estruturalmente e em testes locais com PDF real e
adaptador de vetores controlado. A execução E5 real em A100 depende do Colab
e ainda não foi realizada por este atendimento.

Para regenerar o notebook a partir das fontes na raiz do repositório:

```bash
python lingua/notebook_gramatica.py
```

Testes específicos usam `PyMuPDF==1.26.7`, `nbformat==5.10.4` e NumPy no
ambiente de desenvolvimento; gerar o notebook exige apenas a biblioteca padrão.
