import json
import unittest

from perfis_vetorizacao import E5_REVISAO, ErroPerfil, carregar_perfil, listar_perfis, validar_perfil


class TestPerfisVetorizacao(unittest.TestCase):
    def test_defaults_e5_revisao_imutavel_e_sem_capacidade_global(self):
        perfil = carregar_perfil()
        self.assertEqual(perfil['modelo']['revisao'], E5_REVISAO)
        self.assertEqual(perfil['dispositivo'], 'cpu')
        self.assertIsNone(perfil['limite_tokens'])
        self.assertNotIn('dimensao', perfil)
        self.assertTrue(perfil['somente_local'])
        self.assertIsNone(perfil['legado'])
        self.assertEqual(perfil, validar_perfil(json.loads(json.dumps(perfil))))

    def test_perfis_distintos_papeis_e_copias(self):
        perfis = listar_perfis()
        self.assertEqual(len(perfis), 5)
        self.assertEqual(carregar_perfil('e5_consulta')['prefixo'], 'query: ')
        self.assertEqual(carregar_perfil('e5_conteudo')['prefixo'], 'passage: ')
        perfis[0]['modelo']['revisao'] = 'alterado'
        self.assertEqual(carregar_perfil('e5_consulta')['modelo']['revisao'], E5_REVISAO)

    def test_analista_preserva_configuracao_da_busca_anterior(self):
        perfil = carregar_perfil('e5_analista')
        self.assertEqual(perfil['estrategia_consultas'], 'paragrafos_documento')
        self.assertEqual(perfil['limite_tokens'], 504)
        self.assertEqual(perfil['texto_documento'], 'original')
        self.assertEqual(perfil['agregacao'], 'nenhuma')
        self.assertEqual(perfil, validar_perfil(perfil))
        for opcoes in ({'prefixo': 'passage: '}, {'limite_tokens': 512},
                       {'texto_documento': 'trabalho'}, {'agregacao': 'media_simples'},
                       {'formato_armazenamento': 'float16'}, {'pooling': 'cls'}):
            with self.subTest(opcoes=opcoes), self.assertRaises(ErroPerfil):
                carregar_perfil('e5_analista', opcoes=opcoes)

    def test_multimodel_sem_prefixos_ou_limites_e5_obrigatorios(self):
        perfil = carregar_perfil(opcoes={'modelo': 'organizacao/modelo-maior', 'revisao': 'a'*40,
            'tokenizador': {'identificacao': 'organizacao/tokenizador', 'revisao': 'b'*40},
            'prefixo': '', 'pooling': 'cls', 'normalizacao': 'nenhuma', 'limite_tokens': 8192,
            'formato_armazenamento': 'float64', 'dispositivo': 'cuda:3', 'lote': 1,
            'fragmentacao': 'erro', 'agregacao': 'media_simples', 'texto_documento': 'original'})
        self.assertEqual(perfil['limite_tokens'], 8192)
        self.assertEqual(perfil['prefixo'], '')
        self.assertEqual(perfil, validar_perfil(perfil))

    def test_outro_modelo_exige_revisao_real_imutavel(self):
        for opcoes in ({'modelo': 'novo/modelo'}, {'revisao': 'main'}, {'revisao': ''},
                        {'modelo': {'identificacao': 'novo/modelo', 'revisao': 'a'*40}, 'revisao': 'b'*40},
                        {'tokenizador': 'outro/tokenizador'}):
            with self.subTest(opcoes=opcoes), self.assertRaises(ErroPerfil):
                carregar_perfil(opcoes=opcoes)

    def test_legado_explicito_corte_cabecalho_precision_distinta(self):
        perfil = carregar_perfil('e5_legado', opcoes={'cabecalho': 'Título\nSeção', 'corte_caracteres': 400,
            'dispositivo': 'cuda:2', 'precisao_inferencia': 'float16', 'lote': 32})
        self.assertEqual(perfil['legado']['composicao'], 'cabecalho_mais_lf_mais_texto')
        self.assertEqual(perfil['legado']['corte_caracteres'], 400)
        self.assertEqual(perfil['formato_armazenamento'], 'float16')
        self.assertEqual(perfil['legado']['compatibilidade'], 'nao_verificada')
        self.assertEqual(perfil, validar_perfil(perfil))
        juntado = carregar_perfil('e5_legado', opcoes={'juntar_paragrafos': True})
        self.assertTrue(juntado['legado']['juntar_paragrafos'])
        self.assertEqual(juntado['legado']['composicao'], 'cabecalho_mais_lf_mais_paragrafos')
        self.assertEqual(juntado, validar_perfil(juntado))
        for opcoes in ({'corte_caracteres': 2000}, {'cabecalho': ''}, {'juntar_paragrafos': False}):
            with self.assertRaises(ErroPerfil):
                carregar_perfil(opcoes=opcoes)
        with self.assertRaises(ErroPerfil):
            carregar_perfil('e5_legado', opcoes={'juntar_paragrafos': 1})

    def test_opcoes_invalidas_erro_explicito(self):
        for opcoes in ({'lote': True}, {'lote': 0}, {'limite_tokens': -1}, {'limite_tokens': True},
                        {'normalizacao': 'unitaria'}, {'dimensao': 123}, {'somente_local': 1},
                        {'dispositivo': 't4'}, {'precisao_inferencia': 'int8'}, {'prefixo': None},
                        {'foo': float('nan')}, {3: 'chave'}, {'cache_modelos': object()}):
            with self.subTest(opcoes=opcoes), self.assertRaises(ErroPerfil):
                carregar_perfil(opcoes=opcoes)
        for perfil_id in ('desconhecido', None, 1):
            with self.assertRaises(ErroPerfil):
                carregar_perfil(perfil_id)

    def test_fastembed_sha_e_precisao(self):
        perfil = carregar_perfil(opcoes={'backend': 'fastembed', 'arquivo_onnx': '/tmp/model.onnx',
            'modelo_onnx_sha256': 'a'*64, 'arquivos_onnx_sha256': {'model.onnx': 'a'*64, 'model.onnx_data': 'b'*64}})
        self.assertEqual(perfil, validar_perfil(perfil))
        for opcoes in ({'backend': 'fastembed', 'precisao_inferencia': 'float16'},
                        {'modelo_onnx_sha256': 'main'}, {'arquivos_onnx_sha256': {'../pesos': 'a'*64}}):
            with self.assertRaises(ErroPerfil):
                carregar_perfil(opcoes=opcoes)

    def test_snapshot_adulterado_nao_validado(self):
        for campo, valor in (('finalidade', 'inexistente'), ('versao', '2.0.0'), ('id', 'e5_conteudo'),
                            ('legado', {'ativo': True}), ('extra', 'não permitido')):
            perfil = carregar_perfil()
            perfil[campo] = valor
            with self.subTest(campo=campo), self.assertRaises(ErroPerfil):
                validar_perfil(perfil)
        perfil = carregar_perfil('e5_legado')
        perfil['legado']['compatibilidade'] = 'comprovada'
        with self.assertRaises(ErroPerfil):
            validar_perfil(perfil)


if __name__ == '__main__':
    unittest.main()
