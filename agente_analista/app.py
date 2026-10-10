"""Servidor local separado do Lingua, com uma busca de cada vez e progresso."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import sqlite3
from threading import Lock
from time import monotonic
from urllib.parse import urlsplit
from uuid import uuid4

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from .entrada import ErroEntrada, validar_relato
from .importacao import ErroImportacao, conferir_compatibilidade, importar_vetorizacao
from .persistencia import BancoAnalista, ErroPersistencia


RAIZ = Path(__file__).resolve().parents[1]


class ServicoAnalista:
    """Pesquise no índice usando os vetores recebidos da análise linguística."""

    def __init__(self, pasta=None):
        self.pasta = Path(pasta or RAIZ / "agente_analista/data/Vetor")
        self.corpus = None
        self.buscador = None
        self._lock = Lock()

    def _corpus(self):
        from .corpus import Corpus
        with self._lock:
            if self.corpus is None:
                self.corpus = Corpus(self.pasta)
            return self.corpus

    def status(self):
        problemas = []
        try:
            corpus = self._corpus()
            contagens = {"fragmentos": len(corpus.fragmentos), "blocos": len(corpus.blocos)}
        except (ValueError, ImportError) as erro:
            contagens = {}
            problemas.append(str(erro) if isinstance(erro, ValueError) else
                             "Instale as dependências de agente_analista/requirements.txt.")
        return {"pronto": not problemas, "problemas": problemas, "corpus": contagens,
                "entrada": "vetorizacao_importada", "inferencia_local": False}

    def executar(self, relato, progresso, *, provedor, modelo, chave_api, vetorizacao):
        from .busca import Buscador
        from .ligacoes import ErroLigacoes, avaliar_ligacoes
        from .modelos import ErroModelo, escolher_formato_resposta
        progresso("Conferindo o modelo das justificativas")
        try:
            modo_resposta = escolher_formato_resposta(provedor, modelo)
        except ErroModelo as erro:
            raise ErroLigacoes(str(erro)) from None
        if relato["texto"] != vetorizacao["relato"]["texto"]:
            raise ErroImportacao("O relato difere do texto da vetorização importada.")
        progresso("Conferindo o índice e a vetorização recebida")
        corpus = self._corpus()
        if self.buscador is None:
            self.buscador = Buscador(corpus)
        recuperacao = self.buscador.buscar(vetorizacao, progresso=progresso)
        progresso("Avaliando os candidatos e conferindo as citações")
        ligacoes = avaliar_ligacoes(relato, recuperacao, provedor=provedor, modelo=modelo,
                                   chave_api=chave_api, modo_resposta=modo_resposta)
        return {"relato": relato, **recuperacao, **ligacoes,
                "justificativas": {"provedor": provedor, "modelo": modelo, "formato_resposta": modo_resposta},
                "posicoes": "Pontos de código Unicode, início inclusivo e fim exclusivo [inicio, fim).",
                "aviso": "Pontuações ordenam candidatos; a pertinência interpretativa exige revisão."}


def criar_app(config=None, *, servico=None):
    app = Flask(__name__)
    app.config.update(
        MAX_CONTENT_LENGTH=32 * 1024 * 1024,
        DATABASE=os.environ.get("AGENTE_ANALISTA_DB", str(RAIZ / "instance/agente_analista.sqlite3")),
    )
    if config:
        app.config.update(config)
    servico = servico or ServicoAnalista()
    banco = BancoAnalista(app.config["DATABASE"])
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agente-analista")
    lock = Lock()
    tarefas = {}
    estado = {"ativa": None, "recuperado": False}
    app.extensions["agente_analista"] = {
        "servico": servico, "executor": executor, "tarefas": tarefas, "banco": banco,
    }

    @app.before_request
    def somente_local():
        hostname = urlsplit("http://" + request.host).hostname
        if hostname not in ("localhost", "127.0.0.1", "::1"):
            return jsonify(erro="Acesse a página pelo endereço local 127.0.0.1 ou localhost."), 403
        if request.method == "POST":
            origem = request.headers.get("Origin")
            if origem and urlsplit(origem).netloc != request.host:
                return jsonify(erro="Abra a página local para iniciar a busca."), 403

    @app.before_request
    def recuperar_buscas():
        # Criar a app não comprova que ela conseguiu abrir a porta. Recuperar
        # aqui evita interromper o servidor ativo ao tentar iniciá-lo de novo.
        with lock:
            if not estado["recuperado"]:
                banco.interromper_buscas()
                estado["recuperado"] = True

    @app.after_request
    def sem_cache(resposta):
        resposta.headers["Cache-Control"] = "no-store"
        resposta.headers["X-Content-Type-Options"] = "nosniff"
        resposta.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        return resposta

    @app.get("/")
    def pagina():
        return render_template("index.html")

    @app.get("/api/status")
    def status():
        with lock:
            limpar_expiradas()
        return jsonify(servico.status())

    def manifesto_indice():
        pasta = Path(getattr(servico, "pasta", RAIZ / "agente_analista/data/Vetor"))
        try:
            return json.loads((pasta / "manifesto.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ErroImportacao("Restaure o manifesto do índice de Freud antes de importar a vetorização.") from None

    def carregar_vetorizacao(identificador):
        if not isinstance(identificador, str) or re.fullmatch(r"[0-9a-f]{64}", identificador) is None:
            raise ErroImportacao("Importe o arquivo vetorizacao.json da análise linguística antes de buscar.")
        registro = banco.obter_vetorizacao(identificador)
        if registro is None:
            return None
        importada = importar_vetorizacao(registro)
        conferir_compatibilidade(importada, manifesto_indice())
        return importada

    def resumo_vetorizacao(identificador, importada):
        contagens = {tipo: sum(c["tipo"] == tipo for c in importada["consultas"])
                     for tipo in ("periodo", "contextual", "documento")}
        return {"id": identificador, "relato": importada["relato"],
                "fonte": importada["fonte"], "contagens": contagens}

    @app.post("/api/vetorizacoes")
    def importar():
        if not request.is_json:
            return jsonify(erro="Envie a exportação vetorizacao.json em JSON."), 415
        dados = request.get_json(silent=True)
        if not isinstance(dados, dict):
            return jsonify(erro="Envie o JSON completo da vetorização da análise linguística."), 400
        importada = importar_vetorizacao(dados)
        conferir_compatibilidade(importada, manifesto_indice())
        identificador = banco.registrar_vetorizacao(dados)
        return jsonify(resumo_vetorizacao(identificador, importada)), 201

    @app.get("/api/vetorizacoes/<identificador>")
    def consultar_vetorizacao(identificador):
        importada = carregar_vetorizacao(identificador)
        if importada is None:
            return jsonify(erro="Vetorização não encontrada. Importe o arquivo novamente."), 404
        return jsonify(resumo_vetorizacao(identificador, importada))

    @app.post("/api/relatos")
    def registrar_relato():
        if not request.is_json:
            return jsonify(erro="Envie o relato em JSON."), 415
        dados = request.get_json(silent=True)
        if not isinstance(dados, dict) or not isinstance(dados.get("texto"), str):
            return jsonify(erro="Envie o relato como texto."), 400
        try:
            relato = validar_relato(dados["texto"])
        except ErroEntrada as erro:
            return jsonify(erro=str(erro), contagem=erro.contagem), 400
        vetorizacao_id = dados.get("vetorizacao_id")
        if "vetorizacao_id" in dados:
            importada = carregar_vetorizacao(vetorizacao_id)
            if importada is None:
                return jsonify(erro="Vetorização não encontrada."), 404
            if importada["relato"]["texto"] != relato["texto"]:
                raise ErroImportacao("O texto foi alterado após a vetorização. Importe a exportação correspondente ao relato.")
        relato_id = banco.registrar(relato, vetorizacao_id=vetorizacao_id)
        return jsonify(banco.obter(relato_id)), 201

    @app.get("/api/relatos")
    def listar_relatos():
        try:
            limite = int(request.args.get("limite", "20"))
            antes_id = int(request.args["antes_id"]) if "antes_id" in request.args else None
            if not 1 <= limite <= 100 or (antes_id is not None and antes_id < 1):
                raise ValueError
        except ValueError:
            return jsonify(erro="Informe limite de 1 a 100 e antes_id positivo."), 400
        return jsonify(banco.listar(limite, antes_id))

    @app.get("/api/relatos/<int:relato_id>")
    def consultar_relato(relato_id):
        relato = banco.obter(relato_id)
        if relato is None:
            return jsonify(erro="Relato não encontrado."), 404
        return jsonify(relato)

    def limpar_expiradas():
        agora = monotonic()
        for id in list(tarefas):
            if id != estado["ativa"] and agora - tarefas[id]["criada"] > 3600:
                del tarefas[id]

    def executar(tarefa_id, relato, configuracao_api, vetorizacao):
        def progresso(etapa):
            with lock:
                tarefas[tarefa_id]["etapa"] = etapa
        try:
            resultado = servico.executar(relato, progresso, vetorizacao=vetorizacao, **configuracao_api)
            resultado = banco.concluir(tarefa_id, resultado)
            with lock:
                tarefas[tarefa_id].update(estado="concluido", etapa="Busca concluída", resultado=resultado)
        except Exception as erro:
            from .corpus import ErroCorpus
            from .ligacoes import ErroLigacoes
            # A busca pode estar indisponível por dependências ausentes. Ainda
            # assim, o erro deve encerrar a tarefa e preservar o relato salvo.
            try:
                from .busca import ErroBusca
                erros_busca = (ErroBusca,)
            except ImportError:
                erros_busca = ()
            conhecidos = (ErroEntrada, ErroCorpus, ErroLigacoes, ErroPersistencia, ErroImportacao) + erros_busca
            mensagem = str(erro) if isinstance(erro, conhecidos) else "Não foi possível concluir a busca. Confira o ambiente e tente novamente."
            if isinstance(erro, sqlite3.Error):
                mensagem = "Não foi possível salvar o resultado no banco local. O relato continua disponível no histórico."
            if configuracao_api["chave_api"] in mensagem:
                mensagem = "Não foi possível concluir a avaliação. Confira a chave, o provedor e o modelo na página."
            app.logger.error("Busca não concluída (%s)", type(erro).__name__)
            try:
                banco.falhar(tarefa_id, mensagem)
            except sqlite3.Error:
                app.logger.error("Não foi possível gravar o estado da busca no banco local.")
            with lock:
                tarefas[tarefa_id].update(estado="erro", etapa="Busca interrompida", erro=mensagem)
        finally:
            configuracao_api.clear()
            with lock:
                estado["ativa"] = None

    @app.post("/api/buscas")
    def iniciar():
        if not request.is_json:
            return jsonify(erro="Envie o relato em JSON."), 415
        dados = request.get_json(silent=True)
        if not isinstance(dados, dict) or not isinstance(dados.get("texto"), str):
            return jsonify(erro="Envie o relato como texto."), 400
        try:
            relato = validar_relato(dados["texto"])
        except ErroEntrada as erro:
            return jsonify(erro=str(erro), contagem=erro.contagem), 400
        relato_id = dados.get("relato_id")
        if "relato_id" in dados and (type(relato_id) is not int or relato_id < 1):
            return jsonify(erro="relato_id deve ser um identificador inteiro positivo."), 400
        from .transporte import ErroAPINarrativa, _obter_chave_api
        from .ligacoes import ErroLigacoes, _configuracao
        if not isinstance(dados.get("chave_api"), str) or not dados["chave_api"].strip():
            return jsonify(erro="Informe a chave de API na página para gerar as justificativas."), 400
        if not isinstance(dados.get("modelo"), str) or not dados["modelo"].strip():
            return jsonify(erro="Adicione e selecione um modelo para as justificativas."), 400
        if dados.get("provedor") not in ("openrouter", "openai"):
            return jsonify(erro="Selecione OpenRouter ou OpenAI como provedor das justificativas."), 400
        try:
            provedor, modelo = _configuracao(dados["provedor"], dados["modelo"])
            chave = _obter_chave_api(dados["chave_api"])
        except (ErroAPINarrativa, ErroLigacoes):
            return jsonify(erro="Confira a chave de API e o identificador do modelo informado na página."), 400
        vetorizacao_id = dados.get("vetorizacao_id")
        vetorizacao = carregar_vetorizacao(vetorizacao_id)
        if vetorizacao is None:
            return jsonify(erro="Vetorização não encontrada. Importe o arquivo novamente."), 404
        if relato["texto"] != vetorizacao["relato"]["texto"]:
            raise ErroImportacao("O texto foi alterado após a vetorização. Importe a exportação correspondente ao relato.")
        configuracao = servico.status()
        if not configuracao["pronto"]:
            return jsonify(erro=" ".join(configuracao["problemas"]), problemas=configuracao["problemas"]), 503
        with lock:
            if estado["ativa"]:
                return jsonify(erro="Uma busca já está em andamento. Aguarde sua conclusão."), 409
            # O cache de progresso é limitado; relatos e resultados ficam no SQLite.
            limpar_expiradas()
            agora = monotonic()
            for antiga in list(tarefas):
                if len(tarefas) >= 8:
                    del tarefas[antiga]
            tarefa_id = str(uuid4())
            try:
                relato_id = banco.iniciar_busca(relato, tarefa_id, provedor, modelo, relato_id,
                                               vetorizacao_id=vetorizacao_id)
            except LookupError:
                return jsonify(erro="Relato não encontrado."), 404
            except FileExistsError as erro:
                return jsonify(erro=str(erro)), 409
            except ErroPersistencia as erro:
                return jsonify(erro=str(erro)), 400
            tarefas[tarefa_id] = {
                "id": tarefa_id, "relato_id": relato_id, "estado": "executando",
                "etapa": "Preparando a busca", "criada": agora,
            }
            estado["ativa"] = tarefa_id
        executor.submit(executar, tarefa_id, relato, {"provedor": provedor, "modelo": modelo, "chave_api": chave}, vetorizacao)
        return jsonify(id=tarefa_id, relato_id=relato_id), 202

    @app.get("/api/buscas/<tarefa_id>")
    def consultar(tarefa_id):
        with lock:
            limpar_expiradas()
            tarefa = tarefas.get(tarefa_id)
            if tarefa is None:
                tarefa = banco.obter_busca(tarefa_id)
            if tarefa is None:
                return jsonify(erro="Busca não encontrada."), 404
            return jsonify({k: v for k, v in tarefa.items() if k != "criada"})

    @app.errorhandler(sqlite3.Error)
    def erro_banco(erro):
        app.logger.error("Banco local indisponível (%s)", type(erro).__name__)
        return jsonify(erro="Não foi possível acessar o banco local. Confira o armazenamento e tente novamente."), 503

    @app.errorhandler(ErroImportacao)
    def erro_importacao(erro):
        return jsonify(erro=str(erro)), 400

    @app.errorhandler(HTTPException)
    def erro_http(erro):
        mensagem = "O arquivo excedeu o limite de 32 MiB para envio." if erro.code == 413 else "Pedido inválido ou endereço não encontrado."
        return jsonify(erro=mensagem), erro.code

    return app


if __name__ == "__main__":
    criar_app().run(host="127.0.0.1", port=5002, debug=False, use_reloader=False)
