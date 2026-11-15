# Gaiapus — catálogo de fontes, uso e limpeza

## 1. NASA POWER: série meteorológica

Documentação oficial: [Daily API](https://power.larc.nasa.gov/docs/services/api/temporal/daily/) e [origem e resolução dos produtos](https://power.larc.nasa.gov/docs/tutorials/service-data-request/api/).

Endpoint efetivamente consultado: `https://power.larc.nasa.gov/api/temporal/daily/point`. Parâmetros: `community=AG`, `time-standard=UTC`, `start=20010101`, `end=20241231`, `format=JSON`, coordenadas de `locations.csv` e oito nomes abaixo. URLs exatas por ponto estão nos arquivos `data/raw/training_v1/power_<codigo>.json.manifest.json`.

| Parâmetro | Campo normalizado | Unidade | Uso |
|---|---|---|---|
| IMERG_PRECTOT | rain_mm | mm/dia | Distribuição de chuva, veranicos, entrada do balanço hídrico |
| T2M_MIN | t_min_c | °C | Temperatura mínima e estresse térmico |
| T2M_MAX | t_max_c | °C | Temperatura máxima, calor extremo e fenologia |
| ALLSKY_SFC_SW_DWN | radiation_mj_m2 | MJ/m²/dia | Energia para crescimento e evapotranspiração |
| RH2M | rh2m_pct | % | Contexto atmosférico; não é umidade do solo |
| WS2M | ws2m_m_s | m/s | Demanda evaporativa; vento a 2 m |
| T2MDEW | dewpoint_c | °C | Pressão real de vapor para VPD/ET0 |
| PS | pressure_kpa | kPa | Constante psicrométrica para ET0 |

A API agrega produtos de diferentes resoluções. A documentação descreve meteorologia MERRA-2 em aproximadamente 0,5° × 0,625° e radiação em aproximadamente 1°; IMERG tem grade de 0,1°. Não atribuir a resolução de 30 m do HLS ao clima. Guardar a coleção e a resolução de cada variável no catálogo da aplicação; verificar alterações de fontes no metadata de cada coleta.

Limpeza executada: UTC obrigatório, `fill_value` convertido para nulo, datas dentro do intervalo, unidade conhecida, chuva/radiação não negativas, mínima ≤ máxima, umidade 0–100%, vento não negativo e números finitos. Radiação em kWh/m²/dia é multiplicada por 3,6; em MJ/m²/dia é mantida. As oito séries retornaram zero ausências nos oito campos. Isso demonstra completude do produto, não ausência de erro meteorológico.

**Precisão espacial:** os pontos foram produzidos com `representative_point()` do Shapely dentro de malhas simplificadas oficiais do IBGE. Não são centroides agrícolas, sedes, propriedades ou a mesma coordenada das demonstrações anteriores. Não transportar a validação IMERG de Uberlândia/Sorriso antigos automaticamente para estes novos pontos.

## 2. IBGE: alvos de produtividade e geografia

- [PAM 5457](https://sidra.ibge.gov.br/tabela/5457): soja, categoria `c782/40124`; variáveis `8331,216,214,112`.
- [PAM 839](https://sidra.ibge.gov.br/tabela/839): milho de segunda safra, categoria `c81/114254`; variáveis `109,216,214,112`.
- Base da API: `https://apisidra.ibge.gov.br/values/t/{tabela}/n6/{codigos}/v/{variaveis}/p/{anos}/{classificacao}/{categoria}`.
- Localidades: `https://servicodados.ibge.gov.br/api/v1/localidades/municipios/{codigo}`.
- [Malhas v3](https://servicodados.ibge.gov.br/api/docs/malhas?versao=3): `https://servicodados.ibge.gov.br/api/v3/malhas/municipios/{codigo}?formato=application/vnd.geo%2Bjson&qualidade=intermediaria`.

Unidades verificadas na resposta: área = hectares (`MC=1006`), produção = toneladas (`MC=1017`), rendimento = kg/ha (`MC=33`). Conferência independente: `produção_t * 1000 / área_colhida_ha`, tolerância máxima de 2 kg/ha ou 0,5%, para acomodar arredondamento. As dimensões, categorias e chaves únicas são verificadas.

O pedido cobriu 2001–2024; a resposta da tabela 839 contém 2003–2024 para este recorte. Não criar valores para 2001/2002. Marcadores `-`, `...`, `..`, `X`, `x` e vazio são preservados no bruto e normalizados para nulo; têm semânticas distintas de inexistência, não divulgação ou indisponibilidade. Nenhum vira zero no treinamento de rendimento. Para análise de abandono de área ou quebra total, construir outro alvo específico e interpretar os marcadores segundo a documentação da tabela.

Área e produção do próprio ano ficam na tabela de auditoria de alvos. Não entram nos preditores: sua razão praticamente determina o rendimento. Valores de anos anteriores devem respeitar publicação e revisão. Nesta versão usa-se média de rendimentos de Y−2 a Y−4 como referência conservadora, sem alegar reconstrução exata da disponibilidade histórica.

## 3. INMET: referência terrestre

Origem oficial: [histórico anual de estações automáticas](https://portal.inmet.gov.br/dadoshistoricos). Foi baixado `https://portal.inmet.gov.br/uploads/dadoshistoricos/2024.zip`, preservado em `data/raw/training_v1/inmet/`. O pacote nacional é o formato de distribuição; foram extraídos somente oito CSVs de estações próximas ao recorte de interesse. O catálogo contém as estações MG/MT presentes no ZIP.

| Município | Estação escolhida | Distância ao ponto climático |
|---|---|---:|
| Uberlândia | A507 | 11,84 km |
| Uberaba | A568 | 13,71 km |
| Unaí | A542 | 21,46 km |
| Paracatu | A571 | 19,14 km |
| Sorriso | A904 | 16,84 km |
| Sinop | A917 | 31,96 km |
| Lucas do Rio Verde | A903 | 69,96 km |
| Campo Novo do Parecis | A905 | 15,43 km |

Seleção geográfica preliminar, sem declarar representatividade do talhão. O limite de 75 km é uma triagem de engenharia, não um critério universal de equivalência de chuva. A estação mais próxima pode ter pouca cobertura; avaliar estações alternativas antes de ampliar os anos. Dias completos por estação: A507=365, A568=195, A542=179, A571=312, A904=326, A917=300, A903=365, A905=30. **Campo Novo do Parecis tem apenas 30 dias completos na estação escolhida**: não usar isso para declarar validação anual da chuva.

Tratamento executado: leitura Latin-1, separador `;`, decimal `,`, valores negativos de precipitação e vazios → nulo, chave dia/hora sem duplicação; soma diária somente com 24 registros válidos. **856 dos 2.928 dias não têm chuva diária publicável por essa regra.** Não completar com zero nem com o satélite que será avaliado.

Pendente para validação meteorológica: confirmar a convenção de intervalo da precipitação horária (hora final/inicial), alinhar a janela UTC à do satélite, consultar IMERG exatamente na estação e computar métricas apenas nos pares completos. Não chamar a simples soma de horas de validação do satélite. Avaliar viés, MAE, correlação, dias chuvosos, maiores sequências secas, acumulados mensais e cobertura. Preservar também valores extremos reais, revisando flags de qualidade em vez de winsorizar chuva indiscriminadamente.

## 4. SoilGrids: contexto de solo com incerteza

Documentação: [ISRIC SoilGrids](https://docs.isric.org/globaldata/soilgrids/SoilGrids_faqs.html), [API REST](https://rest.isric.org/soilgrids/v2.0/docs).

Endpoint consultado: `https://rest.isric.org/soilgrids/v2.0/properties/query`. Consulta por latitude/longitude com repetição de `property=clay,sand,soc,bdod,phh2o`, `depth=0-5cm,5-15cm,15-30cm,30-60cm` e `value=mean,Q0.05,Q0.95` como parâmetros individuais. Os parâmetros completos estão no manifesto de cada ponto.

Mapas estimados em 250 m. Guardar os quantis junto da média. Normalização: dividir o valor nativo por `unit_measure.d_factor` e usar `target_units` retornado; não aplicar uma escala fixa igual a todas as propriedades. `soil_context.csv` mantém propriedade, profundidade, unidade, divisor, média e quantis. Das 160 combinações consultadas, 140 têm valores completos e 20 retornaram nulo na origem, todas no ponto de Campo Novo do Parecis. Não deslocar o ponto silenciosamente nem preencher com solo de outro município; o relatório identifica `source_nodata` e esse contexto continua indisponível até nova fonte/recorte validado.

Textura, carbono orgânico e densidade ajudam a contextualizar retenção de água e restrições de solo. pH é contexto químico. Não são teor atual de água nem análise laboratorial. Para converter em capacidade de água disponível será preciso curva de retenção medida ou função de pedotransferência validada, com incerteza. A camada até 60 cm não representa automaticamente toda a profundidade radicular do milho. Os pontos de solo não entram no baseline municipal desta rodada; primeiro agregar espacialmente sobre a cultura.

## 5. ZARC, HLS, IMERG e economia já existentes

- **ZARC:** reutilizado o bruto nacional da safra 2026/27; 240 perfis recortados para a coorte. Fonte, dicionário e portarias estão no [guia 12](gaiapus-12-zarc-e-seca-por-fase.md). Usar para regras atuais e cenários. Não tratar janela recomendada de 2026/27 como plantio observado em 2001.
- **HLS:** imagens e NDVI demonstrativos já existem em `gaiapus-mvp/data/`, `data/raw/margins/sorriso_hls/` e scripts `field_imagery.py`. Consultas: [STAC Planetary Computer](https://planetarycomputer.microsoft.com/api/stac/v1), coleções `hls2-l30` e `hls2-s30`. HLS é reflectância harmonizada Landsat/Sentinel a 30 m. [Guia oficial HLS v2](https://lpdaac.usgs.gov/documents/1698/HLS_User_Guide_V2.pdf).
- **NDVI:** `(NIR-red)/(NIR+red)`; L30 B05/B04, S30 B8A/B04. Aplicar nodata, escala e offset do produto, Fmask e cobertura válida antes de agregar. Reusar o código existente após conferir versão e bits de qualidade. Cenas rejeitadas permanecem no inventário com motivo. Não interpolar nuvens como se fossem observação. O limiar demonstrativo de 65% é configuração do projeto, não garantia agronômica.
- **Expansão HLS para treino:** somente depois de obter polígonos consentidos ou máscara anual de cultura e alinhar ao alvo. Baixar janelas de COG das bandas necessárias, nunca tiles RGB de estados inteiros por padrão. Não espalhar o rótulo PAM em cada pixel como se fosse rendimento observado.
- **IMERG Final V07B:** os comparativos anteriores verificam consistência entre distribuições do mesmo produto. São úteis para proveniência e conversões, mas não uma validação independente de precisão da chuva no solo. O novo dataset marca explicitamente os pontos como ainda não validados por referência independente.
- **Conab:** custos e preços de referência em `data/raw/margins/`; conferir [guia 11](gaiapus-11-margens-e-seca.md). O painel de Unaí é proxy para Uberlândia. Guardar local, sistema, safra, referência monetária e preço/data separadamente. Valor implícito PAM não é cotação spot. Não usar esses poucos preços para treinar previsão de mercado.

## 6. Contrato de limpeza e junção

Um rendimento numérico zero não é sinônimo de ausente: o normalizador aceita zero quando as áreas são positivas e a identidade produção/área é consistente. Área colhida zero torna essa razão indefinida; registrar perda/abandono em alvo próprio. Os marcadores de não informação da PAM não são convertidos para esses zeros. Os nove alvos rejeitados nesta coleta têm campos ausentes.

1. Bronze: resposta original, URL, horário de coleta, hash, licença/atribuição, versão e consulta. Nenhuma correção destrutiva.
2. Silver: unidade explícita, chave única, UTC, município IBGE como texto, coordenadas e suporte espacial, valor e qualidade separados.
3. Gold: `municipality_code + crop + harvest_year + feature_cutoff + dataset_version`; somente entradas anteriores ao corte e efetivamente disponíveis à operação pretendida.
4. Revisões não sobrescrevem snapshots de experimentos; gerar nova versão e novo hash.
5. Imputação: apenas nas entradas, ajustada no conjunto de treino; alvo ausente nunca imputado. Chuva faltante não pode virar dia seco.
6. Janelas fenológicas exigem data real de plantio e cultivar. Janelas de calendário recebem rótulo de proxy. Nunca misturar as duas em um mesmo campo sem sinalização.
7. Previsão meteorológica deve incluir `issue_time`, `valid_time` e membro do ensemble. Observação/reanálise inclui disponibilidade e revisão. Sem isso, o replay operacional vaza futuro.
8. INMET e sondas não são automaticamente verdade perfeita: controle de calibração, falhas, representatividade e alinhamento temporal continuam obrigatórios.

## 7. Orçamento de armazenamento

Nesta rodada, o volume é pequeno frente aos 300 GB porque alvos tabulares e séries pontuais contêm a informação necessária ao primeiro experimento. O ZIP INMET é a maior entrada nova. Não houve download de mosaicos estaduais HLS.

Para expansão, alocar por utilidade: até 30 GB para recortes HLS com rótulos e máscaras, 10 GB para clima/estações selecionadas e restante como reserva, sujeito a inventário de amostras independentes. Esses valores são limites propostos, não downloads realizados. Cada lote deve registrar uso por feature/modelo e ganho em validação; interromper coleta redundante. Não há justificativa atual para consumir 300 GB.
