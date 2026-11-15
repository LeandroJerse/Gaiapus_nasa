# Gaiapus — resultados v2, 14/11/2026

Leia também a [metodologia e limitações](gaiapus-17-validacao-espacial-v2.md). Todos os números abaixo são calculados a partir dos CSVs finais.

## Comparação pareada de treinamento

Foram preservadas 351 amostras e suas divisões. Ajuste até 2018; validação 2019–2021; teste de rendimento 2022–2024 não avaliado. Uberaba/Sinop permanecem fora do ajuste.

| Cultura | Suporte/modelo | MAE temporal kg/ha | MAE espaço-temporal kg/ha |
|---|---|---:|---:|
| Soja | Ponto — Ridge | 189.83 | 294.99 |
| Soja | Agricultura ponderada — Ridge | 191.77 | 305.07 |
| Soja | Ponto — Gradient Boosting | 208.53 | 320.20 |
| Soja | Agricultura ponderada — Gradient Boosting | 174.64 | 300.12 |
| Soja | Somente tendência/histórico — Ridge | 183.40 | 306.70 |
| Milho 2ª | Ponto — Ridge | 1262.82 | 1179.55 |
| Milho 2ª | Agricultura ponderada — Ridge | 1321.71 | 1113.53 |
| Milho 2ª | Ponto — Gradient Boosting | 1582.17 | 1417.98 |
| Milho 2ª | Agricultura ponderada — Gradient Boosting | 1548.61 | 1267.29 |
| Milho 2ª | Somente tendência/histórico — Ridge | 1413.57 | 1305.09 |

## Interpretação por cultura

- **Soja**, Ridge: temporal_validation: aumento de 1.0% no MAE; spatiotemporal_validation: aumento de 3.4% no MAE.
- **Milho de segunda safra**, Ridge: temporal_validation: aumento de 4.7% no MAE; spatiotemporal_validation: redução de 5.6% no MAE.

Mudança de suporte espacial não garante redução de erro. Avaliar também `model_comparison_by_state.csv`: a média pode esconder piora em MG ou MT. Há apenas 18 exemplos na validação temporal e seis na espaço-temporal por cultura; não há evidência suficiente para afirmar significância estatística ou precisão de talhão.

A máscara é de agricultura genérica, não específica de cultura. Os mapas são versões retrospectivas e usam defasagem de dois anos (limitados a 2020). Não houve correção de viés da chuva com os anos de teste e nenhuma rede neural foi introduzida para mascarar limitações de dados.

## Campo Novo do Parecis

Chuva: estação local A905 com 105 dias completos em 2023 e 29 em 2024. Sapezal A911 fornece diagnóstico regional de 2024 completo, a aproximadamente 103 km; suas medições não foram copiadas para preencher a estação local.
Solo: obtido contexto completo de 20 camadas/propriedades no ponto agrícola ESA (-57.881944, -13.651389), a 0.59 km do ponto inicial. É um proxy espacial declarado, não preenchimento do ponto original nem análise de solo de talhão.

## Arquivos para a equipe

- `data/processed/training_v2/rain_station_validation.csv`: métricas por estação/ano.
- `rain_monthly_validation.csv`: cobertura e diferenças por mês; atenção a `full_month`.
- `crop_cell_weights.csv`: área e peso por município/ano/célula.
- `research_samples.csv`: entradas e alvos pareados para pesquisa.
- `model_comparison.csv` e `model_comparison_by_state.csv`: evidência para decidir próximos experimentos.
- `data/models/research_v2/manifest.json`: hashes e features específicas de cada artefato.
- `delivery_manifest.json`: inventário final com hashes, contagens e volume.

## Próximo passo recomendado

Manter todos os modelos como pesquisa. Priorizar máscara por cultura/safra, validação de representatividade da chuva e obtenção de plantio/cultivar/manejo/colheita de talhões. A API pode publicar histórico com proveniência e indicadores de qualidade, mas ainda não uma recomendação quantitativa de produtividade ou irrigação de fazenda.
