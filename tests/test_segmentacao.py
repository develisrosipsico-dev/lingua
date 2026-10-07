import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import UUID

import spacy

from preparacao import mapear_intervalo, preparar_texto, validar_registro
from segmentacao import (
    ErroSegmentacao,
    contexto_periodo,
    segmentar_preparacao,
    validar_segmentacao,
)


class SegmentacaoTests(unittest.TestCase):
    INSTANTE = "2026-10-07T12:34:56-03:00"
    ORIGINAL = "Eu não queria sair.\r\nMas fiquei."

    def preparar(self, texto, *, normalizar_crlf=False):
        return preparar_texto(
            texto,
            "documento-teste-04",
            normalizar_crlf=normalizar_crlf,
            preparacao_id="preparacao-teste-04",
            registrado_em=self.INSTANTE,
            metadados={"fonte": {"etapa": "registro", "etiquetas": ["original"]}},
        )

    def segmentar(self, texto, *, normalizar_crlf=False, **opcoes):
        argumentos = {"segmentacao_id": "segmentacao-teste-04", "registrado_em": self.INSTANTE}
        argumentos.update(opcoes)
        return segmentar_preparacao(self.preparar(texto, normalizar_crlf=normalizar_crlf), **argumentos)

    def conferir_validacao(self, validacao):
        self.assertEqual(validacao["estado"], "pronto_para_etapa_05")
        self.assertIs(validacao["pronto_para_etapa_05"], True)
        self.assertIsInstance(validacao["verificacoes"], list)
        self.assertTrue(validacao["verificacoes"])

    def conferir_unidades(self, registro):
        preparacao = registro["preparacao"]
        trabalho = preparacao["trabalho"]["texto"]
        identificadores = []
        for colecao in ["paragrafos", "periodos", "tokens", "separadores"]:
            fim_anterior = 0
            for ordem, unidade in enumerate(registro[colecao]):
                with self.subTest(colecao=colecao, ordem=ordem):
                    self.assertEqual(unidade["ordem"], ordem)
                    self.assertIsInstance(unidade["id"], str)
                    self.assertTrue(unidade["id"].strip())
                    identificadores.append(unidade["id"])
                    inicio, fim = unidade["trabalho"]["inicio"], unidade["trabalho"]["fim"]
                    self.assertLess(inicio, fim)
                    self.assertGreaterEqual(inicio, fim_anterior)
                    self.assertLessEqual(fim, len(trabalho))
                    self.assertEqual(unidade["texto"], trabalho[inicio:fim])
                    self.assertEqual(unidade["original"], mapear_intervalo(preparacao, inicio, fim))
                    fim_anterior = fim
        self.assertEqual(len(identificadores), len(set(identificadores)))

        partes = sorted(registro["tokens"] + registro["separadores"], key=lambda item: item["trabalho"]["inicio"])
        cursor = 0
        for parte in partes:
            self.assertEqual(parte["trabalho"]["inicio"], cursor)
            cursor = parte["trabalho"]["fim"]
        self.assertEqual(cursor, len(trabalho))
        self.assertEqual("".join(parte["texto"] for parte in partes), trabalho)
        self.assertTrue(all(not token["texto"].isspace() for token in registro["tokens"]))
        self.assertTrue(all(separador["texto"].isspace() for separador in registro["separadores"]))

        tokens = {token["id"]: token for token in registro["tokens"]}
        periodos = {periodo["id"]: periodo for periodo in registro["periodos"]}
        paragrafos = {paragrafo["id"]: paragrafo for paragrafo in registro["paragrafos"]}
        tokens_referidos = []
        periodos_referidos = []
        for periodo in registro["periodos"]:
            self.assertTrue(periodo["tokens"])
            unidades = [tokens[identificador] for identificador in periodo["tokens"]]
            tokens_referidos.extend(periodo["tokens"])
            self.assertEqual(periodo["trabalho"]["inicio"], unidades[0]["trabalho"]["inicio"])
            self.assertEqual(periodo["trabalho"]["fim"], unidades[-1]["trabalho"]["fim"])
            self.assertIsInstance(periodo["regra_encerramento"], str)
            self.assertTrue(periodo["regra_encerramento"].strip())
            for token in unidades:
                self.assertEqual(token["periodo_id"], periodo["id"])
                self.assertEqual(token["paragrafo_id"], periodo["paragrafo_id"])
            paragrafo = paragrafos[periodo["paragrafo_id"]]
            self.assertGreaterEqual(periodo["trabalho"]["inicio"], paragrafo["trabalho"]["inicio"])
            self.assertLessEqual(periodo["trabalho"]["fim"], paragrafo["trabalho"]["fim"])
        for paragrafo in registro["paragrafos"]:
            self.assertTrue(paragrafo["periodos"])
            unidades = [periodos[identificador] for identificador in paragrafo["periodos"]]
            periodos_referidos.extend(paragrafo["periodos"])
            self.assertEqual(paragrafo["trabalho"]["inicio"], unidades[0]["trabalho"]["inicio"])
            self.assertEqual(paragrafo["trabalho"]["fim"], unidades[-1]["trabalho"]["fim"])
            for periodo in unidades:
                self.assertEqual(periodo["paragrafo_id"], paragrafo["id"])
        self.assertEqual(tokens_referidos, [token["id"] for token in registro["tokens"]])
        self.assertEqual(periodos_referidos, [periodo["id"] for periodo in registro["periodos"]])

    def conferir_periodos(self, texto, esperados):
        registro = self.segmentar(texto)
        self.assertEqual([periodo["texto"] for periodo in registro["periodos"]], esperados)
        self.conferir_unidades(registro)
        self.conferir_validacao(validar_segmentacao(registro))
        return registro

    def test_schema_identificadores_coordenadas_e_processamento(self):
        registro = self.segmentar("Olá!")
        campos = {
            "schema_version", "etapa", "registrado_em", "documento_id", "preparacao_id",
            "segmentacao_id", "preparacao", "coordenadas", "processamento", "paragrafos",
            "periodos", "tokens", "separadores", "validacao",
        }
        self.assertTrue(campos.issubset(registro))
        self.assertEqual(registro["schema_version"], "1.0.0")
        self.assertEqual(registro["etapa"], "04_tokenizacao_segmentacao")
        self.assertEqual(registro["documento_id"], "documento-teste-04")
        self.assertEqual(registro["preparacao_id"], "preparacao-teste-04")
        self.assertEqual(registro["segmentacao_id"], "segmentacao-teste-04")
        self.assertEqual(registro["coordenadas"], registro["preparacao"]["coordenadas"])
        processamento = registro["processamento"]
        self.assertEqual(processamento["ferramenta"], "spacy")
        self.assertEqual(processamento["versao"], spacy.__version__)
        self.assertEqual(processamento["idioma"], "pt")
        self.assertIsNone(processamento["modelo"])
        self.assertEqual(processamento["tokenizador"]["versao"], spacy.__version__)
        self.assertIsInstance(processamento["tokenizador"]["ajustes"], list)
        self.assertTrue(processamento["regras"]["identificacao"])
        self.assertEqual(processamento["regras"]["versao"], "1.0.0")
        self.assertIsInstance(processamento["configuracoes"], dict)
        self.conferir_validacao(registro["validacao"])

    def test_integracao_com_os_dois_exemplos_existentes(self):
        pasta = Path(__file__).resolve().parents[1] / "examples"
        for nome in ["preservacao_literal.json", "crlf_para_lf.json"]:
            with self.subTest(nome=nome):
                preparacao = json.loads((pasta / nome).read_text(encoding="utf-8"))
                snapshot = copy.deepcopy(preparacao)
                registro = segmentar_preparacao(preparacao, segmentacao_id="integracao-exemplo", registrado_em=self.INSTANTE)
                self.assertEqual(preparacao, snapshot)
                self.assertEqual(registro["preparacao"], preparacao)
                self.assertEqual([periodo["texto"] for periodo in registro["periodos"]], ["Eu não queria sair.", "Mas fiquei."])
                self.assertEqual(len(registro["paragrafos"]), 1)
                self.conferir_unidades(registro)

    def test_crlf_opcional_produz_mesmas_unidades_e_origem(self):
        original = "Primeiro.\r\nAinda aqui.\r\n \t\r\nSegundo!\r\n\r\nSem ponto"
        literal = self.segmentar(original)
        convertido = self.segmentar(original, normalizar_crlf=True)
        for colecao in ["paragrafos", "periodos", "tokens", "separadores"]:
            unidades_literal = literal[colecao]
            unidades_convertido = convertido[colecao]
            self.assertEqual(len(unidades_literal), len(unidades_convertido))
            for antes, depois in zip(unidades_literal, unidades_convertido):
                self.assertEqual(antes["texto"].replace("\r\n", "\n"), depois["texto"])
                self.assertEqual(antes["original"], depois["original"])
        self.conferir_unidades(literal)
        self.conferir_unidades(convertido)

    def test_exemplo_usuario_mapeia_periodo_e_tokens_apos_crlf(self):
        registro = self.segmentar(self.ORIGINAL, normalizar_crlf=True)
        periodo = registro["periodos"][1]
        self.assertEqual(periodo["texto"], "Mas fiquei.")
        self.assertEqual(periodo["trabalho"], {"inicio": 20, "fim": 31})
        self.assertEqual(periodo["original"], {"inicio": 21, "fim": 32})
        token = next(token for token in registro["tokens"] if token["texto"] == "Mas")
        self.assertEqual(token["trabalho"], {"inicio": 20, "fim": 23})
        self.assertEqual(token["original"], {"inicio": 21, "fim": 24})
        quebra = next(separador for separador in registro["separadores"] if separador["texto"] == "\n")
        self.assertEqual(quebra["trabalho"], {"inicio": 19, "fim": 20})
        self.assertEqual(quebra["original"], {"inicio": 19, "fim": 21})
        self.assertEqual(registro["paragrafos"][0]["original"], {"inicio": 0, "fim": 32})
        self.conferir_unidades(registro)

    def test_periodo_que_atravessa_crlf_preserva_textos_distintos(self):
        registro = self.segmentar("Eu fiquei\r\nna sala.", normalizar_crlf=True)
        periodo = registro["periodos"][0]
        self.assertEqual(periodo["texto"], "Eu fiquei\nna sala.")
        original = registro["preparacao"]["original"]["texto"]
        intervalo = periodo["original"]
        self.assertEqual(original[intervalo["inicio"]:intervalo["fim"]], "Eu fiquei\r\nna sala.")
        self.conferir_unidades(registro)

    def test_abreviacoes_dr_e_dra_nao_criam_periodos_extras(self):
        self.conferir_periodos("Dr. João viu a Dra. Ana. Depois saiu.", ["Dr. João viu a Dra. Ana.", "Depois saiu."])

    def test_abreviacao_etc_no_fim_encerra_diante_de_maiuscula(self):
        self.conferir_periodos("Comprei pão, frutas etc. Depois saí.", ["Comprei pão, frutas etc.", "Depois saí."])

    def test_abreviacao_etc_interna_nao_corta_diante_de_minuscula(self):
        self.conferir_periodos("Comprei pão, frutas etc. para a viagem.", ["Comprei pão, frutas etc. para a viagem."])

    def test_numeros_decimais_milhar_datas_e_horarios(self):
        casos = [
            ("A dose foi 1,5 mg e custou R$ 1.234,56. Depois saí.", ["A dose foi 1,5 mg e custou R$ 1.234,56.", "Depois saí."]),
            ("No dia 07.10.2026, chegamos às 10:30. Acabou.", ["No dia 07.10.2026, chegamos às 10:30.", "Acabou."]),
            ("O valor é 3.14. Entendi.", ["O valor é 3.14.", "Entendi."]),
        ]
        for texto, esperados in casos:
            with self.subTest(texto=texto):
                self.conferir_periodos(texto, esperados)

    def test_urls_emails_e_pontuacao_externa(self):
        casos = [
            ("Veja https://exemplo.com/a?x=1&y=2. Depois escreva.", ["Veja https://exemplo.com/a?x=1&y=2.", "Depois escreva."]),
            ("Envie para ana.silva+tag@exemplo.com. Depois espere.", ["Envie para ana.silva+tag@exemplo.com.", "Depois espere."]),
            ("O site exemplo.com está disponível. Entre.", ["O site exemplo.com está disponível.", "Entre."]),
        ]
        for texto, esperados in casos:
            with self.subTest(texto=texto):
                self.conferir_periodos(texto, esperados)

    def test_reticencias_ascii_e_unicode_preservam_hesitacao(self):
        for reticencias in ["...", "…"]:
            with self.subTest(reticencias=reticencias):
                texto = f"Eu fiquei{reticencias} fiquei olhando."
                self.conferir_periodos(texto, [texto])

    def test_reticencias_sozinhas_encerram_no_fim_do_paragrafo(self):
        self.conferir_periodos("Eu fiquei...\n\nDepois saí…", ["Eu fiquei...", "Depois saí…"])

    def test_virgula_dois_pontos_e_ponto_e_virgula_nao_dividem(self):
        self.conferir_periodos("Eu falei: sim; esperei, fiquei.", ["Eu falei: sim; esperei, fiquei."])

    def test_interrogacao_exclamacao_e_sequencias_de_pontuacao(self):
        self.conferir_periodos("Que foi?! Nada!!! Depois? Fim.", ["Que foi?!", "Nada!!!", "Depois?", "Fim."])

    def test_aspas_e_parenteses_de_fechamento_ficam_no_periodo(self):
        casos = [
            ('Ele disse: "Vou." Depois voltou.', ['Ele disse: "Vou."', "Depois voltou."]),
            ('Ele disse: "Pare!" Depois voltou.', ['Ele disse: "Pare!"', "Depois voltou."]),
            ("Ele disse: “Pare!” Depois voltou.", ["Ele disse: “Pare!”", "Depois voltou."]),
            ('Ele disse: ("Vou!") Depois voltou.', ['Ele disse: ("Vou!")', "Depois voltou."]),
            ("Ele pensou (será?). Depois voltou.", ["Ele pensou (será?).", "Depois voltou."]),
        ]
        for texto, esperados in casos:
            with self.subTest(texto=texto):
                self.conferir_periodos(texto, esperados)

    def test_parentese_de_abertura_pertence_ao_proximo_periodo(self):
        self.conferir_periodos("Ele foi. (Depois voltou.)", ["Ele foi.", "(Depois voltou.)"])

    def test_sem_pontuacao_final_encerra_no_fim_do_paragrafo(self):
        self.conferir_periodos("Primeiro sem ponto\n\nSegundo também", ["Primeiro sem ponto", "Segundo também"])

    def test_periodos_nunca_atravessam_paragrafos(self):
        registro = self.conferir_periodos("Um. Dois\n\nTrês. Quatro", ["Um.", "Dois", "Três.", "Quatro"])
        self.assertEqual(len(registro["paragrafos"]), 2)
        self.assertEqual([len(paragrafo["periodos"]) for paragrafo in registro["paragrafos"]], [2, 2])

    def test_linhas_em_branco_com_espacos_tab_e_delimitadores_repetidos(self):
        texto = "\n\t \nPrimeiro\ncontinua.\n \t\n Segundo sem ponto \n\n\nTerceiro.\n\t"
        registro = self.segmentar(texto)
        self.assertEqual([paragrafo["texto"] for paragrafo in registro["paragrafos"]], ["Primeiro\ncontinua.", "Segundo sem ponto", "Terceiro."])
        self.assertEqual(len(registro["periodos"]), 3)
        self.conferir_unidades(registro)

    def test_paragrafos_crlf_e_lf_mistos_sem_converter(self):
        texto = "Primeiro.\r\n \t\r\nSegundo\ncontinua.\n\nTerceiro"
        registro = self.segmentar(texto)
        self.assertEqual([paragrafo["texto"] for paragrafo in registro["paragrafos"]], ["Primeiro.", "Segundo\ncontinua.", "Terceiro"])
        self.assertEqual(registro["preparacao"]["trabalho"]["texto"], texto)
        self.conferir_unidades(registro)

    def test_quebra_simples_nao_cria_outro_paragrafo(self):
        for quebra in ["\n", "\r\n"]:
            with self.subTest(quebra=repr(quebra)):
                texto = "Uma linha" + quebra + "continua sem ponto"
                registro = self.segmentar(texto)
                self.assertEqual([paragrafo["texto"] for paragrafo in registro["paragrafos"]], [texto])
                self.assertEqual([periodo["texto"] for periodo in registro["periodos"]], [texto])
                self.conferir_unidades(registro)

    def test_cr_isolado_e_separadores_unicode_nao_delimitam_paragrafos(self):
        texto = "A\r\rB\u2028\u2028C\u2029\u2029D"
        registro = self.segmentar(texto)
        self.assertEqual([paragrafo["texto"] for paragrafo in registro["paragrafos"]], [texto])
        self.assertEqual([periodo["texto"] for periodo in registro["periodos"]], [texto])
        self.conferir_unidades(registro)

    def test_vazio_e_apenas_branco_nao_produzem_unidades_linguisticas(self):
        for texto in ["", " ", "\t \r\n\r\n", "\u00a0\u2003\u2028\u2029\r"]:
            with self.subTest(texto=repr(texto)):
                registro = self.segmentar(texto)
                self.assertEqual(registro["paragrafos"], [])
                self.assertEqual(registro["periodos"], [])
                self.assertEqual(registro["tokens"], [])
                self.conferir_unidades(registro)
                self.conferir_validacao(validar_segmentacao(registro))

    def test_espacos_antes_depois_e_repetidos_sao_preservados(self):
        texto = "\t  A  B\t\tC.  \r\n"
        registro = self.segmentar(texto)
        self.assertEqual([paragrafo["texto"] for paragrafo in registro["paragrafos"]], ["A  B\t\tC."])
        self.assertEqual(registro["separadores"][0]["trabalho"]["inicio"], 0)
        self.assertEqual(registro["separadores"][-1]["trabalho"]["fim"], len(texto))
        self.conferir_unidades(registro)

    def test_preservacao_unicode_acentos_combinantes_emojis_zwj_e_bom(self):
        texto = "\ufeff Olá! é e\u0301 😀 👩‍👩‍👧‍👦 📝 漢字… fiquei; hã?!  "
        registro = self.segmentar(texto)
        self.assertEqual(registro["preparacao"]["trabalho"]["texto"], texto)
        self.conferir_unidades(registro)
        self.assertIn("e\u0301", registro["paragrafos"][0]["texto"])
        self.assertIn("👩‍👩‍👧‍👦", registro["paragrafos"][0]["texto"])

    def test_roundtrip_json_estrito_preserva_toda_a_saida(self):
        registro = self.segmentar("Olá 😀 e\u0301!\r\n\r\nDepois… fim", normalizar_crlf=True)
        for ascii_only in [False, True]:
            with self.subTest(ascii_only=ascii_only):
                recuperado = json.loads(json.dumps(registro, ensure_ascii=ascii_only, allow_nan=False))
                self.assertEqual(recuperado, registro)
                self.conferir_unidades(recuperado)
                self.conferir_validacao(validar_segmentacao(recuperado))

    def test_data_e_ids_fornecidos_tornam_saida_reproduzivel(self):
        self.assertEqual(self.segmentar("Um.\n\nDois."), self.segmentar("Um.\n\nDois."))
        registro = self.segmentar("Um.")
        self.assertEqual(datetime.fromisoformat(registro["registrado_em"]), datetime.fromisoformat(self.INSTANTE))

    def test_data_datetime_com_fuso_e_aceita(self):
        instante = datetime(2026, 10, 7, 15, 34, 56, tzinfo=timezone.utc)
        registro = self.segmentar("Texto.", registrado_em=instante)
        self.assertEqual(datetime.fromisoformat(registro["registrado_em"]), instante)

    def test_data_e_uuid_reais_sao_gerados_quando_omitidos(self):
        registro = segmentar_preparacao(self.preparar("Texto."))
        self.assertEqual(UUID(registro["segmentacao_id"]).version, 4)
        instante = datetime.fromisoformat(registro["registrado_em"].replace("Z", "+00:00"))
        self.assertIsNotNone(instante.utcoffset())

    def test_preparacao_recebida_nao_e_mutada_nem_preparada_novamente(self):
        preparacao = self.preparar("Um.\r\nDois.", normalizar_crlf=True)
        snapshot = copy.deepcopy(preparacao)
        with patch("preparacao.preparar_texto", side_effect=AssertionError("Não preparar novamente")):
            registro = segmentar_preparacao(preparacao)
        self.assertEqual(preparacao, snapshot)
        self.assertEqual(registro["preparacao"], snapshot)
        registro["preparacao"]["metadados_origem"]["fonte"]["etiquetas"].append("posterior")
        self.assertEqual(preparacao, snapshot)

    def test_contexto_anterior_atual_seguinte_e_extremidades(self):
        registro = self.segmentar("Um. Dois.\n\nTrês.")
        periodos = registro["periodos"]
        for ordem, periodo in enumerate(periodos):
            with self.subTest(ordem=ordem):
                contexto = contexto_periodo(registro, periodo["id"])
                self.assertEqual(contexto, {
                    "anterior": periodos[ordem - 1] if ordem else None,
                    "atual": periodo,
                    "seguinte": periodos[ordem + 1] if ordem + 1 < len(periodos) else None,
                })
        self.assertNotEqual(periodos[1]["paragrafo_id"], periodos[2]["paragrafo_id"])

    def test_contexto_recusa_id_ausente(self):
        registro = self.segmentar("Texto.")
        with self.assertRaises(ErroSegmentacao):
            contexto_periodo(registro, "periodo-inexistente")

    def test_erro_segmentacao_e_value_error(self):
        self.assertTrue(issubclass(ErroSegmentacao, ValueError))

    def test_recusa_hash_ou_mapa_inconsistente_da_preparacao(self):
        modificacoes = [
            lambda preparacao: preparacao["original"].__setitem__("sha256", "0" * 64),
            lambda preparacao: preparacao["trabalho"].__setitem__("sha256", "0" * 64),
            lambda preparacao: preparacao["mapa_trechos"].clear(),
            lambda preparacao: preparacao["mapa_trechos"][0]["trabalho"].__setitem__("inicio", 1),
        ]
        for alterar in modificacoes:
            with self.subTest(alteracao=alterar):
                preparacao = self.preparar(self.ORIGINAL, normalizar_crlf=True)
                alterar(preparacao)
                with self.assertRaises(ErroSegmentacao):
                    segmentar_preparacao(preparacao)

    def test_recusa_entrada_que_nao_e_preparacao(self):
        for entrada in [None, {}, [], "Texto", 42]:
            with self.subTest(entrada=entrada):
                with self.assertRaises(ErroSegmentacao):
                    segmentar_preparacao(entrada)

    def test_recusa_ids_e_datas_invalidos(self):
        casos = [
            {"segmentacao_id": ""}, {"segmentacao_id": 1}, {"segmentacao_id": True},
            {"registrado_em": "ontem"}, {"registrado_em": "2026-10-07T12:34:56"},
            {"registrado_em": datetime(2026, 10, 7)}, {"registrado_em": True},
        ]
        for opcoes in casos:
            with self.subTest(opcoes=opcoes):
                with self.assertRaises(ErroSegmentacao):
                    self.segmentar("Texto.", **opcoes)

    def test_limite_de_caracteres_e_explicito_e_respeitado(self):
        import segmentacao
        self.assertEqual(segmentacao.MAX_CARACTERES, 2 * 1024 * 1024)
        with patch("segmentacao.MAX_CARACTERES", 20):
            self.conferir_validacao(self.segmentar("a" * 20)["validacao"])
            with self.assertRaises(ErroSegmentacao):
                self.segmentar("a" * 21)

    def test_validacao_de_origem_tem_custo_constante_em_numero_de_tokens(self):
        preparacao = self.preparar("palavra " * 200 + "fim.")
        with patch("segmentacao.validar_registro", wraps=validar_registro) as conferir:
            registro = segmentar_preparacao(preparacao)
        self.assertGreater(len(registro["tokens"]), 200)
        self.assertGreaterEqual(conferir.call_count, 1)
        self.assertLessEqual(conferir.call_count, 3)

    def test_validar_segmentacao_nao_muta_saida(self):
        registro = self.segmentar("Um.\n\nDois.")
        snapshot = copy.deepcopy(registro)
        self.conferir_validacao(validar_segmentacao(registro))
        self.assertEqual(registro, snapshot)

    def test_validador_recusa_estrutura_incompleta(self):
        for valor in [None, {}, [], "texto"]:
            with self.subTest(valor=valor):
                with self.assertRaises(ErroSegmentacao):
                    validar_segmentacao(valor)
        for campo in ["schema_version", "etapa", "documento_id", "preparacao_id", "segmentacao_id", "registrado_em", "preparacao", "coordenadas", "processamento", "paragrafos", "periodos", "tokens", "separadores", "validacao"]:
            with self.subTest(campo=campo):
                registro = self.segmentar("Um.")
                del registro[campo]
                with self.assertRaises(ErroSegmentacao):
                    validar_segmentacao(registro)

    def test_validador_recusa_textos_intervalos_ordem_e_ids_adulterados(self):
        modificacoes = [
            lambda registro: registro["tokens"][0].__setitem__("texto", "adulterado"),
            lambda registro: registro["periodos"][0].__setitem__("texto", "adulterado"),
            lambda registro: registro["paragrafos"][0].__setitem__("texto", "adulterado"),
            lambda registro: registro["separadores"][0].__setitem__("texto", "x"),
            lambda registro: registro["tokens"][0]["original"].__setitem__("inicio", 999),
            lambda registro: registro["tokens"][0]["trabalho"].__setitem__("inicio", -1),
            lambda registro: registro["tokens"][0].__setitem__("ordem", 7),
            lambda registro: registro["periodos"][1].__setitem__("id", registro["periodos"][0]["id"]),
            lambda registro: registro["tokens"][0].__setitem__("id", ""),
            lambda registro: registro["tokens"].append(copy.deepcopy(registro["tokens"][0])),
            lambda registro: registro["separadores"].pop(),
        ]
        for alterar in modificacoes:
            with self.subTest(alteracao=alterar):
                registro = self.segmentar("\t Um. Dois.\r\n\r\nOutro. \t", normalizar_crlf=True)
                alterar(registro)
                with self.assertRaises(ErroSegmentacao):
                    validar_segmentacao(registro)

    def test_validador_recusa_relacoes_entre_unidades_adulteradas(self):
        modificacoes = [
            lambda registro: registro["tokens"][0].__setitem__("periodo_id", "inexistente"),
            lambda registro: registro["tokens"][0].__setitem__("paragrafo_id", registro["paragrafos"][1]["id"]),
            lambda registro: registro["periodos"][0].__setitem__("paragrafo_id", "inexistente"),
            lambda registro: registro["periodos"][0]["tokens"].clear(),
            lambda registro: registro["paragrafos"][0]["periodos"].clear(),
            lambda registro: registro["periodos"][0]["tokens"].append(registro["tokens"][-1]["id"]),
            lambda registro: registro["paragrafos"][0]["periodos"].append(registro["periodos"][-1]["id"]),
        ]
        for alterar in modificacoes:
            with self.subTest(alteracao=alterar):
                registro = self.segmentar("Um. Dois.\n\nOutro.")
                alterar(registro)
                with self.assertRaises(ErroSegmentacao):
                    validar_segmentacao(registro)

    def test_validador_nao_confia_no_flag_pronto(self):
        registro = self.segmentar("Texto.")
        registro["preparacao"]["original"]["sha256"] = "0" * 64
        registro["validacao"]["pronto_para_etapa_05"] = True
        registro["validacao"]["estado"] = "pronto_para_etapa_05"
        with self.assertRaises(ErroSegmentacao):
            validar_segmentacao(registro)

    def test_validador_recusa_metadados_de_processamento_ou_origem_inconsistentes(self):
        modificacoes = [
            lambda registro: registro.__setitem__("schema_version", "99.0.0"),
            lambda registro: registro.__setitem__("documento_id", "outro"),
            lambda registro: registro.__setitem__("preparacao_id", "outra"),
            lambda registro: registro["coordenadas"].__setitem__("unidade", "bytes_utf8"),
            lambda registro: registro["processamento"].__setitem__("idioma", "en"),
            lambda registro: registro["processamento"]["regras"].__setitem__("versao", "99.0.0"),
            lambda registro: registro["validacao"].__setitem__("pronto_para_etapa_05", False),
            lambda registro: registro["validacao"].__setitem__("verificacoes", []),
        ]
        for alterar in modificacoes:
            with self.subTest(alteracao=alterar):
                registro = self.segmentar("Texto.")
                alterar(registro)
                with self.assertRaises(ErroSegmentacao):
                    validar_segmentacao(registro)


if __name__ == "__main__":
    unittest.main()
