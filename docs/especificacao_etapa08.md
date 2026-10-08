# Etapa 08 — Construir unidades de contexto

Especificação consolidada com as decisões da revisão aprovadas pelo usuário. A etapa 08 ainda não está implementada; este documento define o trabalho futuro.

Implemente a etapa 08 sobre a saída validada da etapa 07. Organize cada período junto de suas unidades vizinhas, seu parágrafo e suas anotações linguísticas, mantendo acesso ao documento completo.

O produto é um conjunto de unidades com contexto explícito, rastreável e reproduzível, disponível para a etapa 09 — Vetorizar conteúdo e unidades. Esta etapa organiza os dados já produzidos para que um trecho possa ser recuperado junto ao seu contexto.

Estas são instruções de implementação, não uma declaração de que os módulos anteriores estejam concluídos.

## 1. Examinar o projeto e validar a entrada

Leia as instruções do repositório, os módulos anteriores, os contratos JSON, as rotas, a persistência e os testes. Reutilize os validadores existentes, mapear_intervalo de preparacao.py e as funções de consulta de segmentacao.py, incluindo contexto_periodo quando seu contrato for adequado.

Valide efetivamente a saída da etapa 07 e seus registros de origem. Se uma dependência estiver ausente ou inválida, informe precisamente o que falta. Prossiga somente se o relatório recalculado confirmar pronto_para_etapa_08: true. Um diagnóstico estruturalmente válido, mas incompleto, não deve ser promovido à etapa 09. Preserve também a configuração e a cobertura das regras da origem; prontidão não implica que todas as famílias estavam habilitadas.

Preserve textos, tokens, separadores, parágrafos, períodos, anotações, identificadores, hashes e metadados. Trabalhe sobre uma cópia. As unidades contextuais devem apontar para a segmentação existente.

## 2. Definir a política de contexto

Na configuração inicial, crie uma unidade para cada período existente, na ordem do documento. Esse período é o foco da unidade.

Use os parâmetros:

- raio_anterior=1: até um período anterior;
- raio_seguinte=1: até um período seguinte;
- atravessar_paragrafos=False: seleção limitada ao parágrafo do foco.

Permita configurar os raios com números inteiros não negativos. Rejeite valores inválidos, incluindo booleanos usados como números. atravessar_paragrafos deve aceitar somente um booleano; 0, 1, strings e null não substituem False ou True.

Selecione os períodos vizinhos pela ordem e pelos identificadores registrados. O foco aparece exatamente uma vez na janela. No início ou no fim do parágrafo, use os vizinhos disponíveis, sem preencher a janela artificialmente.

Uma configuração com ambos os raios iguais a zero produz somente o foco. Uma configuração maior inclui mais períodos disponíveis, sem sair do documento.

Se atravessar_paragrafos=True, selecione os vizinhos pela ordem global e registre todos os parágrafos envolvidos. O período central continua pertencendo ao seu único parágrafo original. Essa opção amplia o contexto sem alterar limites de qualquer período.

Registre a política completa e sua versão em cada execução. Para a mesma entrada e configuração, a seleção e a ordenação devem ser determinísticas.

A consulta contexto_periodo da etapa 04 seleciona vizinhos pela ordem global e pode atravessar parágrafos. Preserve esse contrato e sua API. A etapa 08 deve aplicar sua própria política versionada, respeitando o bloqueio de passagem entre parágrafos na configuração inicial.

## 3. Construir cada unidade

Use um identificador novo para a unidade, associado ao identificador da execução contextual. Preserve o identificador original do período central como referência. Use um ID próprio para a execução da etapa 08 e registre explicitamente o ID da execução de regras da etapa 07 utilizada como origem.

Cada unidade precisa conter:

- identificador próprio e ordem;
- periodo_foco_id e paragrafo_foco_id;
- texto do foco e seus intervalos no trabalho e no original;
- identificadores dos tokens do foco, em ordem;
- listas dos períodos anteriores e seguintes selecionados;
- lista completa e ordenada dos períodos da janela, incluindo o foco;
- identificadores dos parágrafos envolvidos;
- texto da janela e seus intervalos no trabalho e no original;
- hashes SHA-256 dos textos exatos do foco e da janela;
- identificação lógica estável do foco com sua seleção contextual, além do ID próprio da execução;
- referências às anotações do foco e às anotações das unidades vizinhas;
- vínculos ao parágrafo completo e ao documento de origem;
- limites encontrados, como falta de vizinhos no início ou no fim;
- ambiguidades e necessidades de contexto herdadas da etapa 07.

A lista anterior deve ficar em ordem de leitura, do vizinho mais distante selecionado até o mais próximo. A lista seguinte também segue a ordem do documento.

As estruturas menores propostas pela etapa 07, como orações e alcances de regras, devem permanecer acessíveis por suas referências e intervalos. Elas não substituem o período central nem criam uma nova segmentação.

Crie todas as unidades, inclusive para períodos sem entidades ou sem ocorrências de regras. A presença de uma anotação específica não é condição para um período receber contexto. Quando a origem estiver pronta e não contiver períodos, produza uma lista vazia válida, com cobertura zero, sem criar foco artificial.

## 4. Preservar o texto e a rastreabilidade

Para uma janela de períodos consecutivos, seu intervalo no trabalho começa no início do primeiro período selecionado e termina no fim do último. Extraia o texto diretamente desse recorte do trabalho.

Não monte a janela juntando textos com um espaço ou uma quebra inventada. O recorte precisa incluir exatamente os separadores existentes entre os períodos e, quando autorizado, entre os parágrafos.

O texto do foco também deve ser um recorte exato do seu intervalo já registrado. Use blocos separados foco e janela, cada um com texto, intervalo no trabalho e intervalo no original. Quando armazenado, identifique o texto original separadamente. O recorte não precisa incluir espaços externos ao primeiro e ao último período selecionado; esses espaços permanecem preservados no documento integral.

Use coordenadas Unicode [inicio, fim) e converta os intervalos ao original com mapear_intervalo ou com mapear_intervalos em lote, de convenção equivalente. Quando armazenar também um texto do original, extraia-o do intervalo correspondente nesse original.

Valide a fonte uma vez por construção, indexe períodos por ordem e parágrafo e anotações por token e período. Reutilize esses índices e prefira mapear_intervalos em lote. Não revalide toda a segmentação por foco chamando repetidamente contexto_periodo. Preserve o registro integral de origem uma vez por execução; as unidades devem referenciá-lo.

Se houver limites de recursos ou de tamanho do resultado, declare-os e produza erro explícito ao excedê-los. Não omita unidades nem trunque textos silenciosamente.

CRLF e LF podem produzir comprimentos e posições diferentes no trabalho. Para preparações literal e normalizada do mesmo documento, a seleção linguística e os intervalos correspondentes no original devem ser equivalentes.

Preserve os intervalos descontínuos das anotações da etapa 07 como listas de intervalos. O fato de a janela ter um recorte contínuo não autoriza preencher lacunas de uma evidência descontínua.

Mantenha qualquer texto de apresentação, como rótulos “anterior” e “seguinte”, separado dos textos canônicos. A interface pode destacar trechos sem modificar os dados exportados.

### 4.1. Hashes de integridade dos textos

Registre foco.sha256_texto e janela.sha256_texto, calculados com SHA-256 sobre a codificação UTF-8 do texto exato de trabalho de cada bloco. Registre o algoritmo e a codificação utilizados. Não normalize Unicode, espaços, tabulações ou quebras de linha antes do cálculo e não inclua rótulos de apresentação. Use o termo hash de integridade para essas garantias.

Quando armazenar o texto original do bloco, registre também seu próprio sha256_texto_original. Os hashes do trabalho e do original podem diferir em preparações normalizadas; cada hash deve identificar explicitamente o campo ao qual corresponde.

O validador deve recalcular os hashes a partir dos recortes verificados da origem. Alterar o texto e recalcular somente seu hash não torna o registro válido: texto, intervalo e fonte precisam continuar coerentes. Os hashes das unidades complementam os hashes do documento e não os substituem.

Na etapa 09, escolha explicitamente o campo textual e verifique seu hash antes de vetorizar, preservando o vínculo com a unidade e a origem.

### 4.2. Identificação lógica estável

Registre janela_logica_id, distinta do ID próprio da unidade e da execução. Calcule-a a partir de uma descrição versionada da seleção contextual, preservada no registro para conferência. Essa descrição deve conter:

- documento_id e hash SHA-256 do documento original;
- intervalo do foco no original e intervalo de seu parágrafo de origem;
- sequência ordenada dos intervalos originais dos períodos selecionados e de seus parágrafos;
- identificação e versão das regras de segmentação;
- identificação e versão da política de contexto e seus parâmetros completos, incluindo ambos os raios e atravessar_paragrafos;
- versão do contrato da identidade lógica.

Serialize essa descrição como JSON canônico com chaves ordenadas, separadores vírgula/dois-pontos sem espaços adicionais, Unicode sem escapes desnecessários e codificação UTF-8, e aplique SHA-256. Documente os nomes e tipos exatos dos campos. Não inclua IDs ou datas gerados para a execução contextual, a execução de regras ou os períodos: a identidade lógica usa o documento e os limites originais, enquanto os IDs reais das fontes permanecem preservados nas referências.

Reexecutar sobre o mesmo documento, os mesmos limites e a mesma política deve produzir a mesma janela_logica_id, mesmo com outro ID de execução ou outra data. Alterar o documento, o foco, a seleção, os limites ou a política deve alterar a descrição e sua identidade. Focos diferentes continuam distintos mesmo quando possuem o mesmo texto de janela. Políticas diferentes continuam distintas mesmo quando as bordas do documento resultam na mesma seleção disponível.

Preparações literal e normalizada podem compartilhar a identidade lógica quando documento, limites originais, regras de segmentação e política coincidirem. Seus hashes de texto de trabalho podem diferir. Portanto, janela_logica_id não substitui a verificação do hash do campo efetivamente vetorizado nem identifica, sozinha, um vetor reutilizável.

A identidade lógica pode repetir-se em execuções históricas. Não elimine essas execuções nem use janela_logica_id como substituto do ID próprio da unidade. Os IDs de origem e suas versões continuam registrando qual processamento forneceu cada resultado.

## 5. Organizar as anotações e suas pendências

Associe ao foco suas anotações morfológicas, relações sintáticas, entidades e ocorrências de regras, usando os identificadores de origem. Separe essas referências das anotações pertencentes aos vizinhos.

Os itens morfológicos e sintáticos usam token_id, sem um ID individual de anotação. Referencie morfologia por anotacao_id e token_id, sintaxe por analise_id e token_id, entidades por seus IDs na análise e regras pelos IDs das ocorrências na execução de origem. Use o periodo_id proprietário para separar foco e vizinhos. Um vínculo com outro período não transfere a propriedade da ocorrência. Preserve os alvos tipados dos vínculos, sem pressupor que todos sejam períodos.

Quando uma ocorrência tiver evidências ou relações em mais de um período, preserve essa estrutura e seus identificadores. Não atribua todas as anotações da janela ao foco.

Uma ambiguidade registrada na etapa 07 deve continuar representada. Acrescente quais unidades agora estão disponíveis para examiná-la. As necessidades da etapa 07 são descrições textuais e nem sempre identificam um alvo localizável. Preserve a descrição e identifique sua origem pela ocorrência, tipo do campo e índice da mensagem.

Quando houver uma referência explícita localizável, indique se está na_janela ou fora_da_janela. Caso contrário, registre localizacao_indeterminada. Não declare que uma necessidade está fora da janela somente por seu raio, nem resolvida porque o documento está disponível. Algumas necessidades, como a referência temporal de amanhã, podem depender de informação externa ao documento. Mantenha o acesso ao parágrafo e ao documento em todos esses casos.

Disponibilizar mais texto não resolve automaticamente uma ambiguidade. Por exemplo, “Ela voltou” pode receber o período anterior como contexto, mas a etapa 08 não deve declarar um antecedente de “Ela” apenas por proximidade.

Uma janela tampouco comprova causa, intenção ou relação entre pessoas. Registre as relações provenientes das etapas anteriores com suas regras e limitações. Novas inferências exigem um procedimento específico, com origem e critérios próprios.

A etapa 08 organiza contexto e referências. Não acrescente interpretação de contradições, identificação de desejos ou classificação semântica especializada. Anotações já produzidas na etapa 07 permanecem acessíveis como origem, sem serem reinterpretadas nem promovidas a novas conclusões.

## 6. Validar, persistir e integrar

Implemente funções equivalentes a:

- construir_unidades_contexto(...);
- validar_unidades_contexto(...);
- consulta de uma unidade por seu identificador ou pelo período central.

Permita fornecer o identificador da execução e a data com fuso horário para reprodução. Registre as versões do esquema, do módulo e da política de seleção, ligadas aos registros anteriores.

A consulta por período central deve ser feita dentro de uma execução contextual específica, pois o mesmo período pode participar de históricos com configurações diferentes. A persistência pode seguir o padrão de uma linha por execução, ligada à execução exata de regras, com o conjunto de unidades em JSON. Consultar uma execução antiga deve recuperar sua política e cadeia original, mesmo após novas análises ou regras.

O validador deve verificar:

- validade e preservação integral da entrada;
- exatamente uma unidade para cada período, na mesma ordem;
- cobertura inclusive dos períodos sem anotações;
- identificadores e associações existentes e corretos;
- foco presente uma única vez em sua janela;
- vizinhos corretos para os raios e a política escolhidos;
- ausência de períodos omitidos indevidamente, duplicados ou reordenados na janela;
- coerência entre textos, tokens, intervalos e mapeamento ao original;
- hashes de integridade corretos para cada campo textual registrado;
- descrição da seleção lógica coerente com a fonte, a política e a janela_logica_id recalculada;
- respeito às fronteiras de parágrafo quando a política exigir;
- identificação correta das anotações do foco e dos vizinhos;
- preservação das pendências e referências descontínuas;
- consistência entre a configuração declarada e os resultados.

Uma contagem correta de unidades com vizinhos ou associações errados deve falhar. Nas bordas, confira raio solicitado, quantidade selecionada e motivo do limite, distinguindo fronteira de parágrafo de início ou fim do documento. Um raio zero é uma escolha de configuração, não falta de vizinhos.

Somente um registro efetivamente validado recebe pronto_para_etapa_09: true. Pendências linguísticas documentadas podem seguir; referências quebradas, perda de texto e falhas de construção impedem essa prontidão.

Integre execução, consulta, histórico e download de JSON ao aplicativo. Use a persistência e as transações segundo as convenções existentes. Grave cada execução como um registro novo, sem sobrescrever a origem.

Na interface, apresente o período central destacado, os vizinhos selecionados e acesso ao parágrafo e ao relato completo. Mostre a política aplicada e as pendências relevantes.

## 7. Criar testes e entregar

Use unittest, fixtures com seleção esperada anotada independentemente do construtor e bancos SQLite temporários quando necessários.

Inclua verificações de:

- um período isolado e vários períodos no mesmo parágrafo;
- início, meio e fim do parágrafo e do documento;
- raios zero, assimétricos, maiores que a quantidade disponível e valores inválidos;
- bloqueio de passagem entre parágrafos na configuração inicial;
- passagem configurada entre parágrafos com separadores preservados;
- uma unidade por período e foco aparecendo uma única vez em cada janela;
- referência com contagens corretas, mas vizinhos, ordem ou associações incorretos;
- períodos sem anotações e anotações pertencentes apenas aos vizinhos;
- relações entre períodos, evidências descontínuas e ambiguidades herdadas;
- contexto necessário fora da janela e manutenção das referências ao documento;
- períodos com hesitação, sem alterar a segmentação recebida;
- texto e limites exatos com espaços repetidos, tabulações, acentos e outros caracteres Unicode;
- leitura pelo leitor existente, serialização JSON e leitura de volta;
- preservação da entrada, dos hashes e do histórico;
- equivalência da seleção e dos intervalos no original entre trabalho literal e normalizado;
- identificadores, data com fuso, rotas, consultas e download.

Inclua também testes para origem da etapa 07 estruturalmente válida, porém não pronta; documento sem períodos; tipo estrito de atravessar_paragrafos; limites de parágrafo distintos de limites do documento; consulta do mesmo período em execuções diferentes; preservação de uma execução antiga após novas regras; necessidade sem localização conhecida e informação externa ao documento; e referências a tokens ou ocorrências de outro registro, mesmo com texto igual.

Inclua testes específicos para as garantias de integridade e identidade:

- hashes esperados do foco e da janela definidos independentemente do construtor, incluindo Unicode, espaços, tabulações e CRLF;
- adulteração somente do hash, do texto ou do intervalo e adulteração conjunta de texto/hash que não corresponda à origem;
- mesma origem e política com IDs/datas de execução diferentes mantendo hashes e identidade lógica;
- mudança do documento, do foco, dos limites, dos vizinhos ou da política alterando a identidade lógica;
- janelas com texto igual, mas foco diferente, mantendo identidades lógicas diferentes;
- políticas diferentes com a mesma seleção disponível mantendo identidades diferentes;
- comparações literal/normalizada com identidade lógica equivalente e hashes de trabalho diferentes quando houver transformação;
- histórico preservando duas execuções distintas com a mesma identidade lógica;
- descrição lógica ou janela_logica_id adulterada sendo rejeitada, inclusive após serialização e leitura JSON.

As seleções e os limites esperados devem ser definidos previamente na referência dos testes. Não os gere chamando o próprio construtor de contexto.

Preparações literal e normalizada podem ter IDs diferentes. Compare equivalência pela ordem dos períodos e pelos intervalos no original, preservando os IDs próprios de cada origem. Não exija igualdade literal de IDs entre execuções distintas.

As quantidades fixas do relato de referência da etapa 04 continuam restritas àquela conferência. A regra desta etapa é uma unidade para cada período efetivamente recebido, qualquer que seja sua quantidade.

Entregue o módulo, os testes, a integração, os exemplos JSON e a documentação de reprodução. Execute as verificações apropriadas e informe resultados reais e dependências pendentes.

O produto final é um conjunto de focos e janelas contextuais com acesso às evidências e ao original. Na etapa 09, esses campos podem ser escolhidos explicitamente para a vetorização, mantendo a identificação do texto usado e da unidade que o originou.
