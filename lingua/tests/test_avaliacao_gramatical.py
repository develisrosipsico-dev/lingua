import copy
import json
import unittest
from unittest.mock import patch

from avaliacao_gramatical import (
    REFERENCIA, avaliar_referencia, comparar_tokens, formatar_markdown,
    resumir, validar_referencia,
)


class AvaliacaoGramaticalTests(unittest.TestCase):
    def referencia(self):
        return json.loads(REFERENCIA.read_text(encoding='utf-8'))

    def caso(self):
        return self.referencia()['casos'][-2]

    def previsoes_corretas(self, caso):
        return [copy.deepcopy(alvo) for alvo in caso['alvos']]

    def test_referencia_inclui_documento_original_controles_e_locucoes(self):
        ref = self.referencia()
        snapshot = copy.deepcopy(ref)
        validar_referencia(ref)
        self.assertEqual(ref, snapshot)
        self.assertGreaterEqual(len(ref['casos']), 10)
        doc = ref['casos'][0]
        self.assertEqual(doc['id'], 'documento-8')
        self.assertTrue({'afirmem', 'redefine', 'examine', 'silenciosamente', 'meramente', 'premissa'} <=
                        {a['texto'] for a in doc['alvos']})
        self.assertEqual([g['texto'] for g in doc['grupos_verbais']],
                         ['passa a reconhecer', 'continue orientando'])
        controle = next(c for c in ref['casos'] if c['id'] == 'passagem-sem-locucao')
        self.assertEqual(controle['grupos_verbais'], [])
        self.assertGreater(len(doc['texto']), 1000)

    def test_comparador_mede_dimensoes_separadas_sem_modificar_previsoes(self):
        caso = self.caso()
        previsoes = self.previsoes_corretas(caso)
        previsoes[0]['lema'] = 'divergencia'
        previsoes[0]['morfologia']['Gender'] = 'Masc'
        previsoes[0]['morfologia']['PronType'] = 'Dem'
        antes = copy.deepcopy(previsoes)
        resultados = comparar_tokens(caso['texto'], previsoes, caso)
        self.assertEqual(previsoes, antes)
        self.assertTrue(resultados[0]['acertos']['pos'])
        self.assertFalse(resultados[0]['acertos']['lema'])
        self.assertFalse(resultados[0]['acertos']['morfologia'])
        self.assertTrue(resultados[0]['tracos']['Number']['acerto'])
        self.assertFalse(resultados[0]['tracos']['Gender']['acerto'])
        # O traço extra não anotado não entra na avaliação por chave.
        self.assertNotIn('PronType', resultados[0]['tracos'])
        resumo = resumir([{'resultados': resultados}])
        self.assertEqual(resumo['metricas']['pos']['acertos'], 2)
        self.assertEqual(resumo['metricas']['lema']['acertos'], 1)
        self.assertNotIn('acuracia_global', resumo)

    def test_tokens_nao_alinhados_contam_como_divergencia_sem_deslocar_alvo(self):
        caso = self.caso()
        previsoes = self.previsoes_corretas(caso)[1:]
        resultados = comparar_tokens(caso['texto'], previsoes, caso)
        self.assertFalse(resultados[0]['alinhado'])
        self.assertIsNone(resultados[0]['obtido'])
        self.assertTrue(all(not v for v in resultados[0]['acertos'].values()))
        resumo = resumir([{'resultados': resultados}])
        self.assertEqual(resumo['alvos_alinhados'], 1)
        self.assertEqual(resumo['metricas']['pos']['avaliados'], 2)

    def test_cabeca_e_relacao_sao_avaliadas_separadamente(self):
        caso = self.caso()
        previsoes = self.previsoes_corretas(caso)
        previsoes[0]['cabeca_original'] = copy.deepcopy(previsoes[0]['original'])
        resultados = comparar_tokens(caso['texto'], previsoes, caso)
        self.assertTrue(resultados[0]['acertos']['dependencia'])
        self.assertFalse(resultados[0]['acertos']['cabeca_original'])
        self.assertFalse(resultados[0]['acertos']['dependencia_e_cabeca'])

    def test_morfologia_ausente_e_registrada_como_divergencia(self):
        caso = self.caso()
        previsoes = self.previsoes_corretas(caso)
        previsoes[0]['morfologia'] = None
        resultados = comparar_tokens(caso['texto'], previsoes, caso)
        self.assertFalse(resultados[0]['acertos']['morfologia'])
        self.assertIsNone(resultados[0]['tracos']['Gender']['obtido'])

    def test_rejeita_limites_texto_duplicado_e_caso_incompativel(self):
        ref = self.referencia()
        ref['casos'][0]['alvos'][0]['original']['fim'] += 1
        with self.assertRaises(ValueError):
            validar_referencia(ref)
        caso = self.caso()
        previsoes = self.previsoes_corretas(caso)
        with self.assertRaises(ValueError):
            comparar_tokens(caso['texto'], previsoes + previsoes[:1], caso)
        with self.assertRaises(ValueError):
            comparar_tokens('outro texto', previsoes, caso)

    def test_modelo_ausente_e_recusado_antes_de_carregar_sem_download(self):
        with patch('spacy.util.get_installed_models', return_value=['pt_core_news_sm']), \
             patch('spacy.load') as carregar:
            with self.assertRaisesRegex(ValueError, 'Modelos não instalados'):
                avaliar_referencia(self.referencia(), ['modelo_inexistente'])
            carregar.assert_not_called()

    def test_relatorio_markdown_preserva_escopo_e_registra_divergencias(self):
        caso = self.caso()
        previsoes = self.previsoes_corretas(caso)
        previsoes[0]['lema'] = 'incorreto'
        casos = [{'id': caso['id'], 'resultados': comparar_tokens(caso['texto'], previsoes, caso)}]
        relatorio = {'gerado_em': '2026-10-16', 'escopo': 'Somente alvos.',
                     'modelos': [{'modelo': 'controle_unitario', 'versao_modelo': 'teste',
                                  'versao_spacy': 'teste', 'componentes': [],
                                  'tempo_carga_segundos': 0, 'tempo_inferencia_segundos': 0,
                                  'resumo': resumir(casos), 'casos': casos}]}
        texto = formatar_markdown(relatorio)
        self.assertIn('Somente alvos.', texto)
        self.assertIn('incorreto', texto)
        self.assertIn('não medem desempenho no Mac', texto)
        self.assertIn('referência pequena', texto)


if __name__ == '__main__':
    unittest.main()
