"""Importação, busca e histórico usando somente vetores já produzidos pelo Lingua."""

from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from agente_analista.app import ServicoAnalista, criar_app
from fixtures_importacao import TEXTO
from test_agente_analista_importacao import construir_exportacao


RAIZ = Path(__file__).resolve().parents[2]
CHAVE = "chave-transitoria-nao-persistida-importacao"


class ServicoVetoresImportados:
    """Simula só a avaliação; não produz vetores nem chama provedores externos."""

    def __init__(self):
        self.chamadas = []
        self.pronto = True
        self.manifesto = json.loads(
            (RAIZ / "agente_analista/data/Vetor/manifesto.json").read_text(encoding="utf-8")
        )

    def _corpus(self):
        return SimpleNamespace(manifesto=self.manifesto)

    def status(self):
        return {
            "pronto": self.pronto,
            "problemas": [] if self.pronto else ["A matriz de fragmentos do corpus está ausente."],
        }

    def executar(self, relato, progresso, *, provedor, modelo, chave_api, vetorizacao):
        self.chamadas.append({"relato": deepcopy(relato), "vetorizacao": vetorizacao})
        progresso("Consumindo vetores importados")
        return {
            "relato": relato, "ligacoes": [], "descartadas": [], "rejeitadas": [],
            "consultas": [], "candidatos": [], "mensagem": "Nenhuma relação sustentada neste teste.",
            "justificativas": {"provedor": provedor, "modelo": modelo},
            "chave_api": chave_api,
        }


class FluxoImportadoAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exportacao = construir_exportacao(dimensao=1024, limite=512)

    def setUp(self):
        self.temporario = TemporaryDirectory()
        self.caminho = Path(self.temporario.name) / "agente.sqlite3"
        self.apps = []
        self.servico = ServicoVetoresImportados()
        # Nem a inicialização da app nem a importação podem iniciar outra
        # inferência. As representações são calculadas pela fixture uma vez.
        self.gerador = Mock(side_effect=AssertionError("O agente não deve gerar novos embeddings."))
        self.adaptador = Mock(side_effect=AssertionError("O agente não deve executar o produtor."))
        # Sentinelas locais: a suíte não importa fábricas de outro projeto.
        self.enterContext(patch.dict(sys.modules, {
            "embeddings_e5": SimpleNamespace(criar_gerador_padrao=self.gerador),
            "modelo_embeddings": SimpleNamespace(criar_adaptador=self.adaptador),
        }))
        self.app = self.nova_app()
        self.client = self.app.test_client()

    def tearDown(self):
        for app in self.apps:
            app.extensions["agente_analista"]["executor"].shutdown(wait=True)
        self.temporario.cleanup()

    def nova_app(self):
        app = criar_app({"TESTING": True, "DATABASE": str(self.caminho)}, servico=self.servico)
        self.apps.append(app)
        return app

    def importar(self, exportacao=None):
        resposta = self.client.post(
            "/api/vetorizacoes", json=self.exportacao if exportacao is None else exportacao,
        )
        self.assertEqual(resposta.status_code, 201, resposta.get_json())
        return resposta.get_json()

    def buscar(self, **campos):
        dados = {
            "texto": TEXTO, "provedor": "openrouter", "modelo": "fabricante/modelo-teste",
            "chave_api": CHAVE,
        }
        dados.update(campos)
        return self.client.post("/api/buscas", json=dados)

    def aguardar(self, busca_id):
        limite = time.monotonic() + 5
        while time.monotonic() < limite:
            resposta = self.client.get(f"/api/buscas/{busca_id}")
            self.assertEqual(resposta.status_code, 200, resposta.get_json())
            tarefa = resposta.get_json()
            if tarefa["estado"] != "executando":
                return tarefa
            time.sleep(.01)
        self.fail("A busca importada de teste não terminou.")

    def test_importa_exportacao_completa_sem_inferencia_e_deduplica(self):
        importada = self.importar()
        self.assertTrue(importada["id"])
        self.assertEqual(importada["relato"]["texto"], TEXTO)
        self.assertIn("fonte", importada)
        self.assertIn("contagens", importada)
        repetida = self.importar(deepcopy(self.exportacao))
        self.assertEqual(repetida["id"], importada["id"])
        resposta = self.client.get(f"/api/vetorizacoes/{importada['id']}")
        self.assertEqual(resposta.status_code, 200, resposta.get_json())
        metadata = resposta.get_json()
        self.assertEqual(metadata["id"], importada["id"])
        self.assertEqual(metadata["relato"]["texto"], TEXTO)
        self.assertNotIn("artefatos", metadata)
        self.assertNotIn('"valores"', json.dumps(metadata))
        self.assertEqual(self.client.get("/api/relatos").get_json()["relatos"], [])
        self.gerador.assert_not_called()
        self.adaptador.assert_not_called()
        self.assertEqual(self.servico.chamadas, [])

    def test_busca_usa_importacao_e_preserva_vinculo_apos_reinicio(self):
        importada = self.importar()
        registrada = self.client.post(
            "/api/relatos", json={"texto": TEXTO, "vetorizacao_id": importada["id"]},
        )
        self.assertEqual(registrada.status_code, 201, registrada.get_json())
        relato_id = registrada.get_json()["id"]
        self.assertEqual(registrada.get_json()["vetorizacao_id"], importada["id"])
        resposta = self.buscar(vetorizacao_id=importada["id"], relato_id=relato_id)
        self.assertEqual(resposta.status_code, 202, resposta.get_json())
        busca_id = resposta.get_json()["id"]
        self.assertEqual(resposta.get_json()["relato_id"], relato_id)
        tarefa = self.aguardar(busca_id)
        self.assertEqual(tarefa["estado"], "concluido", tarefa)
        self.assertEqual(len(self.servico.chamadas), 1)
        self.assertEqual(self.servico.chamadas[0]["relato"]["texto"], TEXTO)
        vetorizacao = self.servico.chamadas[0]["vetorizacao"]
        self.assertEqual(len(vetorizacao["consultas"]), 11)
        self.assertEqual({consulta["tipo"] for consulta in vetorizacao["consultas"]},
                         {"periodo", "contextual", "documento"})
        artefatos = {artefato["id"]: artefato for artefato in self.exportacao["artefatos"]}
        for consulta in vetorizacao["consultas"]:
            self.assertEqual(consulta["vetor"], artefatos[consulta["artefato_id"]]["valores"])
            self.assertEqual(consulta["texto"], TEXTO[consulta["inicio"]:consulta["fim"]])
        self.gerador.assert_not_called()
        self.adaptador.assert_not_called()
        registro = self.client.get(f"/api/relatos/{relato_id}").get_json()
        self.assertEqual(registro["vetorizacao_id"], importada["id"])
        self.assertEqual(registro["texto"], TEXTO)
        self.assertNotIn(CHAVE, json.dumps(registro))
        self.app.extensions["agente_analista"]["executor"].shutdown(wait=True)
        reiniciada = self.nova_app().test_client()
        self.assertEqual(reiniciada.get(f"/api/relatos/{relato_id}").get_json(), registro)
        self.assertEqual(
            reiniciada.get(f"/api/vetorizacoes/{importada['id']}").get_json()["relato"]["texto"], TEXTO,
        )
        self.assertEqual(reiniciada.get(f"/api/buscas/{busca_id}").get_json()["estado"], "concluido")
        self.assertEqual(len(self.servico.chamadas), 1)
        for arquivo in self.caminho.parent.glob(self.caminho.name + "*"):
            if arquivo.is_file():
                self.assertNotIn(CHAVE.encode(), arquivo.read_bytes())

    def test_servico_real_busca_confere_e_salva_relacao_sem_nova_vetorizacao(self):
        fonte = "A árvore conserva a marca da estação anterior."
        contexto_fonte = fonte + " A continuidade não prova uma causa."
        outro = "Uma viagem pode ter seu itinerário alterado."
        documento = self.exportacao["representacoes"][-1]
        artefatos = {artefato["id"]: artefato for artefato in self.exportacao["artefatos"]}
        vetor_documento = np.asarray(artefatos[documento["artefato_id"]]["valores"], dtype=np.float32)
        fragmentos = [
            {"id": "F-arvore", "bloco_id": "B-arvore", "cabecalho": "Árvore", "texto": contexto_fonte},
            {"id": "F-viagem", "bloco_id": "B-viagem", "cabecalho": "Viagem", "texto": outro},
        ]
        blocos = {
            fragmento["bloco_id"]: {
                "bloco_id": fragmento["bloco_id"], "texto": fragmento["texto"],
                "fragmentos_ids": [fragmento["id"]],
                "fragmentos": [{"id": fragmento["id"], "inicio": 0, "fim": len(fragmento["texto"])}],
                "referencia": {"obra": "Fonte fictícia controlada do teste", "volume": 1, "paginas": [12]},
            }
            for fragmento in fragmentos
        }
        servico = ServicoAnalista()
        servico.corpus = SimpleNamespace(
            manifesto=self.servico.manifesto, fragmentos=fragmentos, blocos=blocos,
            vetores=np.stack((vetor_documento, -vetor_documento)),
        )
        app = criar_app({"TESTING": True, "DATABASE": str(self.caminho)}, servico=servico)
        self.apps.append(app)
        self.client = app.test_client()
        self.assertTrue(self.client.get("/api/status").get_json()["pronto"])
        citacao_relato = "Árvore\tverde."
        inicio = TEXTO.index(citacao_relato)
        proposta = {
            "paragrafo": "P1",
            "relato": {"inicio": inicio, "fim": inicio + len(citacao_relato), "texto": citacao_relato},
            "observacao": "O relato registra uma árvore verde.", "conceito": "",
            "bloco_id": "B-arvore", "fragmentos_ids": ["F-arvore"],
            "freud": {"inicio": 0, "fim": len(fonte), "texto": fonte},
            "ligacao": "A fonte permite examinar a descrição sem atribuir uma causa.",
            "justificativa": "A aproximação fica limitada à imagem da árvore registrada nos dois trechos.",
            "limites": "Este resultado controla a integração; não demonstra pertinência interpretativa real.",
            "alternativas": ["Uma descrição comum pode explicar a passagem."], "situacao": "parcial",
        }
        mensagens_enviadas = []

        def transporte_controlado(**pedido):
            mensagens = pedido["construtor"](**pedido["contexto"])
            mensagens_enviadas.append(json.loads(mensagens[1]["content"]))
            return json.dumps({"ligacoes": [proposta]}, ensure_ascii=False)

        with patch("agente_analista.modelos.escolher_formato_resposta", return_value="json_schema") as formato:
            with patch("agente_analista.ligacoes._enviar_mensagens", side_effect=transporte_controlado) as transporte:
                importada = self.importar()
                resposta = self.buscar(vetorizacao_id=importada["id"])
                self.assertEqual(resposta.status_code, 202, resposta.get_json())
                tarefa = self.aguardar(resposta.get_json()["id"])
                self.assertEqual(tarefa["estado"], "concluido", tarefa)
                formato.assert_called_once()
                transporte.assert_called_once()
        resultado = tarefa["resultado"]
        self.assertEqual(len(resultado["consultas"]), 11)
        self.assertEqual({c["bloco_id"] for c in resultado["candidatos"]}, {"B-arvore", "B-viagem"})
        self.assertEqual(resultado["metodo_fusao"]["consultas_por_tipo"],
                         {"periodo": 5, "contextual": 5, "documento": 1})
        candidato = next(c for c in resultado["candidatos"] if c["bloco_id"] == "B-arvore")
        consultas = {consulta["id"]: consulta for consulta in resultado["consultas"]}
        for pontuacao in candidato["pontuacoes"]:
            if pontuacao["metodo"] == "E5":
                consulta = consultas[pontuacao["consulta_id"]]
                vetor = np.asarray(artefatos[consulta["artefato_id"]]["valores"], dtype=np.float32)
                self.assertAlmostEqual(pontuacao["score"], float(vetor_documento @ vetor), places=6)
        self.assertEqual(len(resultado["ligacoes"]), 1)
        ligacao = resultado["ligacoes"][0]
        self.assertTrue(all(ligacao["conferencias"].values()))
        self.assertEqual(ligacao["relato"]["texto"], citacao_relato)
        self.assertEqual(ligacao["freud"]["texto"], fonte)
        self.assertEqual(ligacao["referencia"], blocos["B-arvore"]["referencia"])
        registro = self.client.get(f"/api/relatos/{resposta.get_json()['relato_id']}").get_json()
        self.assertEqual(registro["vetorizacao_id"], importada["id"])
        self.assertEqual(registro["resultado"]["fonte_vetorizacao"]["execucao_id"], self.exportacao["execucao_id"])
        self.assertEqual(len(registro["relacoes"]), 1)
        self.assertEqual(registro["relacoes"][0]["trecho_relato"], citacao_relato)
        self.assertEqual(registro["relacoes"][0]["trecho_freud"], fonte)
        self.assertEqual(mensagens_enviadas[0]["relato_original"], TEXTO)
        self.assertEqual(len(mensagens_enviadas[0]["consultas_da_busca"]), 11)
        self.assertNotIn('"vetor"', json.dumps(mensagens_enviadas))
        self.assertNotIn('"valores"', json.dumps(mensagens_enviadas))
        self.assertNotIn("artefatos", mensagens_enviadas[0])
        self.gerador.assert_not_called()
        self.adaptador.assert_not_called()

    def test_busca_sem_importacao_nao_cria_relato_nem_inferencia(self):
        resposta = self.buscar()
        self.assertEqual(resposta.status_code, 400, resposta.get_json())
        self.assertIn("erro", resposta.get_json())
        self.assertEqual(self.client.get("/api/relatos").get_json()["relatos"], [])
        self.assertEqual(self.servico.chamadas, [])
        self.gerador.assert_not_called()
        self.adaptador.assert_not_called()

    def test_texto_alterado_nao_reutiliza_vetores_nem_salva_registro(self):
        importada = self.importar()
        alterado = TEXTO.replace("Árvore", "Pessoa")
        busca = self.buscar(texto=alterado, vetorizacao_id=importada["id"])
        self.assertEqual(busca.status_code, 400, busca.get_json())
        registro = self.client.post(
            "/api/relatos", json={"texto": alterado, "vetorizacao_id": importada["id"]},
        )
        self.assertEqual(registro.status_code, 400, registro.get_json())
        self.assertEqual(self.client.get("/api/relatos").get_json()["relatos"], [])
        self.assertEqual(self.servico.chamadas, [])

    def test_id_inexistente_nao_e_interpretado_como_busca_por_texto(self):
        inexistente = "f" * 64
        self.assertEqual(self.client.get(f"/api/vetorizacoes/{inexistente}").status_code, 404)
        resposta = self.buscar(vetorizacao_id=inexistente)
        self.assertEqual(resposta.status_code, 404, resposta.get_json())
        registro = self.client.post(
            "/api/relatos", json={"texto": TEXTO, "vetorizacao_id": inexistente},
        )
        self.assertEqual(registro.status_code, 404, registro.get_json())
        self.assertEqual(self.client.get("/api/relatos").get_json()["relatos"], [])
        self.assertEqual(self.servico.chamadas, [])

    def test_importacao_invalida_falha_antes_de_registrar(self):
        for dados in (None, [], {}, {"schema_version": "desconhecida"}):
            with self.subTest(dados=dados):
                resposta = self.client.post("/api/vetorizacoes", json=dados)
                self.assertIn(resposta.status_code, (400, 415), resposta.get_json())
        adulterada = deepcopy(self.exportacao)
        adulterada["artefatos"][0]["valores"][0] += .1
        resposta = self.client.post("/api/vetorizacoes", json=adulterada)
        self.assertEqual(resposta.status_code, 400, resposta.get_json())
        self.assertEqual(self.client.get("/api/relatos").get_json()["relatos"], [])
        self.assertEqual(self.servico.chamadas, [])

    def test_exportacao_valida_de_outro_espaco_vetorial_nao_e_aceita(self):
        dimensao_divergente = construir_exportacao(dimensao=3, limite=512)
        resposta = self.client.post("/api/vetorizacoes", json=dimensao_divergente)
        self.assertEqual(resposta.status_code, 400, resposta.get_json())
        self.assertIn("dimensão", resposta.get_json()["erro"])
        self.assertEqual(self.client.get("/api/relatos").get_json()["relatos"], [])
        self.assertEqual(self.servico.chamadas, [])
        self.gerador.assert_not_called()
        self.adaptador.assert_not_called()

    def test_importacao_compativel_sem_corpus_pronto_fica_disponivel_para_depois(self):
        self.servico.pronto = False
        importada = self.importar()
        resposta = self.buscar(vetorizacao_id=importada["id"])
        self.assertEqual(resposta.status_code, 503, resposta.get_json())
        self.assertIn("problemas", resposta.get_json())
        self.assertEqual(self.client.get(f"/api/vetorizacoes/{importada['id']}").status_code, 200)
        self.assertEqual(self.client.get("/api/relatos").get_json()["relatos"], [])
        self.assertEqual(self.servico.chamadas, [])

    def test_relato_sem_vetores_ainda_pode_ser_salvo_sem_busca(self):
        self.servico.pronto = False
        resposta = self.client.post("/api/relatos", json={"texto": TEXTO})
        self.assertEqual(resposta.status_code, 201, resposta.get_json())
        registro = resposta.get_json()
        self.assertEqual(registro["texto"], TEXTO)
        self.assertIsNone(registro["vetorizacao_id"])
        self.assertEqual(registro["estado"], "registrado")
        self.assertEqual(self.servico.chamadas, [])

    def test_relato_salvo_sem_vetores_recebe_vinculo_na_primeira_busca(self):
        registro = self.client.post("/api/relatos", json={"texto": TEXTO}).get_json()
        importada = self.importar()
        resposta = self.buscar(vetorizacao_id=importada["id"], relato_id=registro["id"])
        self.assertEqual(resposta.status_code, 202, resposta.get_json())
        self.assertEqual(self.aguardar(resposta.get_json()["id"])["estado"], "concluido")
        reaberto = self.client.get(f"/api/relatos/{registro['id']}").get_json()
        self.assertEqual(reaberto["vetorizacao_id"], importada["id"])
        self.assertEqual(len(self.client.get("/api/relatos").get_json()["relatos"]), 1)

    def test_relato_vinculado_nao_troca_de_exportacao_silenciosamente(self):
        original = self.importar()
        registro = self.client.post(
            "/api/relatos", json={"texto": TEXTO, "vetorizacao_id": original["id"]},
        ).get_json()
        outra = self.importar(construir_exportacao(
            dimensao=1024, limite=512, perfil_id="e5_consulta",
        ))
        self.assertNotEqual(original["id"], outra["id"])
        resposta = self.buscar(vetorizacao_id=outra["id"], relato_id=registro["id"])
        self.assertEqual(resposta.status_code, 400, resposta.get_json())
        self.assertEqual(self.client.get(f"/api/relatos/{registro['id']}").get_json(), registro)
        self.assertEqual(self.servico.chamadas, [])
        self.gerador.assert_not_called()
        self.adaptador.assert_not_called()

    def test_banco_anterior_migra_sem_apagar_relato_existente(self):
        caminho_antigo = Path(self.temporario.name) / "banco-anterior.sqlite3"
        with sqlite3.connect(caminho_antigo) as conexao:
            conexao.executescript("""
                CREATE TABLE relatos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, texto TEXT NOT NULL,
                    palavras INTEGER NOT NULL, criado_em TEXT NOT NULL,
                    atualizado_em TEXT NOT NULL, estado TEXT NOT NULL,
                    busca_id TEXT UNIQUE, provedor TEXT, modelo TEXT,
                    erro TEXT, resultado_json TEXT
                );
                CREATE TABLE relacoes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    relato_id INTEGER NOT NULL REFERENCES relatos(id) ON DELETE CASCADE,
                    ordem INTEGER NOT NULL, trecho_relato TEXT NOT NULL,
                    relato_inicio INTEGER NOT NULL, relato_fim INTEGER NOT NULL,
                    trecho_freud TEXT NOT NULL, freud_inicio INTEGER NOT NULL,
                    freud_fim INTEGER NOT NULL, justificativa TEXT NOT NULL,
                    bloco_id TEXT NOT NULL, situacao TEXT NOT NULL, dados_json TEXT NOT NULL,
                    UNIQUE(relato_id, ordem)
                );
            """)
            conexao.execute(
                """INSERT INTO relatos
                (id, texto, palavras, criado_em, atualizado_em, estado)
                VALUES (7, ?, ?, '2026-10-10T09:00:00Z', '2026-10-10T09:00:00Z', 'registrado')""",
                (TEXTO, len(TEXTO.split())),
            )
        app = criar_app({"TESTING": True, "DATABASE": str(caminho_antigo)}, servico=self.servico)
        self.apps.append(app)
        cliente = app.test_client()
        resposta = cliente.get("/api/relatos/7")
        self.assertEqual(resposta.status_code, 200, resposta.get_json())
        registro = resposta.get_json()
        self.assertEqual(registro["texto"], TEXTO)
        self.assertEqual(registro["estado"], "registrado")
        self.assertIsNone(registro["vetorizacao_id"])
        self.assertEqual(registro["criado_em"], "2026-10-10T09:00:00Z")
        importada = cliente.post("/api/vetorizacoes", json=self.exportacao)
        self.assertEqual(importada.status_code, 201, importada.get_json())
        self.assertEqual(cliente.get("/api/relatos/7").get_json(), registro)
        self.assertEqual(len(cliente.get("/api/relatos").get_json()["relatos"]), 1)
        self.gerador.assert_not_called()
        self.adaptador.assert_not_called()


if __name__ == "__main__":
    unittest.main()
