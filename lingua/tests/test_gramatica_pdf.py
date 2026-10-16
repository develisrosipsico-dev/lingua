import hashlib
import tempfile
import unittest
from pathlib import Path

from gramatica_pdf import ErroPreparacaoLivro, extrair_pdf, fragmentar_livro, preparar_extracao


class TokenizadorCaracteres:
    """Determinístico e propositalmente diferente de uma contagem de palavras."""

    def encode(self, texto, add_special_tokens=True, truncation=False):
        assert truncation is False
        return [1] * (sum(2 if ord(c) > 127 else 1 for c in texto) + (2 if add_special_tokens else 0))


def extracao_manual(textos, sumario=None):
    paginas = []
    for numero, blocos in enumerate(textos, 1):
        texto_pagina = "\n\n".join(blocos)
        registros, inicio = [], 0
        for ordem, texto in enumerate(blocos):
            registros.append({"id": f"pagina-{numero}:bloco-{ordem}", "ordem": ordem,
                              "texto": texto, "bbox": [10, 20 + ordem * 30, 200, 40 + ordem * 30],
                              "offset_inicio": inicio, "offset_fim": inicio + len(texto)})
            inicio += len(texto) + 2
        paginas.append({"pagina_pdf": numero, "pagina_rotulo": str(numero + 10),
                        "texto": texto_pagina, "blocos": registros})
    return {"obra": {"sha256_pdf": hashlib.sha256(b"pdf de teste").hexdigest()},
            "paginas": paginas, "sumario": sumario or []}


class FragmentacaoLivroTests(unittest.TestCase):
    def setUp(self):
        self.tokenizador = TokenizadorCaracteres()

    def conferir_origens(self, extracao, resultado):
        partes_blocos = {}
        paginas = {p["pagina_pdf"]: p for p in extracao["paginas"]}
        for indice, fragmento in enumerate(resultado["fragmentos"]):
            self.assertEqual(fragmento["linha_vetor"], indice)
            self.assertLessEqual(fragmento["tokens_embedding"], resultado["relatorio"]["max_tokens"])
            self.assertEqual(fragmento["tokens_embedding"], len(self.tokenizador.encode(fragmento["texto_embedding"])))
            self.assertEqual(fragmento["sha256_texto"], hashlib.sha256(fragmento["texto"].encode()).hexdigest())
            citacoes = []
            for origem in fragmento["origem"]:
                pagina = paginas[origem["pagina_pdf"]]
                texto = pagina["texto"][origem["offset_inicio"]:origem["offset_fim"]]
                citacoes.append(texto)
                partes_blocos.setdefault(origem["bloco_id"], []).append((origem["offset_bloco_inicio"], origem["offset_bloco_fim"], texto))
            self.assertEqual(fragmento["texto"], "\n\n".join(citacoes))
        for pagina in extracao["paginas"]:
            for bloco in pagina["blocos"]:
                if bloco["texto"].strip():
                    partes = partes_blocos[bloco["id"]]
                    self.assertEqual(partes[0][0], 0)
                    self.assertEqual(partes[-1][1], len(bloco["texto"]))
                    self.assertTrue(all(a[1] == b[0] for a, b in zip(partes, partes[1:])))
                    self.assertEqual("".join(p[2] for p in partes), bloco["texto"])

    def test_preserva_unicode_hifens_e_cobertura_sem_truncamento(self):
        texto = "A oração subor-\ndinada traz uma condição. " * 21 + "Fim.\n"
        extracao = extracao_manual([[texto]])
        resultado = fragmentar_livro(extracao, self.tokenizador, alvo_tokens=75, max_tokens=100)
        self.assertGreater(len(resultado["fragmentos"]), 5)
        self.conferir_origens(extracao, resultado)
        self.assertEqual(resultado["relatorio"]["caracteres_origem"], len(texto))
        self.assertFalse(resultado["relatorio"]["truncamento"])
        self.assertEqual(resultado["relatorio"]["normalizacoes"], [])

    def test_reune_blocos_e_mantem_paginas_e_referencias(self):
        extracao = extracao_manual([["Regra.", "Exemplo."], ["Ressalva."]])
        resultado = fragmentar_livro(extracao, self.tokenizador)
        self.assertEqual(len(resultado["fragmentos"]), 1)
        self.assertEqual(resultado["fragmentos"][0]["texto"], "Regra.\n\nExemplo.\n\nRessalva.")
        self.assertEqual([o["pagina_pdf"] for o in resultado["fragmentos"][0]["origem"]], [1, 1, 2])
        self.assertEqual(resultado["fragmentos"][0]["origem"][-1]["pagina_rotulo"], "12")
        self.conferir_origens(extracao, resultado)

    def test_secoes_distintas_nao_se_misturam_e_ids_sao_estaveis(self):
        extracao = extracao_manual([["Concordância."], ["Regência."]], [
            {"nivel": 1, "titulo": "Concordância", "pagina_pdf": 1},
            {"nivel": 1, "titulo": "Regência", "pagina_pdf": 2},
        ])
        resultado = fragmentar_livro(extracao, self.tokenizador)
        self.assertEqual(len(resultado["fragmentos"]), 2)
        self.assertEqual(resultado, fragmentar_livro(extracao, self.tokenizador))
        self.assertEqual(resultado["fragmentos"][0]["secao"], ["Concordância"])
        self.assertTrue(resultado["fragmentos"][0]["texto_embedding"].startswith("passage: Concordância\n\n"))
        self.assertEqual(resultado["fragmentos"][0]["seguinte_id"], resultado["fragmentos"][1]["id"])
        self.assertEqual(resultado["fragmentos"][1]["anterior_id"], resultado["fragmentos"][0]["id"])
        self.conferir_origens(extracao, resultado)

    def test_multiplas_secoes_na_pagina_nao_inventam_atribuicao(self):
        extracao = extracao_manual([["Regra e exemplo."]], [
            {"nivel": 1, "titulo": "Sintaxe", "pagina_pdf": 1},
            {"nivel": 2, "titulo": "Sujeito", "pagina_pdf": 1},
            {"nivel": 2, "titulo": "Predicado", "pagina_pdf": 1},
        ])
        resultado = fragmentar_livro(extracao, self.tokenizador)
        self.assertEqual(resultado["fragmentos"][0]["secao"], ["Sintaxe"])
        self.assertEqual(resultado["relatorio"]["avisos"][0]["codigo"], "secoes_na_mesma_pagina")

    def test_titulo_extenso_fica_nos_metadados_sem_truncar_citacao(self):
        titulo = "Título longo " * 60
        extracao = extracao_manual([["A oração tem sujeito."]], [{"nivel": 1, "titulo": titulo, "pagina_pdf": 1}])
        resultado = fragmentar_livro(extracao, self.tokenizador, alvo_tokens=70, max_tokens=100)
        self.assertEqual(resultado["fragmentos"][0]["secao"], [titulo])
        self.assertEqual(resultado["fragmentos"][0]["texto_embedding"], "passage: A oração tem sujeito.")
        self.assertEqual(resultado["relatorio"]["avisos"][0]["codigo"], "titulo_fora_embedding")
        self.conferir_origens(extracao, resultado)

    def test_palavra_longa_sem_espaco_nao_perde_caracteres(self):
        extracao = extracao_manual([["á" * 400]])
        resultado = fragmentar_livro(extracao, self.tokenizador, alvo_tokens=70, max_tokens=100)
        self.conferir_origens(extracao, resultado)

    def test_erro_explicito_se_um_caractere_nao_cabe(self):
        class TokenizadorImpossivel(TokenizadorCaracteres):
            def encode(self, texto, **opcoes):
                if "§" in texto:
                    return [1] * 1000
                return super().encode(texto, **opcoes)
        with self.assertRaisesRegex(ErroPreparacaoLivro, "caractere"):
            fragmentar_livro(extracao_manual([["§"]]), TokenizadorImpossivel(), alvo_tokens=70, max_tokens=100)

    def test_rejeita_offsets_corrompidos_e_pdf_sem_texto(self):
        extracao = extracao_manual([["Texto."]])
        extracao["paginas"][0]["blocos"][0]["offset_fim"] -= 1
        with self.assertRaisesRegex(ErroPreparacaoLivro, "offsets"):
            fragmentar_livro(extracao, self.tokenizador)
        with self.assertRaisesRegex(ErroPreparacaoLivro, "não contém texto"):
            fragmentar_livro(extracao_manual([["   "]]), self.tokenizador)

    def test_parametros_impossiveis_nao_sao_aceitos(self):
        for opcoes in ({"max_tokens": 513}, {"alvo_tokens": True}, {"alvo_tokens": 400, "max_tokens": 100}):
            with self.subTest(opcoes=opcoes), self.assertRaises(ErroPreparacaoLivro):
                fragmentar_livro(extracao_manual([["Texto."]]), self.tokenizador, **opcoes)


class ExtracaoLivroTests(unittest.TestCase):
    def test_pdf_real_preserva_blocos_metadados_rotulos_e_avisos(self):
        try:
            import pymupdf as fitz
        except ImportError:
            self.skipTest("PyMuPDF não instalado")
        with tempfile.TemporaryDirectory() as pasta:
            arquivo = Path(pasta) / "gramatica.pdf"
            pdf = fitz.open()
            pagina = pdf.new_page()
            pagina.insert_text((30, 50), "Primeira coluna: regra.")
            pagina.insert_text((330, 50), "Segunda coluna: exemplo.")
            pdf.new_page()
            pdf.set_toc([[1, "Sintaxe", 1]])
            pdf.set_page_labels([{"startpage": 0, "prefix": "", "style": "D", "firstpagenum": 11}])
            pdf.save(arquivo)
            pdf.close()
            resultado = extrair_pdf(arquivo, {"titulo": "Gramática de teste", "edicao": "1"})
            self.assertEqual(resultado["obra"]["sha256_pdf"], hashlib.sha256(arquivo.read_bytes()).hexdigest())
            self.assertEqual(resultado["obra"]["total_paginas"], 2)
            self.assertEqual(resultado["obra"]["metadados"]["titulo"], "Gramática de teste")
            self.assertEqual(resultado["paginas"][0]["pagina_rotulo"], "11")
            self.assertEqual(resultado["sumario"][0]["titulo"], "Sintaxe")
            self.assertEqual({a["codigo"] for a in resultado["relatorio"]["avisos"]}, {"ordem_leitura_ambigua", "pagina_sem_texto"})
            for pagina in resultado["paginas"]:
                for bloco in pagina["blocos"]:
                    self.assertEqual(pagina["texto"][bloco["offset_inicio"]:bloco["offset_fim"]], bloco["texto"])
            self.assertFalse(resultado["relatorio"]["ocr_executado"])
            self.assertEqual(resultado["relatorio"]["normalizacoes"], [])


class PreparacaoLivroTests(unittest.TestCase):
    def setUp(self):
        self.tokenizador = TokenizadorCaracteres()

    def conferir_mapa(self, extracao, fragmento):
        paginas = {p["pagina_pdf"]: p for p in extracao["paginas"]}
        for parte in fragmento["mapa_texto_preparado"]:
            texto = fragmento["texto"][parte["inicio"]:parte["fim"]]
            brutos = [paginas[f["pagina_pdf"]]["texto"][f["offset_inicio"]:f["offset_fim"]] for f in parte["origens"]]
            if parte["literal"]:
                self.assertEqual(texto, "".join(brutos))
            else:
                self.assertTrue(parte["normalizacao_id"])
                self.assertTrue(brutos)

    def test_cabecalho_repetido_so_e_excluido_quando_confirmado(self):
        extracao = extracao_manual([[f"Sintaxe {n}", "A mesma frase no corpo."] for n in range(1, 4)])
        for pagina in extracao["paginas"]:
            pagina.update(altura=800, largura=600)
            pagina["blocos"][0]["bbox"] = [20, 15, 300, 30]
            pagina["blocos"][1]["bbox"] = [20, 300, 400, 320]
        preparada = preparar_extracao(extracao)
        grupos = preparada["relatorio"]["candidatos_margem"]
        self.assertEqual(len(grupos), 1)
        self.assertEqual(grupos[0]["assinatura"], "sintaxe #")
        self.assertEqual(grupos[0]["paginas"], [1, 2, 3])
        self.assertEqual(len(preparada["preparacao"]["exclusoes"]), 0)
        confirmada = preparar_extracao(extracao, ids_excluir=grupos[0]["ids_blocos"])
        resultado = fragmentar_livro(confirmada, self.tokenizador)
        self.assertNotIn("Sintaxe 1", " ".join(f["texto"] for f in resultado["fragmentos"]))
        self.assertIn("Sintaxe 1", confirmada["paginas"][0]["texto"])
        self.assertEqual(len(confirmada["preparacao"]["exclusoes"]), 3)
        self.assertEqual(confirmada["paginas"], extracao["paginas"])

    def test_hifen_entre_blocos_e_paginas_exige_palavra_aprovada(self):
        extracao = extracao_manual([["O termo inte-\n"], ["grantes é retomado.", "O bem-\nestar e o guarda-\nchuva permanecem."]])
        sem_aprovacao = preparar_extracao(extracao)
        self.assertEqual(sem_aprovacao["preparacao"]["normalizacoes"], [])
        candidatos = sem_aprovacao["preparacao"]["candidatos_hifenizacao"]
        self.assertEqual(candidatos[0]["palavra_recomposta"], "integrantes")
        self.assertEqual(candidatos[0]["paginas"], [1, 2])
        preparada = preparar_extracao(extracao, recompor_palavras=["integrantes"])
        resultado = fragmentar_livro(preparada, self.tokenizador)
        texto = " ".join(f["texto"] for f in resultado["fragmentos"])
        self.assertIn("integrantes", texto)
        self.assertIn("bem-\nestar", texto)
        self.assertIn("guarda-\nchuva", texto)
        self.assertEqual(len(preparada["preparacao"]["normalizacoes"]), 1)
        self.assertEqual(preparada["paginas"], extracao["paginas"])
        self.assertIn("inte-", resultado["fragmentos"][0]["texto_extracao_bruta"])
        for fragmento in resultado["fragmentos"]:
            self.conferir_mapa(extracao, fragmento)

    def test_hifen_condicional_tem_registro_e_hifen_sem_quebra_nao_muda(self):
        extracao = extracao_manual([["A subor\u00ad\ndinada traz bem-estar e guarda-chuva."]])
        preparada = preparar_extracao(extracao)
        resultado = fragmentar_livro(preparada, self.tokenizador)
        self.assertEqual(resultado["fragmentos"][0]["texto"], "A subordinada traz bem-estar e guarda-chuva.")
        self.assertEqual(resultado["relatorio"]["normalizacoes"][0]["criterio"], "hifen_condicional")
        self.conferir_mapa(extracao, resultado["fragmentos"][0])

    def test_titulo_e_introducao_de_exemplo_acompanham_conteudo(self):
        extracao = extracao_manual([["Orações concessivas", "Observe o exemplo:", "Embora chova, sairei."]], [
            {"nivel": 1, "titulo": "Orações concessivas", "pagina_pdf": 1},
        ])
        preparada = preparar_extracao(extracao)
        self.assertEqual(len(preparada["preparacao"]["unidades"]), 1)
        resultado = fragmentar_livro(preparada, self.tokenizador, alvo_tokens=80, max_tokens=150)
        self.assertEqual(len(resultado["fragmentos"]), 1)
        self.assertIn("Embora chova", resultado["fragmentos"][0]["texto"])
        self.assertTrue(resultado["fragmentos"][0]["texto"].startswith("Orações concessivas"))
        self.conferir_mapa(extracao, resultado["fragmentos"][0])

    def test_titulo_orfao_fica_na_trilha_sem_vetor_so_titulo(self):
        extracao = extracao_manual([["Uma regra explicada."], ["Apêndice"]], [
            {"nivel": 1, "titulo": "Apêndice", "pagina_pdf": 2},
        ])
        preparada = preparar_extracao(extracao)
        self.assertEqual(preparada["preparacao"]["titulos_sem_conteudo"][0]["texto"], "Apêndice")
        resultado = fragmentar_livro(preparada, self.tokenizador)
        self.assertEqual(len(resultado["fragmentos"]), 1)
        self.assertEqual(resultado["fragmentos"][0]["texto"], "Uma regra explicada.")
        self.assertEqual(len(resultado["relatorio"]["titulos_sem_conteudo"]), 1)

    def test_pagina_nao_selecionada_e_exclusao_desconhecida_sao_explicitas(self):
        extracao = extracao_manual([["Capa."], ["Uma regra."], ["Índice."]])
        preparada = preparar_extracao(extracao, paginas_incluir=[2])
        self.assertEqual(preparada["preparacao"]["paginas_incluidas"], [2])
        self.assertEqual([e["motivo"] for e in preparada["preparacao"]["exclusoes"]], ["pagina_fora_intervalo"] * 2)
        self.assertEqual(fragmentar_livro(preparada, self.tokenizador)["fragmentos"][0]["origem"][0]["pagina_pdf"], 2)
        for opcoes in ({"paginas_incluir": [4]}, {"ids_excluir": ["inventado"]}, {"recompor_palavras": "subordinada"}):
            with self.subTest(opcoes=opcoes), self.assertRaises(ErroPreparacaoLivro):
                preparar_extracao(extracao, **opcoes)

    def test_unidade_extensa_e_dividida_com_aviso_e_fontes_validas(self):
        extracao = extracao_manual([["Exemplo:", "A oração tem sujeito e predicado. " * 15]])
        preparada = preparar_extracao(extracao)
        resultado = fragmentar_livro(preparada, self.tokenizador, alvo_tokens=80, max_tokens=120)
        self.assertGreater(len(resultado["fragmentos"]), 2)
        self.assertIn("unidades_divididas_por_limite", {a["codigo"] for a in resultado["relatorio"]["avisos"]})
        for fragmento in resultado["fragmentos"]:
            self.conferir_mapa(extracao, fragmento)
            self.assertLessEqual(fragmento["tokens_embedding"], 120)

    def test_cabecalho_entre_metades_da_palavra_precisa_sair_antes_da_juncao(self):
        extracao = extracao_manual([["Um ad-\n"], ["Sintaxe 42", "junto modifica o verbo."]])
        preparada = preparar_extracao(extracao, ids_excluir=["pagina-2:bloco-0"], recompor_palavras=["adjunto"])
        resultado = fragmentar_livro(preparada, self.tokenizador)
        self.assertEqual(resultado["fragmentos"][0]["texto"], "Um adjunto modifica o verbo.")
        self.assertEqual(preparada["preparacao"]["normalizacoes"][0]["origem"][1]["pagina_pdf"], 2)
        self.assertEqual(preparada["preparacao"]["exclusoes"][0]["texto"], "Sintaxe 42")
        self.conferir_mapa(extracao, resultado["fragmentos"][0])

    def test_pagina_so_com_cabecalho_excluido_exige_conferencia(self):
        extracao = extracao_manual([["Capítulo 1"], ["Texto explicativo."]])
        preparada = preparar_extracao(extracao, ids_excluir=["pagina-1:bloco-0"])
        aviso = next(a for a in preparada["preparacao"]["avisos"] if a["codigo"] == "pagina_sem_conteudo_apos_preparacao")
        self.assertEqual(aviso["pagina_pdf"], 1)

    def test_fonte_maior_em_tabela_nao_e_promovida_a_titulo_ou_removida(self):
        extracao = extracao_manual([["Uma explicação extensa. " * 10], ["Modo", "Tempo", "Pessoa"]])
        extracao["paginas"][0]["blocos"][0]["estilo"] = {"tamanho_mediano": 10}
        for bloco in extracao["paginas"][1]["blocos"]:
            bloco["estilo"] = {"tamanho_mediano": 18}
        extracao["relatorio"] = {"avisos": [{"codigo": "possivel_quadro_ou_tabela", "pagina_pdf": 2}]}
        preparada = preparar_extracao(extracao)
        self.assertEqual(preparada["preparacao"]["titulos_sem_conteudo"], [])
        resultado = fragmentar_livro(preparada, self.tokenizador)
        self.assertIn("Modo", " ".join(f["texto"] for f in resultado["fragmentos"]))
        self.assertFalse(preparada["preparacao"]["exclusoes"])

    def test_normalizacao_dividida_mantem_trilha_e_contagem_raw_sem_duplicar(self):
        extracao = extracao_manual([["A subor-\ndinada finaliza a frase."]])
        preparada = preparar_extracao(extracao, recompor_palavras=["subordinada"])
        resultado = fragmentar_livro(preparada, self.tokenizador, alvo_tokens=15, max_tokens=20)
        self.assertGreater(len(resultado["fragmentos"]), 1)
        self.assertEqual(resultado["relatorio"]["caracteres_origem"], len(extracao["paginas"][0]["texto"]))
        for fragmento in resultado["fragmentos"]:
            self.conferir_mapa(extracao, fragmento)


if __name__ == "__main__":
    unittest.main()
