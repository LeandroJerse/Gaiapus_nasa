# Gaiapus — validação da chuva e base espacial v2

Preparação de 14/11/2026. Esta versão complementa os guias 13–16. Dados/modelos v1 foram preservados; os novos arquivos ficam em `data/raw/training_v2`, `data/processed/training_v2` e `data/models/research_v2`.

## O que mudou e o que permanece limitado

1. A precipitação INMET foi alinhada ao intervalo correto e comparada ao IMERG nas coordenadas reais de 12 estações, em 2023–2024.
2. Campo Novo ganhou referências regionais alternativas e investigação de solo em pontos declarados. Medições locais ausentes continuam ausentes; referência regional não vira observação da fazenda.
3. O clima passou a ser ponderado pela área agrícola mapeada, com pesos anuais e grades separadas por produto. A máscara acessível é **agricultura genérica ESA CCI**, não soja/milho identificados.
4. O treinamento compara ponto v1, ponderação v2 e tendência/histórico, com as mesmas amostras e divisões. O teste de rendimento 2022–2024 não é avaliado.

Essa entrega mede limitações em vez de declarar validação apenas porque a consulta retornou valores. **Validação calculada não significa aprovação agronômica ou operacional.**

## Chuva: convenção temporal e teste realizado

O [glossário oficial INMET](https://portal.inmet.gov.br/glossario/tempo) informa que a precipitação automática corresponde à hora anterior à mensagem. Portanto, a mensagem das 00h do dia seguinte fecha o dia UTC atual. A implementação subtrai uma hora do timestamp antes de agrupar. O total diário só existe com 24 registros válidos e únicos.

Os arquivos anuais de 2023 e 2025 foram coletados; 2024 foi reutilizado. Do arquivo de 2025, apenas a mensagem de 01/janeiro às 00h entra na análise, para fechar 31/dezembro/2024. Coordenadas nos cabeçalhos foram verificadas para impedir que uma mudança de posição da estação passe silenciosamente pela comparação.

O denominador é construído a partir do calendário completo de 365/366 dias, não do número de datas devolvidas pela API. Chuva nula e ausência são distintas. Dias ausentes de qualquer fonte continuam nos pares, mas não entram nas métricas de erro. Acumulados mensais só representam o mês inteiro quando `full_month=true`; do contrário são somas de dias pareados.

### Diagnóstico de 2024

| Estação | Dias pareados / 366 | MAE diário mm | Viés IMERG − INMET mm/dia | Correlação |
|---|---:|---:|---:|---:|
| A507 Uberlândia | 365 | 3,43 | +0,44 | 0,66 |
| A568 Uberaba | 191 | 1,53 | −0,17 | 0,62 |
| A542 Unaí | 173 | 1,21 | −0,10 | 0,76 |
| A571 Paracatu | 312 | 2,91 | +0,62 | 0,72 |
| A904 Sorriso | 326 | 4,97 | −2,26 | 0,63 |
| A917 Sinop | 298 | 3,83 | +1,35 | 0,62 |
| A903 São José do Rio Claro | 364 | 3,63 | −0,99 | 0,83 |
| A905 Campo Novo | 29 | 8,96 | +6,74 | 0,32 |
| A911 Sapezal | 366 | 3,16 | −0,68 | 0,63 |

As métricas com pouca cobertura descrevem apenas os dias observados; não permitem concluir desempenho anual. Um MAE baixo em uma estação com muitos meses ausentes não prova superioridade. Relatórios completos incluem 12 estações, 2023, 2024, meses, acertos, falhas e falsos alarmes de dia chuvoso (≥1 mm).

Arquivos: `rain_pairs.csv`, `station_daily_utc.csv`, `rain_station_validation.csv`, `rain_monthly_validation.csv` e `rain_validation_manifest.json`.

**Não foi aplicada correção de viés.** Ajustar uma correção usando 2023/2024 e aplicá-la retrospectivamente ao treino tornaria a avaliação contaminada. Se for necessário calibrar chuva, coletar estações no período de ajuste, ajustar a correção apenas ali e avaliar fora desse período. IMERG Final também incorpora informação pluviométrica em seu ajuste; não se deve afirmar independência total de toda rede de pluviômetros sem verificar a procedência das estações usadas pelo produto.

## Campo Novo: tratamento das lacunas

### Chuva local

A905 apresentou 105 dias completos em 2023 e 29 em 2024. Não há medição local válida nos dias restantes nesses arquivos. O script avaliou A911, A928, A902, A927 e A903 como alternativas, preservando distância e cobertura em `campo_station_alternatives.csv`.

Sapezal/A911 tem 366 dias completos em 2024, a aproximadamente 102,6 km do ponto inicial de Campo Novo. Tangará/A902 tem os dois anos completos e fica a aproximadamente 121 km. Esses registros permitem diagnóstico regional; não preenchem a chuva medida em Campo Novo. A API futura deve retornar tanto `regional_reference_available` quanto `local_ground_validation_incomplete`.

O modelo continua recebendo chuva estimada por satélite nas áreas agrícolas. O relatório de qualidade da região acompanha essa série. Para recomendação local sensível à água, é necessária estação/pluviômetro próximo e calibrado ou nova fonte de observações locais verificadas.

### Solo

O ponto representativo inicial é classificado como urbano (classe 190) no mapa ESA de 2020. Isso mostra por que um ponto meramente interno ao município não deve ser tratado como talhão; não demonstra, por si só, a causa dos nulos SoilGrids.

Foram consultados vizinhos declarados. Quatro pontos a cerca de 250–280 m também ficaram sem valores; um ponto 1,11 km ao norte retornou as 20 combinações de propriedade/profundidade, mas ainda pertence à classe urbana ESA. Esse resultado é preservado como diagnóstico e **não é o suporte agrícola preferido**.

A etapa `campo_crop_soil_v2` procura pixels ESA agrícolas dentro do município, ordenados por proximidade, e consulta o SoilGrids em candidatos separados por pelo menos 500 m. **Foi obtido contexto completo de 20 combinações em um pixel agrícola a 585 m do ponto inicial.** O resultado consta em `campo_agricultural_soil_resolution.json` e `campo_soil_agricultural.csv`. A coordenada, distância, classe de cobertura e unidade acompanham cada valor. O ponto original permanece nulo. Nenhum desses valores é análise de solo de uma fazenda nem entra automaticamente no modelo municipal de rendimento.

## Ponderação por área agrícola

### Fonte e alternativa adotada

Foram testadas URLs públicas oficiais do [MapBiomas Cobertura 30 m](https://brasil.mapbiomas.org/iniciativas-e-produtos/cobertura-e-uso-da-terra/cobertura-30m/cobertura/) e do [uso agrícola de segunda safra](https://brasil.mapbiomas.org/iniciativas-e-produtos/cobertura-e-uso-da-terra/agricultura/uso-agricola-2a-safra/). As consultas retornaram HTTP 403. A alternativa documentada pelo MapBiomas é o acesso via Earth Engine; não foi criado projeto, autenticação ou exportação em conta do usuário.

Foi usada a coleção pública `esa-cci-lc` no [Planetary Computer](https://planetarycomputer.microsoft.com/dataset/esa-cci-lc). O STAC preservado registra descrições, versões, classes, resolução e URLs originais. Foram recortados 21 anos (2000–2020) × oito municípios = **168 GeoTIFFs**, em resolução nativa de aproximadamente 300 m, sem reamostragem por overview.

Classes incluídas: 10, 11 e 20 (agricultura de sequeiro/herbácea e irrigada/inundada, conforme a legenda do asset). Classes mistas 30/40 e agricultura arbórea 12 foram excluídas para não inventar uma fração agrícola. Não há identificação específica de soja, milho ou segunda safra nessa máscara.

### Cálculo

```text
área_agricola(célula, município, ano_máscara)
  = soma das áreas geodésicas dos pixels agrícolas cujo centro pertence ao município e à célula

peso_célula = área_agricola_célula / soma_das_áreas_agricolas
clima_municipal(dia) = soma(peso_célula × valor_célula_dia)
```

A área usa elipsoide WGS84, com variação da área do pixel por latitude. Limites municipais são as mesmas malhas IBGE simplificadas da v1. A decisão por centro de pixel aproxima interseções de borda; não é integração vetorial exata de cada pixel cortado.

Grades de amostragem: IMERG 0,1°; meteorologia nominal MERRA-2 0,5° × 0,625°; radiação nominal CERES 1°. Cada variável é ponderada em seu suporte, sem atribuir 300 m de resolução à meteorologia. São **549 células de chuva, 43 de meteorologia e 18 de radiação**, consultadas no NASA POWER. Para meteorologia/radiação, as grades nominais e a extração pontual da API permanecem uma hipótese técnica registrada; não são novos produtos NASA reprocessados a partir dos arquivos-fonte.

Esta rodada pondera seis variáveis usadas no treinamento: chuva, Tmin, Tmax, umidade relativa, vento e radiação. Ponto de orvalho e pressão continuam disponíveis na base pontual v1, mas não foram ponderados nem usados aqui para calcular ET0. Não misturar esses suportes e apresentar o resultado como balanço hídrico validado.

Todos os 504 grupos município × ano × grade têm pesos positivos e soma 1; a área agrícola total é consistente entre grades. A ausência de um valor em uma célula de peso positivo propaga para a média; não se redistribuem silenciosamente os pesos às células restantes.

`mask_year = min(harvest_year − 2, 2020)`. Isso evita utilizar a máscara da própria safra como observação já disponível no corte. Ainda assim, os mapas são versões históricas revisadas, não reproduções de edições publicadas em cada data. Para 2023/2024 a máscara 2020 fica defasada e deve ser mostrada como tal.

Arquivos: `crop_cell_weights.csv`, `crop_mask_audit.csv`, `mask_manifest.json`, `spatial_audit.json`, `weighted_season_daily.csv` e `research_samples.csv`.

### Consequências para o treinamento

Os alvos, janelas temporais e grupos de validação são mantidos. A única alteração nos experimentos principais é o suporte espacial dos indicadores meteorológicos. Dias secos e calor extremo continuam calculados sobre a série média municipal; `mean_dry_area_fraction` é exportada como diagnóstico adicional, mas não adicionada às 11 features da comparação principal. Uma média regional pode esconder a seca em uma fração do município.

Não se deve anunciar essa versão como “clima ponderado pela soja/milho exatos”. A ponderação é por agricultura genérica. A próxima substituição é uma máscara específica de cultura e safra, com acesso, licença, versão e disponibilidade temporal verificados. Evitar usar a área agrícola total como alvo de área de soja; são quantidades diferentes.

## Reprodução

```powershell
uv run --python 3.13 python -m unittest discover -s tests
uv run --python 3.13 python -m data.ingest.rain_validation_v2
uv run --with rasterio==1.4.4 --with shapely==2.1.2 --with pyproj==3.7.2 python -m data.ingest.crop_weighted_v2 masks
uv run --python 3.13 python -m data.ingest.crop_weighted_v2 weather
uv run --python 3.13 --with numpy==2.3.3 python -m data.ingest.crop_weighted_v2 build
uv run --python 3.13 --with scikit-learn==1.7.2 --with numpy==2.3.3 --with pandas==2.3.3 python -m data.ingest.retrain_v2
```

Os comandos de coleta usam cache verificado. Com cache completo, não exigem novo download de brutos, exceto tokens efêmeros se algum recorte ESA precisar ser refeito. O pipeline não retreina durante requisições do aplicativo. O ambiente do desktop pode exigir execução fora do sandbox para acessar o cache uv; isso é uma condição do ambiente, não da fonte dos dados.

Solo agrícola e verificação espacial:

```powershell
uv run --with rasterio==1.4.4 --with shapely==2.1.2 --with pyproj==3.7.2 python -m data.ingest.campo_crop_soil_v2
uv run --with rasterio==1.4.4 --with shapely==2.1.2 --with pyproj==3.7.2 python -m data.ingest.geospatial_audit_v2
```

## Contrato para backend/frontend

- Acrescentar `spatial_support`, `mask_source`, `mask_year`, `species_specific_mask`, `weight_version` e `ground_validation_status` aos retornos climáticos.
- Mostrar a máscara como camada opcional, com legenda “agricultura mapeada ESA — não identifica a cultura”.
- Em Campo Novo, distinguir histórico de satélite, estação regional e ausência de observação local; não traçar estação Sapezal como se estivesse na fazenda.
- Mostrar no solo o deslocamento entre ponto solicitado e ponto de contexto. Não converter o proxy em “sensor do talhão”.
- Modelos de ablação têm duas features; modelos climáticos têm 11. O manifesto registra `features_by_artifact`, e o backend deve validar essa lista antes de inferir.
- Manter `production_approved=false` até validação agronômica e critérios do guia 15. Um modelo novo pode ser pior que o anterior; a comparação abaixo decide o que a evidência permite afirmar.

## Resultados finais de treinamento

Os resultados reproduzíveis são registrados em `model_comparison.csv`, `model_comparison_by_state.csv`, `validation_predictions.csv` e no manifesto de modelos. Leia a [comparação final de resultados](gaiapus-18-resultados-v2.md): a melhora é mista e não autoriza substituir automaticamente os modelos anteriores.
