# Pipeline meteorológico da Gaiapus

## IMERG Early/Late e disponibilidade

O [guia 21](../docs/superpowers/plans/gaiapus-21-imerg-operacional-e-disponibilidade.md) registra a coleta piloto de Early/Late/Final V07B em 01/01/2021 (24 células, 16 pares), o catálogo 2016–2021 e os controles de versão/disponibilidade. Dados em `raw/imerg_operational_v1/` e `processed/imerg_operational_v1/`. Refaça a comparação com `python -m data.ingest.report_imerg_operational`. São arquivos reprocessados em 2024 e obtidos em 2026; não foram tratados como disponíveis em 2021. Nenhum novo modelo operacional foi treinado: 672 candidatos recusados por falta de dados/versionamento temporal.

## Treinamento com atributos hídricos v3

O [guia de treinamento hídrico](../docs/superpowers/plans/gaiapus-20-treinamento-com-deficit-hidrico.md) documenta 28 modelos em `models/research_v3/` e comparações em `processed/training_v3/`. Execute `python -m data.ingest.train_hydro_v3`. Usa prefixos de 60/90 dias, treino até 2016, embargo 2017–2018 e validação 2019–2021; teste 2022–2024 continua reservado. Os indicadores melhoraram 7/16 pares e pioraram 9/16. O corte de linhas futuras é testado, mas produtos reprocessados sem versões históricas impedem alegar avaliação operacional; nenhum modelo foi promovido.

## ET0 e simulação hídrica

Implementado o [balanço hídrico e seca por fase](../docs/superpowers/plans/gaiapus-19-balanco-hidrico-et0.md) para soja/milho 2ª em oito municípios MG/MT. Execute `python -m data.ingest.water_balance_run`. Resultados em `processed/water_balance_v1/`: 70.128 dias de ET0, 4.464 cenários e 17.856 resumos de fase. Usa clima **pontual v1 completo**, sem misturá-lo com as variáveis ponderadas v2. Validado numericamente; CAD/fenologia ainda não calibradas em campo, sem previsão de rendimento. O JSON de exemplo serve ao futuro backend, não representa endpoint implantado.

## Base espacial v2

Os novos arquivos estão em `raw/training_v2/`, `processed/training_v2/` e `models/research_v2/`. Veja a [metodologia de validação e ponderação](../docs/superpowers/plans/gaiapus-17-validacao-espacial-v2.md) e os [resultados comparativos](../docs/superpowers/plans/gaiapus-18-resultados-v2.md). Brutos, CSVs e modelos v1 permanecem preservados; os textos v1 originais foram arquivados em `docs/archive/training_v1/` antes das atualizações dos guias.

## Nova base de treinamento MG/MT

A preparação de 13–14/11/2026 está em `raw/training_v1/`, `processed/training_v1/` e `models/research_v1/`. Comece pelo [guia de treinamento e limites](../docs/superpowers/plans/gaiapus-13-base-de-treinamento.md). Os dados e modelos desta pasta são experimentais, de escala municipal. O [catálogo](../docs/superpowers/plans/gaiapus-14-catalogo-e-qualidade.md) explica NASA/IBGE/INMET/SoilGrids, limpeza e lacunas. Os pontos desta coorte são diferentes dos recortes demonstrativos abaixo.

Ponto ilustrativo de Uberlândia (-18,92; -48,28). O histórico da [API NASA POWER Daily](https://power.larc.nasa.gov/docs/services/api/temporal/daily/) cobre 01/01/2016 a 31/12/2024. São 3.288 dias em UTC com chuva IMERG, temperatura mínima, máxima e radiação, sem lacunas nos quatro campos. O JSON bruto está em `raw/power/` e o histórico normalizado em `processed/uberlandia-2016-2024/`. O manifesto guarda consulta, checksum, parâmetros e unidades.

A conferência prevista no plano cobre janeiro de 2024. Foram usados os originais diários NASA GES DISC [GPM_3IMERGDF V07](https://doi.org/10.5067/GPM/IMERGDF/DAY/07), arquivos V07B espelhados no [NSF NCAR GDEX d734000](https://gdex.ucar.edu/datasets/d734000/dataaccess/). A célula que contém o ponto tem centro em -18,95; -48,25. A extração usa um ponto via OPeNDAP, com NetCDF original local como alternativa. O manifesto da referência preserva URL e hash por dia. A comparação usa o mesmo dia UTC e mm/dia.

| Verificação | Resultado |
|---|---:|
| Dias pareados | 31/31 |
| Dias com diferença acima de 0,02 mm | 0 |
| MAE diário | 0,00129 mm |
| Maior diferença diária absoluta | 0,005 mm |
| Acumulado do mês no POWER | 238,17 mm |
| Acumulado do mês na referência | 238,19 mm |

## Validação de 2016–2024

O [relatório mensal CSV](processed/uberlandia-2016-2024/rain_validation_monthly.csv) e o [relatório detalhado JSON](processed/uberlandia-2016-2024/rain_validation.json) cobrem os 108 meses. Há dois recortes de comparação:

- **Acumulados de mês inteiro.** Soma dos dias do POWER comparada aos 108 arquivos do [IMERG mensal Final V07B](https://doi.org/10.5067/GPM/IMERG/3B-MONTH/07) no [NSF NCAR GDEX d736000](https://gdex.ucar.edu/datasets/d736000/dataaccess/). A taxa mensal de precipitação foi convertida de mm/h para mm multiplicando pelo número de horas UTC no mês. A extração validou a célula do índice Kerchunk. Os arquivos e hashes dos recortes estão em `raw/imerg/monthly/reference.json`. Os 108 pares de acumulados estão completos. A diferença média absoluta foi 0,333 mm/mês e o viés POWER menos IMERG foi −0,196 mm/mês. A maior diferença foi em junho de 2017, com 0,44 mm no POWER e 7,20 mm no produto mensal.
- **Conferência diária de origem.** POWER comparado aos valores obtidos da coleção diária Final V07B. Os campos `power_mm` e `reference_mm` somam apenas os dias pareados. O relatório indica quantos dias faltaram em cada fonte, as datas e os desvios acima de 0,02 mm/dia. Lacuna na coleta do arquivo de referência indica ausência de verificação direta nesta rodada. Os produtos diário e mensal são publicados separadamente e os respectivos acumulados podem divergir. A série mensal não foi usada para preencher datas.

As comparações são entre distribuições do produto IMERG. A precisão da chuva no campo exige medições locais.

**Investigação de junho de 2017.** O HDF5 mensal original confirmou 7,20 mm na célula. Nos 30 arquivos diários Final V07B, a soma foi 0,445 mm, compatível com os 0,44 mm do POWER e com maior diferença de 0,005 mm/dia. Nesse mês, a discrepância está entre o produto mensal e o acumulado do produto diário. O teste não identificou a causa dessa divergência.

## Recortes demonstrativos de 2024

O [catálogo do MVP](../gaiapus-mvp/data/regions.json) associa cada polígono ilustrativo à sua malha do IBGE. As imagens são composições RGB de cenas NASA HLS v2 obtidas pelo [Microsoft Planetary Computer](https://planetarycomputer.microsoft.com/dataset/group/hls2). A mediana de NDVI usa os pixels do polígono que passam pela máscara Fmask e pela checagem de reflectância. Cenas abaixo de 65% de cobertura válida ficam sem NDVI. O app mostra a estimativa diária IMERG Final V07B em mm por dia UTC, para a célula do **centro** de cada polígono. As bordas de algumas áreas cruzam o limite da célula.

| Recorte | Longitude, latitude de consulta | Período UTC | Cenas HLS aceitas / consultadas | Dias de chuva |
|---|---|---|---:|---:|
| Uberlândia, MG | −48,409; −18,9035 | 01/04 a 31/07/2024 | 51 / 64 | 122 / 122 |
| Rio Verde, GO | −51,050; −17,900 | 01/06 a 31/07/2024 | 18 / 19 | 61 / 61 |
| Barreiras, BA | −45,350; −12,200 | 01/06 a 31/07/2024 | 16 / 19 | 61 / 61 |

A [conferência dos recortes](processed/demo-rain-audit.json) compara POWER e os arquivos diários IMERG Final V07B, com célula, data, unidade, versão e hash verificados. A referência de Uberlândia desta tabela é de outra célula em relação ao histórico 2016–2024 acima. O cruzamento entre duas distribuições do IMERG verifica consistência; não substitui medição em campo.

| Recorte | Dias pareados | Dias acima de 0,02 mm | Máximo absoluto |
|---|---:|---:|---:|
| Uberlândia | 122/122 | 0 | 0,005 mm |
| Rio Verde | 61/61 | 1 | 0,07 mm |
| Barreiras | 61/61 | 0 | 0 mm |

Em 04/06/2024, o POWER retornou 0,07 mm em Rio Verde e o V07B retornou 0 mm. O gráfico usa 0 mm da referência e o valor do POWER permanece arquivado. O app oculta o pacote quando a série de referência não tem todos os dias.

Para revalidar a chuva dos três recortes a partir dos extratos já coletados: `python -m data.ingest.demo_rain_audit --publish`. O código 1 indica que o cruzamento relatou lacuna ou divergência acima da tolerância, mesmo quando o gráfico pode usar a referência completa. Para gerar outro pacote HLS, coloque as malhas `BR.geojson`, `UF.geojson` e `city.geojson` da área no diretório de destino, guarde o JSON POWER em UTC desde sete dias antes da série e execute, por exemplo:

```powershell
$env:GDAL_DISABLE_READDIR_ON_OPEN = 'EMPTY_DIR'
uv run --with rasterio==1.4.4 --with requests --with pillow python -m data.ingest.field_imagery `
  --bbox -51.054 -17.904 -51.046 -17.896 --start 2024-06-01 --end 2024-07-31 `
  --out gaiapus-mvp/data/regions/rio-verde --uf-file UF.geojson `
  --weather data/raw/power/rio-verde-2024.json
```

## Reproduzir

Da raiz do workspace, para regenerar CSV, Parquet, proveniência e relatório a partir dos arquivos preservados:

```powershell
python -m unittest discover -s tests
uv run --with pyarrow==26.0.1 python -m data.ingest.run `
  --reference data/raw/imerg/reference/imerg_final_v07.csv `
  --reference-manifest data/raw/imerg/reference/imerg_final_v07.manifest.json
```

Para reconstruir a referência mensal usando o cache e os NetCDFs locais:

```powershell
uv run --with netCDF4 python -m data.ingest.imerg_ncar `
  --start 2024-01-01 --end 2024-01-31 --cache-only --netcdf-dir data/raw/imerg/nc4
```

Para coletar outra janela de referência, execute o mesmo módulo com `--start`, `--end` e `--out`; omita `--cache-only` para permitir acesso à rede. No módulo `data.ingest.run`, `--download` refaz a consulta POWER. Preserve o manifesto anterior ou use caminhos separados ao baixar uma nova versão. Uma comparação solicitada retorna código 2 em caso de lacuna ou discrepância.

Para reconstruir a referência mensal, execute `python -m data.ingest.imerg_monthly_reference`. O índice NCAR e os blocos consultados foram preservados em `raw/imerg/`. Para regenerar o relatório completo com os caches disponíveis:

```powershell
python -m data.ingest.imerg_ncar --start 2016-01-01 --end 2024-12-31 --cache-only --netcdf-dir data/raw/imerg/nc4
uv run --with pyarrow==26.0.1 python -m data.ingest.run --reference data/raw/imerg/reference/imerg_final_v07.csv --reference-manifest data/raw/imerg/reference/imerg_final_v07.manifest.json --monthly-reference data/raw/imerg/monthly/reference.json
```
