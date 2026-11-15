# Gaiapus — início operacional: de onde vêm os dados e o que fazer primeiro

**Próxima sessão, 15/11/2026:** [retomada da coleta autenticada e do treinamento](gaiapus-22-retomada-2026-11-15.md).

**Coleta operacional mais recente:** [IMERG Early/Late e controle de disponibilidade](gaiapus-21-imerg-operacional-e-disponibilidade.md). Piloto real com três produtos e oito células; 6.576 granulos catalogados. Autenticação do navegador concluída, mas histórico integral e versões disponíveis no passado ainda não estão prontos para treino operacional.

**Atualização mais recente:** [treinamento com déficit hídrico por fase, v3](gaiapus-20-treinamento-com-deficit-hidrico.md): 28 modelos, cortes de 60/90 dias, ablação pareada e teste 2022–2024 preservado. Ganhos mistos; a disponibilidade histórica das versões NASA/IBGE ainda impede uso operacional.

**Motor físico:** [ET0, balanço hídrico e seca por fase implementados](gaiapus-19-balanco-hidrico-et0.md): 4.464 cenários MG/MT, com validação numérica e contrato para a futura API. Começar por esse guia para os cenários físicos; ele não estima produtividade validada.

**Base espacial anterior:** [validação INMET e ponderação agrícola v2](gaiapus-17-validacao-espacial-v2.md), com [resultados do novo treinamento](gaiapus-18-resultados-v2.md). A máscara é agrícola genérica; as lacunas locais de Campo Novo não foram transformadas em observações artificiais.

**Atualização de preparação MG/MT (13–14/11):** comece agora pela [base de treinamento efetivamente coletada](gaiapus-13-base-de-treinamento.md), [catálogo/limpeza](gaiapus-14-catalogo-e-qualidade.md), [treinamento reproduzível](gaiapus-15-protocolo-de-treinamento.md) e [arquitetura/tarefas](gaiapus-16-arquitetura-e-execucao.md). Inclui oito municípios, soja/milho 2ª, 2001–2024 e baselines de pesquisa. O roteiro abaixo preserva o contexto do primeiro demonstrador.

**Recorte inicial:** soja em um talhão piloto consentido em Uberlândia (MG); usar o recorte público demonstrativo enquanto não houver acesso à fazenda. A cultura é uma proposta inicial, a confirmar com a equipe e um produtor. O navegador explora o mundo; o motor agrícola da competição atende apenas as combinações brasileiras publicadas. O artefato anterior é referência visual.

1. [Catálogo e chamadas](gaiapus-06-fontes-e-coleta.md): origem, API, forma de acesso e primeira extração.
2. [Limpeza e dados de treinamento](gaiapus-07-limpeza-e-dataset.md): granularidade, qualidade, tabelas e rótulos.
3. [Modelos e dois modos do produto](gaiapus-08-modelos-e-cenarios.md): escolha de plantio, MLP, simulação agrícola, produtividade e avaliação.
4. [Backend e plano de execução](gaiapus-09-operacao-e-integracao.md): pipeline, inferência e cronograma para a equipe.
5. [Atlas e catálogo agronômico](gaiapus-10-atlas-e-catalogo-agronomico.md) para comparar culturas por região e associar riscos climáticos à fase da lavoura.
6. [Margens e cenários de seca em MG e MT](gaiapus-11-margens-e-seca.md) para integrar os dados Conab, IBGE e NASA já coletados.
7. [Janelas ZARC e clima por fase](gaiapus-12-zarc-e-seca-por-fase.md) para simular soja e milho de segunda safra em Uberlândia e Sorriso.

## Primeiros 90 minutos

- [ ] Dados: abrir `data/raw/power/uberlandia-2016-2024.json` e `data/processed/uberlandia-2016-2024/daily_weather.parquet`. Reexecutar `python -m unittest discover -s tests`. Não baixar os nove anos novamente.
- [ ] Dados: rodar as chamadas curtas do catálogo e observar três dias de POWER e duas cenas HLS. Comparar as datas, unidades e coordenadas com `gaiapus-mvp/data/field.json`. O POWER original de nove anos usa outro ponto e outra célula.
- [ ] Dados: executar `python -m data.ingest.demo_rain_audit`. Esperado: 122/122 em MG, 61/61 em GO e 61/61 na BA. Código 1 sinaliza a diferença já conhecida de 0,07 mm em GO; investigar qualquer divergência nova separadamente.
- [ ] App: fixar exemplo JSON de histórico real e de cenário calculado. Usar o novo repositório da aplicação para telas e API, seguindo os [guias de frontend](2026-11-14-gaiapus-03-frontend.md) e [backend](2026-11-14-gaiapus-04-backend.md).
- [ ] Campo: combinar com produtor/cooperativa acesso a datas de plantio e colheita, cultura/cultivar, área colhida, peso e umidade da colheita, irrigação, análise de solo e custo da safra. Sem esses dados, o app pode demonstrar cenários, mas não validar rendimento por talhão.
