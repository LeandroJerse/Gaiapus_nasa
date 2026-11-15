# Execução — atributos hídricos no treinamento

- [x] Construir atributos causais sobre as linhas aos 60/90 dias inclusivos, sem consultar dias posteriores; fases futuras ausentes, nunca déficit zero observado.
- [x] Testar invariância a alterações no futuro e rejeição de lacunas; bloquear uso operacional sem vintage/publicação.
- [x] Ajustar Ridge/HGB com treino até 2016, embargo 2017–2018, validação 2019–2021; manter Uberaba/Sinop fora do ajuste e teste 2022–2024 fechado.
- [x] Comparar tendência, clima, hídrico e clima+hídrico nos mesmos pares; preservar resultados v1/v2 como referência não diretamente equivalente.
- [x] Registrar métricas MG/MT, artefatos, proveniência, revisão independente e guia operacional.

Decisões: CAD AD4/água inicial 70% são hipóteses fixas, não parâmetros ajustados ao rendimento. Não usar cenários sintéticos como novos rótulos. Excluir histórico de rendimento das entradas para evitar dependência de datas de publicação não reconstruídas. Embargo conservador de dois anos, não prova de disponibilidade das revisões IBGE. IMERG Final/POWER retrospectivos impedem alegar avaliação operacional point-in-time. Modelos novos permanecem de pesquisa. Comparação justa é a ablação interna com mesmos anos, cortes e entradas, não a diferença bruta contra v1/v2.

## Evidência de execução

- Três testes inicialmente falharam pela ausência do construtor; passaram após implementação. Acrescentados isolamento de treino e equivalência física. Suíte completa: 30/30.
- 542 linhas de atributos, 28 modelos, 56 métricas, 672 predições de validação; zero linhas do teste final extraídas.
- Revisão independente `review_hydro_training`: sem bloqueadores para pesquisa retrospectiva. Recalculou os 16 pares a partir das predições (erro máximo 2,84e-13), conferiu splits e hashes dos 28 modelos. Melhora em 7/16, piora em 9/16.
- Ressalvas da revisão incorporadas ao guia 20: publicação/vintage não resolvida, gate depende de metadados confiáveis, milho não é sucessão validada, comparação anterior apenas contextual. Nenhum item menor adiado.
