"""Persistência do agente sem modelo E5 nem chamadas a provedores externos."""

from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import time
import unittest

from agente_analista.app import criar_app
from agente_analista.entrada import validar_relato


CHAVE_TESTE = "credencial-transitoria-nao-deve-ir-ao-banco"
TEXTO = "Não respondi ao convite 😀.\r\n \t\r\nAinda guardo a mensagem."


class ServicoPersistenciaTeste:
    def __init__(self):
        self.chamadas = 0
        self.pronto = True
        self.falhar = False
        self.ligacao_invalida = False
        self.sem_ligacoes = False
        self.com_descartada = False

    def status(self):
        return {"pronto": self.pronto, "problemas": [] if self.pronto else ["E5 ausente."]}

    def executar(self, relato, progresso, *, provedor, modelo, chave_api):
        self.chamadas += 1
        progresso("Conferindo citações")
        if self.falhar:
            raise RuntimeError("conteúdo privado da falha " + chave_api)
        passagem = relato["paragrafos"][0]
        fonte = "Uma passagem fictícia usada apenas pelo teste de armazenamento."
        ligacao = {
            "id": "L1", "paragrafo": "P1",
            "relato": {campo: passagem[campo] for campo in ("inicio", "fim", "texto")},
            "freud": {"inicio": 0, "fim": len(fonte), "texto": fonte},
            "observacao": "A pessoa manteve a mensagem após o convite.",
            "conceito": "", "bloco_id": "bloco-de-teste", "fragmentos_ids": ["fragmento-de-teste"],
            "ligacao": "Associação técnica fictícia para testar o armazenamento.",
            "justificativa": "Os trechos são conservados sem alteração de caracteres.",
            "limites": "Esta associação não é uma avaliação interpretativa real.",
            "alternativas": [], "situacao": "pertinente",
            "referencia": {"obra": "Fonte fictícia do teste", "volume": 1},
            "contexto": fonte,
            "conferencias": {"relato_literal": True, "freud_literal": True, "fragmentos_validos": True},
        }
        ligacoes = [] if self.sem_ligacoes else [ligacao]
        if self.ligacao_invalida:
            invalida = deepcopy(ligacao)
            invalida["id"] = "L2"
            invalida.pop("conferencias")
            ligacoes.append(invalida)
        descartadas = []
        if self.com_descartada:
            descartada = deepcopy(ligacao)
            descartada.update(
                id="L2", situacao="descartada",
                justificativa="As citações são literais, mas não sustentam a relação proposta.",
            )
            descartadas.append(descartada)
        return {
            "relato": relato, "ligacoes": ligacoes, "descartadas": descartadas, "rejeitadas": [],
            "candidatos": [], "consultas": [], "mensagem": "Ligação fictícia de teste.",
            "justificativas": {"provedor": provedor, "modelo": modelo, "formato_resposta": "json_schema"},
            # O banco deve selecionar os campos de resultado; nunca gravar esta chave.
            "chave_api": chave_api,
        }


class PersistenciaAPITests(unittest.TestCase):
    def setUp(self):
        self.temporario = TemporaryDirectory()
        self.caminho = Path(self.temporario.name) / "agente.sqlite3"
        self.servico = ServicoPersistenciaTeste()
        self.apps = []
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

    def registrar(self, texto=TEXTO):
        resposta = self.client.post("/api/relatos", json={"texto": texto})
        self.assertEqual(resposta.status_code, 201, resposta.get_json())
        return resposta.get_json()

    def buscar(self, texto=TEXTO, **campos):
        dados = {"texto": texto, "provedor": "openrouter", "modelo": "fabricante/modelo-teste",
                 "chave_api": CHAVE_TESTE}
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
        self.fail("A busca de teste não terminou.")

    def test_registro_sem_e5_preserva_original_e_valida_entrada(self):
        self.servico.pronto = False
        registro = self.registrar()
        self.assertIs(type(registro["id"]), int)
        self.assertEqual(registro["texto"], TEXTO)
        self.assertEqual(registro["palavras"], len(TEXTO.split()))
        self.assertTrue(registro["criado_em"])
        self.assertEqual(registro["estado"], "registrado")
        self.assertIsNone(registro["busca_id"])
        self.assertIsNone(registro["resultado"])
        self.assertIsNone(registro["erro"])
        self.assertEqual(registro["relacoes"], [])
        self.assertEqual(self.servico.chamadas, 0)
        self.assertEqual(self.client.get(f"/api/relatos/{registro['id']}").get_json(), registro)
        for dados in ({"texto": "Um só parágrafo."}, {"texto": []}, None):
            with self.subTest(dados=dados):
                resposta = self.client.post("/api/relatos", json=dados)
                self.assertIn(resposta.status_code, (400, 415))
        self.assertEqual(len(self.client.get("/api/relatos").get_json()["relatos"]), 1)

    def test_historico_paginado_nao_perde_nem_repete_registros(self):
        registros = [self.registrar(f"Registro {numero}.\n\nSegunda parte.") for numero in range(3)]
        pagina = self.client.get("/api/relatos?limite=2").get_json()
        ids = [item["id"] for item in pagina["relatos"]]
        self.assertEqual(ids, [registros[2]["id"], registros[1]["id"]])
        for item in pagina["relatos"]:
            self.assertEqual(item["estado"], "registrado")
            self.assertEqual(item["total_relacoes"], 0)
            self.assertNotIn("resultado", item)
        cursor = pagina["proximo_antes_id"]
        self.assertEqual(cursor, ids[-1])
        restante = self.client.get(f"/api/relatos?limite=2&antes_id={cursor}").get_json()
        self.assertEqual([item["id"] for item in restante["relatos"]], [registros[0]["id"]])
        self.assertIsNone(restante["proximo_antes_id"])

    def test_conclusao_reabre_apos_reinicio_sem_perder_citacoes_ou_guardar_chave(self):
        resposta = self.buscar()
        self.assertEqual(resposta.status_code, 202, resposta.get_json())
        ids = resposta.get_json()
        tarefa = self.aguardar(ids["id"])
        self.assertEqual(tarefa["estado"], "concluido", tarefa)
        self.assertNotIn(CHAVE_TESTE, json.dumps(tarefa))
        self.assertNotIn("chave_api", json.dumps(tarefa))
        registro = self.client.get(f"/api/relatos/{ids['relato_id']}").get_json()
        self.assertEqual(registro["texto"], TEXTO)
        self.assertEqual(registro["estado"], "concluido")
        self.assertEqual(registro["busca_id"], ids["id"])
        self.assertEqual(len(registro["relacoes"]), 1)
        relacao = registro["relacoes"][0]
        ligacao = registro["resultado"]["ligacoes"][0]
        self.assertEqual(ligacao["observacao"], "A pessoa manteve a mensagem após o convite.")
        self.assertEqual(relacao["trecho_relato"], ligacao["relato"]["texto"])
        self.assertEqual(relacao["trecho_freud"], ligacao["freud"]["texto"])
        self.assertEqual(relacao["justificativa"], ligacao["justificativa"])
        self.assertEqual(relacao["relato_id"], registro["id"])
        self.assertEqual(TEXTO[ligacao["relato"]["inicio"]:ligacao["relato"]["fim"]], ligacao["relato"]["texto"])
        self.app.extensions["agente_analista"]["executor"].shutdown(wait=True)
        reiniciada = self.nova_app().test_client()
        self.assertEqual(reiniciada.get(f"/api/relatos/{registro['id']}").get_json(), registro)
        reaberta = reiniciada.get(f"/api/buscas/{ids['id']}")
        self.assertEqual(reaberta.status_code, 200)
        self.assertEqual(reaberta.get_json()["resultado"], registro["resultado"])
        self.assertEqual(self.servico.chamadas, 1)
        self.assertNotIn(CHAVE_TESTE, json.dumps(registro))
        self.assertNotIn("chave_api", json.dumps(registro))
        for arquivo in self.caminho.parent.glob(self.caminho.name + "*"):
            if arquivo.is_file():
                self.assertNotIn(CHAVE_TESTE.encode(), arquivo.read_bytes())

    def test_relato_registrado_pode_ser_analisado_uma_vez_sem_sobrescrever(self):
        registro = self.registrar()
        resposta = self.buscar(relato_id=registro["id"])
        self.assertEqual(resposta.status_code, 202, resposta.get_json())
        ids = resposta.get_json()
        self.assertEqual(ids["relato_id"], registro["id"])
        self.assertEqual(self.aguardar(ids["id"])["estado"], "concluido")
        antes = self.client.get(f"/api/relatos/{registro['id']}").get_json()
        self.assertEqual(self.buscar(relato_id=registro["id"]).status_code, 409)
        self.assertEqual(self.client.get(f"/api/relatos/{registro['id']}").get_json(), antes)
        nova = self.buscar()
        self.assertEqual(nova.status_code, 202)
        self.assertNotEqual(nova.get_json()["relato_id"], registro["id"])
        self.assertEqual(self.aguardar(nova.get_json()["id"])["estado"], "concluido")

    def test_id_ausente_ou_texto_divergente_nao_altera_registro(self):
        registro = self.registrar()
        self.assertEqual(self.buscar(relato_id=registro["id"] + 100).status_code, 404)
        self.assertEqual(self.buscar(texto="Outro relato.\n\nOutra parte.", relato_id=registro["id"]).status_code, 400)
        self.assertEqual(self.client.get(f"/api/relatos/{registro['id']}").get_json(), registro)
        self.assertEqual(self.servico.chamadas, 0)

    def test_resultado_sem_ligacoes_permanece_concluido_no_historico(self):
        self.servico.sem_ligacoes = True
        resposta = self.buscar()
        self.assertEqual(resposta.status_code, 202)
        ids = resposta.get_json()
        self.assertEqual(self.aguardar(ids["id"])["estado"], "concluido")
        registro = self.client.get(f"/api/relatos/{ids['relato_id']}").get_json()
        self.assertEqual(registro["estado"], "concluido")
        self.assertIsNone(registro["erro"])
        self.assertEqual(registro["resultado"]["ligacoes"], [])
        self.assertEqual(registro["relacoes"], [])
        historico = self.client.get("/api/relatos").get_json()["relatos"]
        self.assertEqual(historico[0]["id"], registro["id"])
        self.assertEqual(historico[0]["estado"], "concluido")
        self.assertEqual(historico[0]["total_relacoes"], 0)

    def test_reinicio_marca_busca_incompleta_sem_perder_o_relato(self):
        banco = self.app.extensions["agente_analista"]["banco"]
        busca_id = "busca-interrompida-do-teste"
        relato_id = banco.iniciar_busca(
            validar_relato(TEXTO), busca_id, "openrouter", "fabricante/modelo-teste",
        )
        self.app.extensions["agente_analista"]["executor"].shutdown(wait=True)
        reiniciada = self.nova_app().test_client()
        resposta = reiniciada.get(f"/api/relatos/{relato_id}")
        self.assertEqual(resposta.status_code, 200)
        registro = resposta.get_json()
        self.assertEqual(registro["texto"], TEXTO)
        self.assertEqual(registro["busca_id"], busca_id)
        self.assertEqual(registro["estado"], "interrompido")
        self.assertIsNone(registro["resultado"])
        self.assertEqual(registro["relacoes"], [])
        self.assertTrue(registro["erro"])
        tarefa = reiniciada.get(f"/api/buscas/{busca_id}")
        self.assertEqual(tarefa.status_code, 200)
        self.assertEqual(tarefa.get_json()["estado"], "erro")
        self.assertTrue(tarefa.get_json()["erro"])
        self.assertEqual(self.servico.chamadas, 0)

    def test_segunda_app_sem_pedidos_nao_interrompe_busca_da_primeira(self):
        # A primeira instância já atende pedidos; a segunda pode falhar ao
        # abrir a porta e nunca deve alterar uma busca que continua ativa.
        self.assertEqual(self.client.get("/api/relatos").status_code, 200)
        banco = self.app.extensions["agente_analista"]["banco"]
        relato = validar_relato(TEXTO)
        busca_id = "busca-da-primeira-instancia"
        relato_id = banco.iniciar_busca(relato, busca_id, "openrouter", "fabricante/modelo-teste")
        self.nova_app()
        self.assertEqual(banco.obter(relato_id)["estado"], "executando")
        resultado = self.servico.executar(
            relato, lambda etapa: None, provedor="openrouter",
            modelo="fabricante/modelo-teste", chave_api=CHAVE_TESTE,
        )
        banco.concluir(busca_id, resultado)
        registro = self.client.get(f"/api/relatos/{relato_id}").get_json()
        self.assertEqual(registro["estado"], "concluido")
        self.assertEqual(registro["texto"], TEXTO)
        self.assertEqual(len(registro["relacoes"]), 1)
        self.assertEqual(registro["resultado"]["ligacoes"][0]["id"], "L1")

    def test_relacao_descartada_conferida_permanece_no_resultado_e_no_banco(self):
        self.servico.com_descartada = True
        resposta = self.buscar()
        self.assertEqual(resposta.status_code, 202)
        ids = resposta.get_json()
        self.assertEqual(self.aguardar(ids["id"])["estado"], "concluido")
        registro = self.client.get(f"/api/relatos/{ids['relato_id']}").get_json()
        self.assertEqual(len(registro["resultado"]["ligacoes"]), 1)
        self.assertEqual(len(registro["resultado"]["descartadas"]), 1)
        self.assertEqual([item["situacao"] for item in registro["relacoes"]], ["pertinente", "descartada"])
        self.assertEqual([item["ordem"] for item in registro["relacoes"]], [1, 2])
        descartada = registro["resultado"]["descartadas"][0]
        self.assertEqual(registro["relacoes"][1]["dados"], descartada)
        self.assertEqual(registro["relacoes"][1]["justificativa"], descartada["justificativa"])
        self.assertEqual(descartada["conferencias"], {
            "relato_literal": True, "freud_literal": True, "fragmentos_validos": True,
        })
        historico = self.client.get("/api/relatos").get_json()["relatos"]
        self.assertEqual(historico[0]["total_relacoes"], 2)

    def test_falha_do_servico_preserva_relato_sem_expor_erro_privado(self):
        self.servico.falhar = True
        resposta = self.buscar()
        self.assertEqual(resposta.status_code, 202)
        ids = resposta.get_json()
        tarefa = self.aguardar(ids["id"])
        self.assertEqual(tarefa["estado"], "erro")
        registro = self.client.get(f"/api/relatos/{ids['relato_id']}").get_json()
        self.assertEqual(registro["texto"], TEXTO)
        self.assertEqual(registro["estado"], "erro")
        self.assertIsNone(registro["resultado"])
        self.assertEqual(registro["relacoes"], [])
        self.assertTrue(registro["erro"])
        self.assertNotIn("conteúdo privado", json.dumps(registro))
        self.assertNotIn(CHAVE_TESTE, json.dumps(registro))

    def test_lote_com_relacao_nao_conferida_faz_rollback(self):
        self.servico.ligacao_invalida = True
        resposta = self.buscar()
        self.assertEqual(resposta.status_code, 202)
        ids = resposta.get_json()
        self.assertEqual(self.aguardar(ids["id"])["estado"], "erro")
        registro = self.client.get(f"/api/relatos/{ids['relato_id']}").get_json()
        self.assertEqual(registro["estado"], "erro")
        self.assertIsNone(registro["resultado"])
        self.assertEqual(registro["relacoes"], [])
        banco = self.app.extensions["agente_analista"]["banco"]
        conexao = banco.conectar()
        try:
            self.assertEqual(conexao.execute("SELECT COUNT(*) FROM relacoes").fetchone()[0], 0)
        finally:
            conexao.close()

    def test_chave_estrangeira_impede_relacao_orfa(self):
        resposta = self.buscar()
        ids = resposta.get_json()
        self.assertEqual(self.aguardar(ids["id"])["estado"], "concluido")
        banco = self.app.extensions["agente_analista"]["banco"]
        conexao = banco.conectar()
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conexao.execute("UPDATE relacoes SET relato_id = -1")
            conexao.rollback()
        finally:
            conexao.close()
        registro = self.client.get(f"/api/relatos/{ids['relato_id']}").get_json()
        self.assertEqual(len(registro["relacoes"]), 1)
        self.assertEqual(registro["relacoes"][0]["relato_id"], registro["id"])


if __name__ == "__main__":
    unittest.main()
