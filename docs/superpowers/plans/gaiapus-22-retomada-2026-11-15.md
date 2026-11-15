# Gaiapus — retomada em 15/11/2026

## Instrução salva

> agora continue de forma independente: automatize a coleta autenticada do IMERG Early/Late, baixe o histórico de 2016 a 2021 para os oito municípios de MG e MT e compare com o Final, mantendo o controle de disponibilidade e atualizando os .md

Claude e Fable podem ser usados se necessário. A coleta e o treino serão retomados em 15/11/2026.

## Ponto de partida

- O balanço hídrico e o pipeline de pesquisa estão implementados. O v3 já treinou 28 modelos experimentais de soja e milho, com cortes de 60 e 90 dias. O teste de rendimento 2022–2024 permanece reservado.
- O piloto IMERG validou Early, Late e Final em 01/01/2021 para os oito municípios: 24 registros, 16 comparações. Há 6.576 arquivos catalogados para 2016–2021, mas só os três do piloto foram baixados.
- A autenticação do navegador funcionou. O coletor Python ainda precisa de autenticação programática. Na auditoria operacional, nenhum dos 672 candidatos de treino foi elegível.

## Ordem de execução

1. Validar a autenticação programática e um recorte pequeno. Automatizar coleta com cache, retomada e verificação de cobertura, unidade, célula e hash.
2. Coletar Early/Late de 2016–2021 nos oito municípios e parear com Final por célula e dia UTC. Registrar lacunas, diferenças e métricas por mês e município.
3. Registrar data observada, versão e primeira obtenção comprovada. Arquivos reprocessados são úteis para pesquisa retrospectiva; latência nominal e data de produção não comprovam disponibilidade histórica.
4. Consolidar chuva, meteorologia para ET0 e rótulos. Treinar e comparar baselines retrospectivos com cortes explícitos, mantendo o teste reservado. Treinar para operação somente quando houver cobertura e evidência de que atributos e rótulos estavam disponíveis nos respectivos cortes.
5. Atualizar os relatórios e os arquivos Markdown com cobertura real, resultados e bloqueios.

Referências locais: [treinamento v3](gaiapus-20-treinamento-com-deficit-hidrico.md) e [coleta e disponibilidade IMERG](gaiapus-21-imerg-operacional-e-disponibilidade.md).

## Execução iniciada em 14/11/2026

**Atualização de 14/11/2026.** A autenticação programática foi validada com Early, Late e Final de 02/01/2021. O lote completo foi iniciado em seguida. O processo foi configurado para refazer o relatório e iniciar o treino retrospectivo após a conclusão bem-sucedida da coleta. A primeira comparação atualizada inclui 01 e 02/01/2021, com 32 pares completos e MAE contra o Final de 1,50 mm/dia para Early e 0,84 mm/dia para Late. A cobertura e a disponibilidade registradas no relatório correspondem às aquisições verificadas até cada execução. Os comandos abaixo permitem retomar o processamento em caso de interrupção.

O lote reaproveita arquivos verificados, repete requisições em falhas transitórias e para diante de erros de autorização. A comparação de produtividade exige a cobertura pareada das 17.536 combinações município-dia. O treino aguarda o término da coleta.

Após configurar `EARTHDATA_TOKEN` no ambiente do processo ou configurar Earthdata no netrc local, executar:

```powershell
python -m data.ingest.imerg_operational --reuse-catalog --batch --start 2021-01-02 --end 2021-01-02
python -m data.ingest.imerg_operational --reuse-catalog --batch
python -m data.ingest.report_imerg_operational
python -m data.ingest.train_imerg_comparison
```

O coletor atual baixa os NetCDF completos do GES DISC e extrai oito células. O lote pode ocupar centenas de GB. O primeiro comando valida autenticação e esquema antes do lote longo. O relatório compara chuva na mesma célula e dia UTC, agrega métricas mensais e registra a primeira obtenção local com hash. Data de produção, atualização do catálogo e latência nominal ficam separadas da evidência de disponibilidade.

O treino comparativo usa Ridge para soja e milho de segunda safra, com os mesmos exemplos por produto, janelas de 60/90 dias e variantes com e sem balanço hídrico. O recorte retrospectivo ajusta com safras 2017–2018, valida em 2020–2021 e mantém 2022–2024 reservado. Esses cortes permitem usar safras completas dentro da janela pedida, com 2019 de intervalo. A comparação testa o efeito de trocar a entrada de chuva enquanto conserva ET0 e demais variáveis meteorológicas. O treino operacional ainda depende de versões e datas de disponibilidade comprovadas para todos os insumos e rótulos.

## Dados usados e relação com produtividade

- IMERG diário V07B Early (`GPM_3IMERGDE`), Late (`GPM_3IMERGDL`) e Final (`GPM_3IMERGDF`) em grade de 0,1°. Extraímos chuva em mm/dia e contagem de intervalos válidos. É um produto combinado da missão GPM. Entre as fontes estão GMI no GPM Core Observatory, AMSR2 no GCOM-W1, SSMIS em satélites DMSP, além de instrumentos de outras plataformas e infravermelho geoestacionário. O Final incorpora ajuste com pluviômetros do GPCC. O agregado diário usado aqui não identifica qual sensor contribuiu para cada célula e horário. [Documentação NASA](https://gpm.nasa.gov/resources/documents/imerg-v07-technical-documentation)
- NASA POWER fornece temperatura mínima/máxima, radiação, umidade relativa, vento, ponto de orvalho e pressão para a meteorologia e a ET0. A chuva IMERG e a ET0 alimentam o balanço hídrico por fase. [Catálogo local](gaiapus-14-catalogo-e-qualidade.md)
- IBGE PAM/SIDRA fornece rendimento anual municipal em kg/ha para soja e milho de segunda safra. O objetivo é testar desempenho em produtividade municipal. A comparação com Final mede diferenças entre versões de chuva. O ganho preditivo precisa aparecer na validação contra os rendimentos do IBGE.
