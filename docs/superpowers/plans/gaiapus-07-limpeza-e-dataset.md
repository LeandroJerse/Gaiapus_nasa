# Gaiapus 07 — limpeza, junção e dataset de treinamento

**Entrega:** processamento reproduzível do bruto à tabela de features. Layout recomendado no repositório novo: `data/raw/<fonte>/<versão>/<data>`, `data/normalized/`, `data/qa/` e `datasets/<versão>`. Scripts: `normalize_weather`, `summarize_hls`, `normalize_harvest`, `build_features` e `validate_release`. Reusar os extratores existentes em [data/ingest](../../../data/README.md).

| Tabela | Chave | Campos que não podem faltar |
|---|---|---|
| `fields` | `field_id, geometry_version` | WGS84, código IBGE, área, fonte/consentimento, células associadas |
| `weather_daily` | `grid_cell_id, product, valid_date, issue_at` | unidade, tipo observado/previsto, ciclo, resolução, `available_at`, flags |
| `hls_observation` | `field_id, scene_id` | aquisição/disponibilidade, NDVI mediano, p25/p75 quando computados, cobertura, QA |
| `management` | `field_id, season_id, event_id` | cultura, data de plantio, cultivar, irrigação em mm, manejo |
| `harvest` | `field_id, season_id` | massa, umidade, área efetivamente colhida, data, produtividade kg/ha, origem |
| `soil_sensor` | `device_id, boot_id, sequence` | horário, profundidade, umidade calibrada, versão, qualidade |
| `features` | `field_id, season_id, cutoff_at, horizon` | features disponíveis no corte, ID de versão e alvo com data distinta |

1. **Bruto → normalizado.** Conferir código HTTP, parse, schema, geometria, timezone e unidade. Guardar `event_at`, `available_at` e `ingested_at` separadamente. Lidar com dias UTC, ano bissexto e conversão para dia agrícola local por regra escrita; janela que cruza meia-noite não pode receber chuva em dobro.
2. **Óptico.** Recortar no CRS do raster, validar alinhamento e nodata, usar B04/B8A S30 ou B04/B05 L30 e excluir nuvens, adjacência, sombras, neve e água via Fmask. Aplicar escala/offset conforme metadados. Calcular NDVI pixel a pixel e tirar a mediana; marcar cena com menos de 65% de pixels válidos como rejeitada para o demo. Deduplicar o mesmo item; para uma linha por dia, escolher a observação disponível de maior cobertura pela regra do dataset.
3. **Tempo e clima.** Converter T em °C, radiação para MJ/m²/dia e chuva para mm/dia conforme unidade e intervalo de origem; não somar acumulados de um mesmo ciclo sem separar períodos. Normalizar sentinelas para nulos, nunca zeros. Comparar POWER/IMERG por data UTC e célula; preservar divergência, não interpolar chuva com média mensal. Cruzar satélite com estação/pluviômetro em validação separada.
4. **Campo.** Padronizar peso/área e, quando houver umidade medida e padrão documentado, ajustar a massa à umidade de referência antes de dividir pela área colhida. Ausência de área, cultivar ou data vira flag ou exclusão conforme a tarefa. Sonda sem calibração tem umidade absoluta nula; guardar profundidade e identificação da calibração.
5. **Join e features.** Vincular por talhão e versão geográfica, chave de célula e `available_at <= cutoff_at`. Para MLP construir janelas causais de 7, 14 e 30 dias, flags de lacuna e dias desde último NDVI. Incluir plantio, estágio, solo e irrigação conhecidos no corte. Fontes tardias só entram em backtest retrospectivo rotulado como tal.
6. **Release.** Produzir Parquet, dicionário, manifestos e relatório com cobertura, duplicatas, intervalos, fontes e recusas por UF, talhão e safra. Congelar IDs antes da modelagem. Tipos, outliers físicos, duplicação, falta de chuva, NDVI nulo e atraso de publicação precisam de teste.

**Teste que evita falsa confiança:** 3.288 dias meteorológicos não são 3.288 exemplos independentes de produtividade. A unidade do rótulo é **talhão × safra**; vários cortes da mesma safra ficam no mesmo grupo. Rendimento da PAM só autoriza modelo de escala municipal, se houver observações independentes suficientes nessa escala.
