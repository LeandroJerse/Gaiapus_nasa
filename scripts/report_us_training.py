"""Read already-scored US research artifacts and render a factual Markdown handoff."""
from pathlib import Path
import importlib.metadata
import json

from data.ingest.us_data import ROOT, RAW, OUT, PROTOCOL, PROTOCOL_PATH, sha, save_json


def main():
    score = json.loads((OUT / 'test_score.json').read_text())
    features = json.loads((OUT / 'feature_manifest.json').read_text())
    raw = json.loads((RAW / 'manifest.json').read_text())
    freezes = {s: json.loads((OUT / s / 'freeze.json').read_text()) for s in PROTOCOL['scopes']}
    models = ROOT / 'data/models/research_us_v1'
    sizes = {name: sum(p.stat().st_size for p in path.rglob('*') if p.is_file()) for name, path in [('raw', RAW), ('processed', OUT), ('models', models)]}
    environment = {name: importlib.metadata.version(name) for name in ['torch', 'numpy', 'pandas', 'scikit-learn', 'joblib', 'requests', 'pytest']}
    save_json(OUT / 'environment.json', environment)
    names = {'IA_soy': 'Iowa — soja', 'IA_maize': 'Iowa — milho', 'NE_soy': 'Nebraska — soja', 'NE_maize': 'Nebraska — milho', 'KS_wheat_winter': 'Kansas — trigo de inverno'}
    lines = ['# Gaiapus EUA — resultados executados em 15/11/2026', '',
             '**Conclusão:** a primeira rodada foi coletada, treinada e avaliada. Nenhum dos cinco escopos satisfez o protocolo de confirmação de ganho. Nenhum modelo foi promovido para produção.', '',
             'O Codex executou a rodada; este documento lê os artefatos salvos. Não é uma proposta de resultado. Plano e comandos: [coleta e treinamento](2026-11-15-gaiapus-eua-usda-treinamento.md).', '',
             '## 1. O que foi executado', '',
             '- 150 condados: 50 por estado, com dez por estado reservados para avaliação espacial.',
             f"- USDA oficial: {raw['counts']['development']:,} registros de rendimento de desenvolvimento, {raw['counts']['sealed_test']:,} de teste e {raw['counts']['ancillary']:,} registros auxiliares. Contagens incluem registros suprimidos; eles não viram alvos zero.",
             '- NASA POWER: 2000–2024, seis variáveis diárias, ponto interno Census por condado; não é clima ponderado por área cultivada.',
             f"- Matrizes: {features['rows']['selection']:,} linhas de seleção; {features['rows']['final_fit']:,} linhas de desenvolvimento final; {features['rows']['test_inputs']} entradas de teste sem alvos. As matrizes incluem condados reservados; os filtros de fit os excluem.",
             f"- {len(features['exclusions'])} exclusões de linhas por alvo ausente entre as duas matrizes de desenvolvimento; não são necessariamente safras distintas. Nenhuma exclusão por lacuna climática nesta execução.",
             '- Cinco modelos finais por família: Ridge, HGB e MLP, além de quatro referências por escopo. Cada MLP final é a média de três sementes treinadas em CUDA.',
             '- 45 testes passaram: 28 novos e 17 testes existentes de balanço hídrico/clima. Uma revisão independente de código encontrou e ajudou a corrigir o bloqueio após interrupção da avaliação.', '',
             '## 2. Resultado primário: respeitar o escolhido na validação', '',
             'MAE em kg/ha, média dos erros médios por condado. “Melhor referência no teste” é uma comparação descritiva, não uma nova seleção para servir.', '',
             '| Escopo | Escolhido antes do teste | MAE nos condados conhecidos | Melhor referência no teste | MAE na reserva espacial | Pares conhecidos / espaciais | Status |',
             '|---|---|---:|---:|---:|---|---|']
    for scope, result in score['scopes'].items():
        winner = result['selected_on_validation']
        seen = result['subgroups']['seen_counties']['metrics']
        held = result['subgroups']['spatial_holdout']['metrics']
        ref = min(['last', 'mean3', 'trend', 'history_ridge'], key=lambda k: seen[k]['mae_kg_ha'])
        lines.append(f"| {names[scope]} | {winner} | {seen[winner]['mae_kg_ha']:.1f} | {ref}: {seen[ref]['mae_kg_ha']:.1f} | {held[winner]['mae_kg_ha']:.1f} | {seen[winner]['pairs']} / {held[winner]['pairs']} | {result['status']} |")
    lines += ['', 'Todos os conjuntos espaciais ficaram abaixo dos 30 pares observados exigidos, devido à ausência/supressão de rótulos. Os dez condados foram reservados, mas nem todas as três safras possuem rendimento publicado aproveitável. Não reduzir o limiar depois de observar essa limitação.', '',
              '## 3. Comparação secundária dos candidatos congelados', '',
              'Esses candidatos também foram definidos antes do teste. A tabela não autoriza escolher retrospectivamente o vencedor do teste.', '',
              '| Escopo | Grupo | Ridge | HGB | MLP | Último histórico | Média 3 | Tendência | Ridge histórico |',
              '|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for scope, result in score['scopes'].items():
        for group, part in result['subgroups'].items():
            m = part['metrics']
            vals = ' | '.join(f"{m[name]['mae_kg_ha']:.1f}" for name in ['ridge', 'hgb', 'mlp', 'last', 'mean3', 'trend', 'history_ridge'])
            lines.append(f'| {names[scope]} | {group} | {vals} |')
    lines += ['', 'Leitura: o MLP de soja em Iowa teve bom resultado exploratório nos condados conhecidos, mas piora relativa na reserva espacial. O Ridge de milho em Iowa e os candidatos lineares/não lineares de trigo merecem estudo futuro, não promoção nesta avaliação. Nebraska mostra necessidade de investigar representatividade climática, manejo/irrigação e generalização; esta rodada não identifica uma causa única do erro.', '',
              '## 4. Intervalos e cobertura', '',
              'Diferença = MAE do escolhido menos MAE da referência; negativo favorece o escolhido. Bootstrap pareado por condado, 2.000 reamostragens; anos mantidos fixos. Limite ajustado controla cinco escopos × quatro referências. Esses intervalos não estimam a incerteza de todos os anos futuros.', '',
              '| Escopo, conhecidos | Referência | Diferença kg/ha | IC95% | Limite superior ajustado |',
              '|---|---|---:|---|---:|']
    for scope, result in score['scopes'].items():
        for reference, comparison in result['subgroups']['seen_counties']['comparisons'].items():
            lo, hi = comparison['ci95']
            lines.append(f"| {names[scope]} | {reference} | {comparison['delta_mae_kg_ha']:.1f} | [{lo:.1f}; {hi:.1f}] | {comparison['family_control_upper']:.1f} |")
    lines += ['', '| Escopo | Previsões | Rótulos observados | Rótulos ausentes |', '|---|---:|---:|---:|']
    for scope, r in score['scopes'].items():
        lines.append(f"| {names[scope]} | {r['predicted_pairs']} | {r['observed_pairs']} | {r['missing_labels']} |")
    lines += ['', 'Detalhes por ano, RMSE, viés, erro relativo e R² estão no JSON de score. “MAE / média do rendimento” não equivale a uma porcentagem de acurácia.', '',
              '## 5. GPU e inferência CPU', '', '| Escopo | Épocas por semente | Pico VRAM MB | Maior diferença CPU/CUDA kg/ha |', '|---|---:|---:|---:|']
    for scope, f in freezes.items():
        logs = f['mlp_final_seeds']
        lines.append(f"| {names[scope]} | {logs[0]['epochs']} | {max(x['peak_vram_mb'] for x in logs):.2f} | {f['cpu_parity_kg_ha']['mlp']:.8f} |")
    lines += ['', 'Hardware: NVIDIA GeForce RTX 4070 Ti; todos os logs de MLP final indicam `cuda:0`. Foi executado forward/backward/update real antes do treino. Os artefatos foram recarregados em processo novo, com inferência CPU reproduzindo as previsões salvas sem carregar rótulos.', '',
              'Houve aviso do joblib porque `wmic` não está disponível para contar núcleos físicos no Windows; ele usou núcleos lógicos. Isso não impediu treino, paridade ou pontuação.', '',
              'Ambiente observado:', '', '```json', json.dumps(environment, indent=2), '```', '',
              '## 6. Integridade, armazenamento e limites', '',
              f"- Hash do protocolo: `{sha(PROTOCOL_PATH)}`.",
              f"- Hash do score: `{sha(OUT / 'test_score.json')}`.",
              f"- Download nacional processado em fluxo: {raw['network_bytes'] / 1e9:.3f} GB de tráfego; sem arquivo nacional persistido.",
              f"- Brutos selecionados: {sizes['raw'] / 1e6:.2f} MB; processados: {sizes['processed'] / 1e6:.2f} MB; modelos: {sizes['models'] / 1e6:.2f} MB (unidades decimais).",
              '- `test_consumed.json` foi criado antes da leitura de rótulos. Treino e score recusam nova execução desta versão depois desse marcador.',
              '- Modelos brasileiros, front/backend e registro prospectivo não foram alterados.',
              '- A avaliação é retrospectiva, com exposição prévia parcial a snippets públicos documentada. Snapshots revisados USDA/POWER não provam disponibilidade histórica na decisão.',
              '- Só há um corte por cultura e calendários hipotéticos. O teste de 2024 continua usando histórico até 2020 por desenho de avaliação fixa.',
              '- Não há prova de desempenho em talhão, de generalização para todos os EUA ou de efeito causal de manejo.', '',
              '## 7. Arquivos de continuidade', '',
              '- Protocolo: `data/frozen/us_training_v1_protocol.json`.',
              '- Coleta/manifesta: `data/raw/us_training_v1/`.',
              '- Features, congelamentos e score: `data/processed/us_training_v1/`.',
              '- Artefatos CPU e pesos: `data/models/research_us_v1/`.',
              '- Código: `data/ingest/us_data.py`, `us_features.py`, `us_train.py`.',
              '- Testes: `tests/test_us_data.py`, `test_us_features.py`, `test_us_train.py`.', '',
              '## 8. Como continuar', '',
              'Não continuar ajustando modelos contra 2022–2024 como se esse período ainda fosse teste independente. Preservar v1; definir v2 com hipóteses explícitas, avaliação interna temporal e outra confirmação elegível. Priorizar cobertura de rótulos, representação espacial/cultivada do clima, irrigação do Nebraska e calendário do trigo. Avaliar fontes novas uma a uma. Antes de qualquer uso no app, exportar um contrato EUA próprio e manter status experimental; não reutilizar a aprovação dos modelos brasileiros.', '']
    destination = ROOT / 'docs/superpowers/plans/2026-11-15-gaiapus-eua-resultados.md'
    destination.write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps({'report': str(destination), 'sizes_bytes': sizes, 'score_sha256': sha(OUT / 'test_score.json')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
