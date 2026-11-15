"""Generate evidence-linked handoff results from actual v2 outputs."""
import csv
import json
from pathlib import Path
from data.ingest.v2_common import RAW,OUT,sha,save_json


def read_csv(name):
    with (OUT/name).open(encoding='utf-8-sig') as stream:return list(csv.DictReader(stream))


def main():
    rows=read_csv('model_comparison.csv');index={(r['crop'],r['variant'],r['model'],r['scope']):r for r in rows}
    soil=json.loads((OUT/'campo_agricultural_soil_resolution.json').read_bytes())
    climate=json.loads((OUT/'weighted_build_manifest.json').read_bytes())
    lines=['# Gaiapus — resultados v2, 14/11/2026','',
           'Leia também a [metodologia e limitações](gaiapus-17-validacao-espacial-v2.md). Todos os números abaixo são calculados a partir dos CSVs finais.',
           '', '## Comparação pareada de treinamento','',
           f"Foram preservadas {climate['samples']} amostras e suas divisões. Ajuste até 2018; validação 2019–2021; teste de rendimento 2022–2024 não avaliado. Uberaba/Sinop permanecem fora do ajuste.",
           '', '| Cultura | Suporte/modelo | MAE temporal kg/ha | MAE espaço-temporal kg/ha |','|---|---|---:|---:|']
    for crop in ('soy','maize2'):
        for variant,model,label in [('point_v1','ridge','Ponto — Ridge'),('weighted_v2','ridge','Agricultura ponderada — Ridge'),
                                     ('point_v1','hist_gradient_boosting','Ponto — Gradient Boosting'),('weighted_v2','hist_gradient_boosting','Agricultura ponderada — Gradient Boosting'),
                                     ('point_v1','trend_history_only','Somente tendência/histórico — Ridge')]:
            a=index[(crop,variant,model,'temporal_validation')];b=index[(crop,variant,model,'spatiotemporal_validation')]
            lines.append(f"| {'Soja' if crop=='soy' else 'Milho 2ª'} | {label} | {float(a['mae_kg_ha']):.2f} | {float(b['mae_kg_ha']):.2f} |")
    lines+=['','## Interpretação por cultura','']
    for crop in ('soy','maize2'):
        effects=[]
        for scope in ('temporal_validation','spatiotemporal_validation'):
            before=float(index[(crop,'point_v1','ridge',scope)]['mae_kg_ha']);after=float(index[(crop,'weighted_v2','ridge',scope)]['mae_kg_ha'])
            effects.append(f"{scope}: {'redução' if after<before else 'aumento'} de {abs(100*(after-before)/before):.1f}% no MAE")
        lines.append(f"- **{'Soja' if crop=='soy' else 'Milho de segunda safra'}**, Ridge: {'; '.join(effects)}.")
    lines += ['', 'Mudança de suporte espacial não garante redução de erro. Avaliar também `model_comparison_by_state.csv`: a média pode esconder piora em MG ou MT. Há apenas 18 exemplos na validação temporal e seis na espaço-temporal por cultura; não há evidência suficiente para afirmar significância estatística ou precisão de talhão.',
              '', 'A máscara é de agricultura genérica, não específica de cultura. Os mapas são versões retrospectivas e usam defasagem de dois anos (limitados a 2020). Não houve correção de viés da chuva com os anos de teste e nenhuma rede neural foi introduzida para mascarar limitações de dados.',
              '', '## Campo Novo do Parecis','',
              'Chuva: estação local A905 com 105 dias completos em 2023 e 29 em 2024. Sapezal A911 fornece diagnóstico regional de 2024 completo, a aproximadamente 103 km; suas medições não foram copiadas para preencher a estação local.']
    if soil['agricultural_context_available']:
        p=next(r for r in soil['attempts'] if r['valid_layers']==20)
        lines.append(f"Solo: obtido contexto completo de 20 camadas/propriedades no ponto agrícola ESA ({p['longitude']:.6f}, {p['latitude']:.6f}), a {p['distance_km']:.2f} km do ponto inicial. É um proxy espacial declarado, não preenchimento do ponto original nem análise de solo de talhão.")
    else:lines.append('Solo agrícola: as tentativas não produziram contexto completo; a ausência permanece registrada. O contexto próximo urbano não foi promovido a solo agrícola.')
    lines+=['','## Arquivos para a equipe','',
            '- `data/processed/training_v2/rain_station_validation.csv`: métricas por estação/ano.',
            '- `rain_monthly_validation.csv`: cobertura e diferenças por mês; atenção a `full_month`.',
            '- `crop_cell_weights.csv`: área e peso por município/ano/célula.',
            '- `research_samples.csv`: entradas e alvos pareados para pesquisa.',
            '- `model_comparison.csv` e `model_comparison_by_state.csv`: evidência para decidir próximos experimentos.',
            '- `data/models/research_v2/manifest.json`: hashes e features específicas de cada artefato.',
            '- `delivery_manifest.json`: inventário final com hashes, contagens e volume.',
            '', '## Próximo passo recomendado','',
            'Manter todos os modelos como pesquisa. Priorizar máscara por cultura/safra, validação de representatividade da chuva e obtenção de plantio/cultivar/manejo/colheita de talhões. A API pode publicar histórico com proveniência e indicadores de qualidade, mas ainda não uma recomendação quantitativa de produtividade ou irrigação de fazenda.']
    Path('docs/superpowers/plans/gaiapus-18-resultados-v2.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('\n'.join(lines[:25]))


if __name__=='__main__':main()
