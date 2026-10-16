"""Notebook autossuficiente para vetorizar um PDF de gramática no Colab."""

import argparse
import hashlib
import json
from pathlib import Path
from textwrap import dedent


BASE = Path(__file__).resolve().parent
MODELO = "intfloat/multilingual-e5-large"
REVISAO = "3d7cfbdacd47fdda877c5cd8a79fbcc4f2a574f3"
MODULOS = ("gramatica_pdf.py", "gramatica_acervo.py")


def _celula(tipo, nome, fonte):
    cell = {"cell_type": tipo, "id": nome, "metadata": {},
            "source": dedent(fonte).strip().splitlines(keepends=True)}
    if tipo == "code":
        cell.update(execution_count=None, outputs=[])
    return cell


def gerar_notebook_gramatica():
    fontes = {nome: (BASE / nome).read_text(encoding="utf-8") for nome in MODULOS}
    cells = []

    def md(nome, fonte):
        cells.append(_celula("markdown", nome, fonte))

    def code(nome, fonte):
        cells.append(_celula("code", nome, fonte))

    md("introducao", """
        # Vetorizar um livro de gramática — PDF selecionável

        Este notebook prepara um acervo citável com E5 Large. É independente do
        aplicativo: não altera o Língua nem o Agente Analista e não faz análise
        sintática do relato. A importação do acervo no aplicativo será implementada depois.

        No Colab, selecione **Ambiente de execução → Alterar tipo de ambiente de
        execução → GPU**. O alvo é A100; a GPU e sua VRAM reais serão verificadas.
        Os 80 GB da GPU não são a RAM da máquina.

        Execute por etapas. Há uma pausa para conferir a extração antes dos vetores.
        Ao enviar o PDF ao Colab, ele será processado na infraestrutura do Google.
        O modelo é baixado do Hugging Face; não são usados serviços de embeddings.
        A retomada depende de manter a pasta de trabalho; use Drive para persistência
        entre sessões. O PDF e os pesos não são incluídos no ZIP final.
        """)
    code("configuracao", """
        from pathlib import Path
        import hashlib
        import json
        import os
        import subprocess
        import sys
        import textwrap
        import venv

        TITULO = "Nova Gramática do Português Contemporâneo"
        AUTOR = "Celso Cunha; Lindley Cintra"
        EDICAO = "7ª edição, 2ª impressão"
        ANO = "2017"
        CAMINHO_PDF = ""  # Deixe vazio para escolher um PDF do computador na etapa 3.
        USAR_DRIVE = True  # Preserva trabalho e checkpoints entre sessões do Colab.
        PASTA_BASE = Path("/content/gramatica_livro")
        if USAR_DRIVE:
            from google.colab import drive
            drive.mount("/content/drive")
            PASTA_BASE = Path("/content/drive/MyDrive/gramatica_livro")
        PASTA_BASE.mkdir(parents=True, exist_ok=True)

        ALVO_TOKENS = 380
        LIMITE_TOKENS = 490   # Inclui título, prefixo e tokens especiais; modelo aceita até 512.
        LOTE_INICIAL = 128    # Reduzido automaticamente em falta de memória na GPU.
        EXTRACAO_CONFERIDA = False
        FRAGMENTOS_CONFERIDOS = False
        # Recorte previamente usado: só é aplicado ao PDF reconhecido pelo SHA-256.
        USAR_CORPO_CUNHA_RECONHECIDO = True
        PAGINAS_INCLUIR = None  # None = automático; ou lista de páginas PDF (começa em 1).
        """)
    code("fontes", "FONTES = " + repr(fontes) + "\n" + dedent("""
        PASTA_CODIGO = Path("/content/lingua_gramatica_codigo")
        PASTA_CODIGO.mkdir(parents=True, exist_ok=True)
        for nome, conteudo in FONTES.items():
            (PASTA_CODIGO / nome).write_text(conteudo, encoding="utf-8")
        HASHES_FONTES = {nome: hashlib.sha256(texto.encode()).hexdigest() for nome, texto in FONTES.items()}
        print("Fontes incorporadas ao notebook:", HASHES_FONTES)
        """))
    md("ambiente-nota", """
        ## 2. Preparar o ambiente

        O ambiente isolado reutiliza o PyTorch CUDA já instalado pelo Colab.
        As bibliotecas de extração e embeddings são instaladas nele, sem substituir
        o PyTorch da GPU. O download inicial do E5 Large é de aproximadamente 2,2 GB.
        As versões efetivamente usadas serão registradas no pacote.
        """)
    code("ambiente", """
        PASTA_AMBIENTE = Path("/content/venv_gramatica_livro")
        PYTHON_LIVRO = PASTA_AMBIENTE / "bin" / "python"
        if not PYTHON_LIVRO.exists():
            venv.EnvBuilder(with_pip=True, system_site_packages=True).create(PASTA_AMBIENTE)
        subprocess.run([str(PYTHON_LIVRO), "-c",
            "import torch; assert torch.cuda.is_available(), 'Selecione GPU no ambiente de execução do Colab.'; "
            "print('PyTorch CUDA existente:', torch.__version__); "
            "print('GPU:', torch.cuda.get_device_name(0)); "
            "print('VRAM GiB:', round(torch.cuda.get_device_properties(0).total_memory / 2**30, 2))"], check=True)
        subprocess.run([str(PYTHON_LIVRO), "-m", "pip", "install", "--disable-pip-version-check",
            "--no-input", "PyMuPDF==1.26.7", "sentence-transformers==5.2.0",
            "transformers==4.57.3", "huggingface-hub==0.36.0", "numpy>=1.26,<3",
            "sentencepiece==0.2.1", "protobuf>=5.29,<7", "rank-bm25==0.2.2"], check=True)
        os.environ['USE_TF'] = '0'
        os.environ['USE_FLAX'] = '0'
        subprocess.run([str(PYTHON_LIVRO), '-c',
            'import torch, numpy, pymupdf; from transformers import AutoTokenizer; '
            'from sentence_transformers import SentenceTransformer; from rank_bm25 import BM25Okapi; '
            'assert torch.cuda.is_available(); print("OK: bibliotecas e CUDA importadas.")'], check=True)

        def executar(codigo):
            cabecalho = (
                "from pathlib import Path\\nimport json, sys, hashlib\\n"
                "config = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))\\n"
                "pasta = Path(config['pasta_execucao'])\\n"
            )
            subprocess.run([str(PYTHON_LIVRO), "-c", cabecalho + textwrap.dedent(codigo),
                str(CONFIG_PATH)], cwd=PASTA_CODIGO, check=True)
        """)
    md("pdf-nota", """
        ## 3. Escolher o PDF

        Use um PDF com texto selecionável. A extração preserva o texto e aponta
        páginas vazias, possíveis colunas e outras limitações; não aplica OCR.
        Se usar `CAMINHO_PDF`, o arquivo deve estar acessível no Colab ou no Drive.
        """)
    code("pdf", f"""
        if CAMINHO_PDF:
            PDF = Path(CAMINHO_PDF).expanduser()
        else:
            from google.colab import files
            enviados = files.upload()
            if len(enviados) != 1:
                raise ValueError("Escolha exatamente um arquivo PDF.")
            nome, dados = next(iter(enviados.items()))
            PDF = Path("/content") / Path(nome).name
            PDF.write_bytes(dados)
            del enviados, dados
        if not PDF.is_file() or PDF.suffix.lower() != ".pdf":
            raise ValueError("O caminho precisa apontar para um PDF existente.")
        digest = hashlib.sha256()
        with PDF.open("rb") as arquivo:
            for bloco in iter(lambda: arquivo.read(1024 * 1024), b""):
                digest.update(bloco)
        PDF_SHA256 = digest.hexdigest()
        PDF_CUNHA_RECONHECIDO = PDF_SHA256 == 'bd25df1ccaca3320d0ab7a62ad550b5943df092ee65c7ac3173d5f2daa53ac3f'
        PAGINAS_SELECIONADAS = PAGINAS_INCLUIR
        if PAGINAS_SELECIONADAS is None and USAR_CORPO_CUNHA_RECONHECIDO and PDF_CUNHA_RECONHECIDO:
            PAGINAS_SELECIONADAS = list(range(41, 771))
            print('PDF Cunha/Cintra reconhecido: corpo 41–770; demais páginas registradas como excluídas.')
        elif PAGINAS_SELECIONADAS is None:
            print('Serão consideradas todas as páginas. Confira os dados bibliográficos e a cobertura.')
        parametros = {{"modelo": {MODELO!r}, "revisao": {REVISAO!r},
            "prefixo": "passage: ", "alvo_tokens": ALVO_TOKENS, "limite_tokens": LIMITE_TOKENS,
            "hashes_fontes": HASHES_FONTES,
            "metadados": {{"titulo": TITULO or PDF.stem, "autor": AUTOR, "edicao": EDICAO, "ano": ANO}}}}
        chave = hashlib.sha256(json.dumps(parametros, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
        PASTA_EXECUCAO = PASTA_BASE / PDF_SHA256[:16] / chave
        PASTA_EXECUCAO.mkdir(parents=True, exist_ok=True)
        PASTA_INSPECAO = PASTA_EXECUCAO
        CONFIG_PATH = PASTA_EXECUCAO / "config_notebook.json"
        config = {{**parametros, "pdf": str(PDF.resolve()), "pdf_sha256": PDF_SHA256,
            "pasta_execucao": str(PASTA_EXECUCAO), "lote_inicial": LOTE_INICIAL,
            "cache_modelos": "/content/cache_e5_gramatica"}}
        CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        CONFIG_BASE = dict(config)
        print("PDF:", PDF.name, "SHA-256:", PDF_SHA256)
        print("Pasta de trabalho/retomada:", PASTA_EXECUCAO)
        """)
    code("extrair", """
        executar(r'''
        from gramatica_pdf import extrair_pdf
        extraido = extrair_pdf(config['pdf'], metadados=config['metadados'])
        (pasta / 'extracao.json').write_text(json.dumps(extraido, ensure_ascii=False, indent=2), encoding='utf-8')
        with (pasta / 'paginas.jsonl').open('w', encoding='utf-8') as arquivo:
            for pagina in extraido['paginas']:
                arquivo.write(json.dumps(pagina, ensure_ascii=False) + '\\n')
        print('Páginas:', len(extraido['paginas']))
        print('Relatório:', json.dumps(extraido['relatorio'], ensure_ascii=False, indent=2))
        ''')
        """)
    md("inspecao-nota", """
        ## 4. Conferir a extração antes dos vetores

        Confira páginas e imagens, especialmente exemplos, tabelas e palavras
        partidas. No PDF já examinado, as páginas 54 e 55 tiveram extração pobre.
        Os candidatos a cabeçalho/rodapé são sugestões; somente os grupos que você
        confirmar serão excluídos do texto preparado. O original permanece no pacote.
        A paginação impressa não é inferida automaticamente pela página do PDF.
        """)
    code("inspecionar", """
        extraido = json.loads((PASTA_EXECUCAO / "extracao.json").read_text(encoding="utf-8"))
        selecionadas = PAGINAS_SELECIONADAS or list(range(1, len(extraido['paginas']) + 1))
        PAGINAS_PARA_CONFERIR = ([54, 55, 97, 98, 658, 659] if PDF_CUNHA_RECONHECIDO else
            [selecionadas[0], selecionadas[len(selecionadas) // 2], selecionadas[-1]])
        for numero in dict.fromkeys(PAGINAS_PARA_CONFERIR):
            pagina = extraido["paginas"][numero - 1]
            print("\\nPÁGINA DO PDF", numero, "— rótulo:", pagina.get("pagina_rotulo"))
            print(pagina["texto"][:3500])
        print("\\nExtração integral:", PASTA_EXECUCAO / "extracao.json")
        print("Confira outras páginas e exemplos antes de continuar.")
        """)
    code("ver-paginas", r"""
        MOSTRAR_IMAGENS_PDF = True
        if MOSTRAR_IMAGENS_PDF:
            (PASTA_INSPECAO / 'paginas_conferencia.json').write_text(json.dumps(PAGINAS_PARA_CONFERIR), encoding='utf-8')
            executar(r'''
            import pymupdf
            saida = pasta / 'imagens_conferencia'
            saida.mkdir(exist_ok=True)
            with pymupdf.open(config['pdf']) as pdf:
                for numero in json.loads((pasta / 'paginas_conferencia.json').read_text()):
                    if not 1 <= numero <= len(pdf):
                        raise ValueError('Página de conferência fora do PDF.')
                    pdf[numero - 1].get_pixmap(dpi=90).save(saida / f'pagina-{numero}.png')
            ''')
            from IPython.display import Image, display
            for numero in dict.fromkeys(PAGINAS_PARA_CONFERIR):
                print('Página PDF', numero)
                display(Image(filename=str(PASTA_INSPECAO / 'imagens_conferencia' / f'pagina-{numero}.png')))
        """)
    code("candidatos-margem", r"""
        grupos = extraido['relatorio'].get('candidatos_margem', [])
        for grupo in grupos:
            print(grupo['grupo_id'], '|', grupo['posicao'], '| ocorrências:', grupo['ocorrencias'],
                  '| páginas:', grupo['paginas'][:12])
            print('  Amostras:', json.dumps(grupo['amostras'][:2], ensure_ascii=False))
        print('Copie abaixo os IDs dos grupos que são realmente cabeçalhos/rodapés.')
        print('Não selecione títulos, notas ou exemplos que pertençam ao conteúdo.')
        """)
    code("selecionar-margens", r"""
        GRUPOS_MARGEM_CONFIRMADOS = []  # Ex.: ['margem-...']; copie os IDs mostrados acima.
        IDS_BLOCOS_EXCLUIR = []        # Opcional: exclusões individuais explicitamente conferidas.
        por_grupo = {g['grupo_id']: g for g in grupos}
        if set(GRUPOS_MARGEM_CONFIRMADOS) - por_grupo.keys():
            raise ValueError('ID de grupo desconhecido; use os IDs desta extração.')
        ids_excluir = sorted(set(IDS_BLOCOS_EXCLUIR).union(*[
            set(por_grupo[g]['ids_blocos']) for g in GRUPOS_MARGEM_CONFIRMADOS]))
        config = {**CONFIG_BASE, 'paginas_incluir': PAGINAS_SELECIONADAS, 'ids_excluir': ids_excluir}
        CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
        executar(r'''
        from gramatica_pdf import preparar_extracao
        extraido = json.loads((pasta / 'extracao.json').read_text(encoding='utf-8'))
        preparado = preparar_extracao(extraido, paginas_incluir=config['paginas_incluir'],
            ids_excluir=config['ids_excluir'])
        (pasta / 'preparacao_previa.json').write_text(json.dumps(preparado, ensure_ascii=False), encoding='utf-8')
        ''')
        previa = json.loads((PASTA_INSPECAO / 'preparacao_previa.json').read_text(encoding='utf-8'))
        candidatos = previa['preparacao'].get('candidatos_hifenizacao', [])
        for candidato in candidatos[:100]:
            print(candidato['palavra_recomposta'], '| páginas:', candidato['paginas'],
                  '| antes:', repr(candidato['antes']))
        print('Candidatos a junção:', len(candidatos), '(lista integral na preparação prévia).')
        print('Confirme somente palavras partidas pela diagramação; preserve compostos e notação gramatical.')
        """)
    code("preparar", r"""
        RECOMPOR_PALAVRAS = []  # Copie formas unidas conferidas: por exemplo 'integrantes'.
        import shutil
        opcoes = {'paginas_incluir': PAGINAS_SELECIONADAS, 'ids_excluir': ids_excluir,
                  'recompor_palavras': sorted(set(RECOMPOR_PALAVRAS))}
        assinatura = {k: CONFIG_BASE[k] for k in ('modelo', 'revisao', 'prefixo', 'alvo_tokens',
                      'limite_tokens', 'hashes_fontes', 'metadados')}
        assinatura['preparacao'] = opcoes
        chave = hashlib.sha256(json.dumps(assinatura, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
        PASTA_EXECUCAO = PASTA_BASE / PDF_SHA256[:16] / ('exec-' + chave)
        PASTA_EXECUCAO.mkdir(parents=True, exist_ok=True)
        for nome in ('extracao.json', 'paginas.jsonl'):
            shutil.copyfile(PASTA_INSPECAO / nome, PASTA_EXECUCAO / nome)
        config = {**CONFIG_BASE, **opcoes, 'pasta_execucao': str(PASTA_EXECUCAO)}
        CONFIG_PATH = PASTA_EXECUCAO / 'config_notebook.json'
        CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
        EXTRACAO_CONFERIDA = False
        FRAGMENTOS_CONFERIDOS = False
        executar(r'''
        from gramatica_pdf import preparar_extracao
        extraido = json.loads((pasta / 'extracao.json').read_text(encoding='utf-8'))
        preparado = preparar_extracao(extraido, paginas_incluir=config['paginas_incluir'],
            ids_excluir=config['ids_excluir'], recompor_palavras=config['recompor_palavras'])
        (pasta / 'preparacao.json').write_text(json.dumps(preparado, ensure_ascii=False), encoding='utf-8')
        p = preparado['preparacao']
        print('Páginas incluídas:', len(p['paginas_incluidas']))
        print('Exclusões registradas:', len(p['exclusoes']), '| normalizações:', len(p['normalizacoes']))
        print('Candidatos de hifenização para conferir:', len(p['candidatos_hifenizacao']))
        for unidade in p['unidades'][:3]:
            print('\nAMOSTRA PREPARADA:', unidade['texto'][:1600])
        print('Preparação integral:', pasta / 'preparacao.json')
        ''')
        print('Pasta dos vetores e checkpoints:', PASTA_EXECUCAO)
        """)
    md("conferencia-nota", """
        Após conferir a extração, altere a célula abaixo para `True` e execute-a.
        A célula de vetorização interrompe antes de carregar o E5 se essa conferência
        não estiver marcada. Se encontrar erros, preserve o relatório e revise a
        extração antes de gerar o acervo.
        """)
    code("conferir", "EXTRACAO_CONFERIDA = False  # Altere para True depois da conferência.")
    code("fragmentar", """
        if not EXTRACAO_CONFERIDA:
            raise RuntimeError("Confira a extração e marque EXTRACAO_CONFERIDA = True antes de continuar.")
        executar(r'''
        from transformers import AutoTokenizer
        from gramatica_pdf import fragmentar_livro
        tokenizador = AutoTokenizer.from_pretrained(config['modelo'], revision=config['revisao'],
            cache_dir=config['cache_modelos'], trust_remote_code=False)
        extraido = json.loads((pasta / 'preparacao.json').read_text(encoding='utf-8'))
        if extraido['obra']['sha256_pdf'] != config['pdf_sha256']:
            raise ValueError('O PDF extraído não corresponde ao arquivo identificado. Reexecute a preparação.')
        resultado = fragmentar_livro(extraido, tokenizador, alvo_tokens=config['alvo_tokens'],
            max_tokens=config['limite_tokens'], prefixo=config['prefixo'])
        fragmentos = resultado['fragmentos']
        if not fragmentos:
            raise ValueError('Nenhum fragmento foi produzido. Confira a extração.')
        (pasta / 'fragmentos_preparados.json').write_text(json.dumps(fragmentos, ensure_ascii=False), encoding='utf-8')
        obra = {**extraido['obra'], **config['metadados'], 'pdf_sha256': config['pdf_sha256']}
        (pasta / 'obra.json').write_text(json.dumps(obra, ensure_ascii=False, indent=2), encoding='utf-8')
        relatorio = {'extracao': extraido['relatorio'], 'fragmentacao': resultado['relatorio'],
            'preparacao': {k: v for k, v in extraido['preparacao'].items() if k != 'unidades'},
            'conferencia_extracao': 'confirmada_no_notebook', 'paginas_impressas': 'nao_conferidas'}
        (pasta / 'relatorio_extracao.json').write_text(json.dumps(relatorio, ensure_ascii=False, indent=2), encoding='utf-8')
        print('Fragmentos preparados:', len(fragmentos))
        from collections import Counter
        tokens = [f['tokens_embedding'] for f in fragmentos]
        contagens = Counter(f['texto_embedding'] for f in fragmentos)
        auditoria = {'fragmentos': len(fragmentos), 'tokens_min': min(tokens), 'tokens_max': max(tokens),
            'fragmentos_menos_32_tokens': sum(n < 32 for n in tokens),
            'entradas_repetidas_excedentes': sum(n - 1 for n in contagens.values()),
            'avisos_fragmentacao': resultado['relatorio'].get('avisos', []),
            'conferencia_fragmentos': 'pendente'}
        (pasta / 'auditoria_fragmentos.json').write_text(json.dumps(auditoria, ensure_ascii=False, indent=2), encoding='utf-8')
        print('Auditoria:', json.dumps(auditoria, ensure_ascii=False, indent=2)[:6000])
        print('Fragmentos mais curtos — confira se há regra ou exemplo, e não só título:')
        for item in sorted(fragmentos, key=lambda f: f['tokens_embedding'])[:12]:
            print(item['id'], '|', item['tokens_embedding'], '|', repr(item['texto'][:300]), '|', item['origem'])
        for item in fragmentos[:3]:
            print('\\n', item['id'], '| tokens:', item['tokens_embedding'])
            print(item['texto_embedding'][:1400])
        ''')
        """)
    md("fragmentos-nota", """
        Confira os fragmentos mais curtos e alguns trechos de adjetivas, concessivas
        e morfologia em `fragmentos_preparados.json`. Os títulos reconhecidos são
        agrupados com conteúdo; layouts ambíguos continuam exigindo conferência.
        Verifique também se regra, exemplo e ressalva precisam de um vizinho.
        Após conferir, mude a célula abaixo para `True`. Alterar páginas ou correções
        cria outra pasta de execução, preservando os vetores anteriores.
        """)
    code("conferir-fragmentos", "FRAGMENTOS_CONFERIDOS = False  # Altere para True após conferir os recortes.")
    md("vetorizar-nota", """
        ## 5. Gerar os vetores E5 na GPU

        Entradas já incluem `passage: ` e são conferidas sem truncamento.
        A inferência é float32, com mean pooling e saída L2 de 1.024 dimensões.
        Falta de memória reduz o lote; outros erros interrompem sem publicar um
        pacote completo. Reexecutar esta célula retoma lotes válidos na mesma pasta.
        """)
    code("vetorizar", """
        if not EXTRACAO_CONFERIDA or not FRAGMENTOS_CONFERIDOS:
            raise RuntimeError("Confira extração e fragmentos antes de carregar o E5.")
        executar(r'''
        import importlib.metadata as metadata
        import platform
        import datetime
        import torch
        from sentence_transformers import SentenceTransformer
        from gramatica_acervo import vetorizar_fragmentos
        if not torch.cuda.is_available():
            raise RuntimeError('GPU CUDA indisponível. Selecione GPU no ambiente do Colab.')
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
        modelo = SentenceTransformer(config['modelo'], revision=config['revisao'], device='cuda',
            cache_folder=config['cache_modelos'], trust_remote_code=False)
        modelo.float().eval()
        modelo.max_seq_length = config['limite_tokens']
        if modelo.get_sentence_embedding_dimension() != 1024:
            raise ValueError('O modelo não produziu o espaço E5 de 1024 dimensões.')
        pooling = [m.get_config_dict() for m in modelo if type(m).__name__ == 'Pooling']
        if len(pooling) != 1 or not pooling[0].get('pooling_mode_mean_tokens') or any(
            valor for chave, valor in pooling[0].items()
            if chave.startswith('pooling_mode_') and chave != 'pooling_mode_mean_tokens'):
            raise ValueError('Pooling do modelo incompatível com o perfil de referência.')
        ambiente = {'python': platform.python_version(), 'torch': torch.__version__,
            'cuda': torch.version.cuda,
            'bibliotecas': {n: metadata.version(n) for n in
                ('sentence-transformers', 'transformers', 'huggingface-hub', 'numpy', 'PyMuPDF', 'sentencepiece')}}
        tentativa = {'inicio_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'ambiente': ambiente, 'gpu': torch.cuda.get_device_name(0),
            'vram_bytes': torch.cuda.get_device_properties(0).total_memory,
            'conferencia_extracao': True, 'conferencia_fragmentos': True}
        with (pasta / 'historico_execucao.jsonl').open('a', encoding='utf-8') as arquivo:
            arquivo.write(json.dumps(tentativa, ensure_ascii=False) + '\\n')
        perfil = {'modelo': config['modelo'], 'revisao': config['revisao'],
            'tokenizador': {'identificacao': config['modelo'], 'revisao': config['revisao']},
            'pooling': 'mean', 'pooling_configuracao': pooling, 'prefixo': config['prefixo'],
            'dimensao': 1024, 'normalizacao': 'l2', 'formato_armazenamento': 'float32',
            'precisao_inferencia': 'float32', 'limite_tokens': config['limite_tokens'],
            'pdf_sha256': config['pdf_sha256'], 'hashes_fontes': config['hashes_fontes'],
            'ambiente': ambiente, 'tf32': False}
        fragmentos = json.loads((pasta / 'fragmentos_preparados.json').read_text(encoding='utf-8'))
        auditoria_path = pasta / 'auditoria_fragmentos.json'
        auditoria = json.loads(auditoria_path.read_text(encoding='utf-8'))
        auditoria['conferencia_fragmentos'] = 'confirmada_no_notebook'
        auditoria_path.write_text(json.dumps(auditoria, ensure_ascii=False, indent=2), encoding='utf-8')
        matriz = vetorizar_fragmentos(fragmentos, modelo, pasta, perfil,
            lote_inicial=config['lote_inicial'], progresso=lambda evento: print(json.dumps(evento), flush=True))
        print('Matriz concluída:', matriz)
        print('Ambiente:', json.dumps(ambiente, ensure_ascii=False, indent=2))
        ''')
        """)
    md("consultas-nota", """
        ## 6. Inspecionar a recuperação (opcional)

        Estas consultas ajudam a conferir o acervo. Não são uma avaliação de acurácia.
        Ajuste os assuntos ao livro e confira as passagens e suas páginas. Esta célula
        gera consultas com `query: ` na GPU e combina E5 com busca lexical BM25.
        Resultados repetidos são agrupados, e vizinhos da mesma seção acompanham
        a passagem para inspeção. O acervo permanece independente do aplicativo.
        """)
    code("consultas", """
        CONSULTAS_TESTE = ['oração subordinada adjetiva restritiva e explicativa',
                          'oração adverbial concessiva introduzida por embora',
                          'objeto direto e objeto indireto',
                          'modo subjuntivo e flexão verbal',
                          'pronomes pessoais átonos e colocação pronominal']
        (PASTA_EXECUCAO / 'consultas_teste.json').write_text(json.dumps(CONSULTAS_TESTE, ensure_ascii=False), encoding='utf-8')
        executar(r'''
        import numpy as np
        import re
        import torch
        from rank_bm25 import BM25Okapi
        from sentence_transformers import SentenceTransformer
        from gramatica_acervo import validar_acervo
        validacao = validar_acervo(pasta)
        if not validacao['valido']:
            raise ValueError(validacao)
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
        modelo = SentenceTransformer(config['modelo'], revision=config['revisao'], device='cuda',
            cache_folder=config['cache_modelos'], trust_remote_code=False)
        modelo.float().eval()
        modelo.max_seq_length = config['limite_tokens']
        consultas = json.loads((pasta / 'consultas_teste.json').read_text(encoding='utf-8'))
        entradas = ['query: ' + texto for texto in consultas]
        if any(len(modelo.tokenizer.encode(t, add_special_tokens=True, truncation=False)) > config['limite_tokens'] for t in entradas):
            raise ValueError('Consulta de inspeção longa demais; reduza o texto sem truncar silenciosamente.')
        vetores = modelo.encode(entradas, prompt='', normalize_embeddings=True, convert_to_numpy=True,
            show_progress_bar=False, batch_size=8)
        np.save(pasta / 'vetores_consultas.npy', vetores.astype(np.float32), allow_pickle=False)
        matriz = np.load(pasta / 'vetores.npy', mmap_mode='r', allow_pickle=False)
        fragmentos = [json.loads(linha) for linha in (pasta / 'fragmentos.jsonl').read_text(encoding='utf-8').splitlines()]
        def termos(texto):
            return re.findall(r'[^\\W_]+', texto.casefold())
        bm25 = BM25Okapi([termos(f['texto']) for f in fragmentos])
        resultados = []
        for consulta, vetor in zip(consultas, vetores):
            scores = matriz @ vetor.astype(np.float32)
            scores_lexicais = np.asarray(bm25.get_scores(termos(consulta)))
            rankings = [np.argsort(-scores, kind='stable')[:32]]
            if np.any(scores_lexicais != 0):
                rankings.append(np.argsort(-scores_lexicais, kind='stable')[:32])
            fusao = {}
            for ranking in rankings:
                for posicao, i in enumerate(ranking, start=1):
                    fusao[int(i)] = fusao.get(int(i), 0.0) + 1 / (60 + posicao)
            indices = sorted(fusao, key=lambda i: (-fusao[i], -float(scores[i]), i))
            itens, vistos = [], set()
            for i in indices:
                f = fragmentos[i]
                chave = ' '.join(f['texto'].casefold().split())
                if chave in vistos:
                    continue
                vistos.add(chave)
                vizinhos = [{'fragmento_id': fragmentos[j]['id'], 'texto': fragmentos[j]['texto'],
                    'origem': fragmentos[j]['origem']} for j in (i - 1, i + 1)
                    if 0 <= j < len(fragmentos) and fragmentos[j]['secao'] == f['secao']]
                itens.append({'fragmento_id': f['id'], 'linha_vetor': i,
                    'similaridade_cosseno': float(scores[i]), 'score_rrf': fusao[i],
                    'score_bm25': float(scores_lexicais[i]),
                    'texto': f['texto'], 'origem': f['origem'], 'vizinhos': vizinhos})
                if len(itens) == 5:
                    break
            resultados.append({'consulta': consulta, 'resultados': itens})
            print('\\nCONSULTA:', consulta)
            for item in itens:
                print(item['score_rrf'], item['fragmento_id'], item['texto'][:900], 'Origem:', item['origem'])
        (pasta / 'avaliacao_recuperacao.json').write_text(json.dumps({
            'tipo': 'inspecao_sem_referencia_manual',
            'limite': 'Similaridade não é confiança gramatical nem acurácia.',
            'consultas': resultados}, ensure_ascii=False, indent=2), encoding='utf-8')
        ''')
        """)
    md("exportacao-nota", """
        ## 7. Conferir e baixar o pacote

        O pacote inclui textos e suas origens, vetores, perfil, relatório de extração,
        estado da execução e manifesto de hashes. PDF, pesos e checkpoints ficam fora
        do ZIP. Rótulos de páginas não são certificação de paginação impressa.
        Guarde o ZIP; ele será usado na futura integração do acervo no aplicativo.
        """)
    code("exportar", """
        executar(r'''
        import shutil
        from gramatica_acervo import validar_acervo, exportar_acervo
        validacao = validar_acervo(pasta)
        if not validacao['valido']:
            raise ValueError(validacao)
        print(json.dumps(validacao, ensure_ascii=False, indent=2))
        fontes = pasta / 'codigo'
        fontes.mkdir(exist_ok=True)
        for nome in ('gramatica_pdf.py', 'gramatica_acervo.py'):
            shutil.copyfile(nome, fontes / nome)
        destino = exportar_acervo(pasta)
        (pasta / 'pacote_download.json').write_text(json.dumps({'caminho': str(destino)}), encoding='utf-8')
        print('Pacote validado:', destino)
        ''')
        """)
    code("baixar", """
        from google.colab import files
        pacote = json.loads((PASTA_EXECUCAO / 'pacote_download.json').read_text(encoding='utf-8'))['caminho']
        files.download(pacote)
        print('Download solicitado:', pacote)
        print('Guarde também o PDF original e mantenha a pasta de trabalho se precisar retomar.')
        """)
    return {"nbformat": 4, "nbformat_minor": 5, "metadata": {
        "accelerator": "GPU", "colab": {"name": "Vetorizar livro de gramática — E5 A100.ipynb", "provenance": []},
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python"}}, "cells": cells}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--saida", type=Path, default=BASE / "notebooks" / "vetorizar_livro_gramatica_colab.ipynb")
    args = parser.parse_args()
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    args.saida.write_text(json.dumps(gerar_notebook_gramatica(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.saida)


if __name__ == "__main__":
    main()
