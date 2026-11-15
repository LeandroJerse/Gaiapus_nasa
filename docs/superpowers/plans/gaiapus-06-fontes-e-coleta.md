# Gaiapus 06 — catálogo de fontes e coleta verificável

**Estado:** “usado” significa dados recuperados pelo projeto; “consultado” significa documentação técnica verificada; “pendente” significa que a integração ainda precisa da chamada, controle local ou acesso. A página de [ofertas do Space Apps 2026](https://www.spaceappschallenge.org/2026/global-offers/) não foi confirmada pela consulta pública inicial. Não assumir créditos, vouchers ou entitlement; confirmar no portal da equipe. O acesso público ao catálogo Microsoft é independente dessa confirmação.

| Prioridade e fonte | Onde chamar / como obter | O que tirar e limite |
|---|---|---|
| P0 · **NASA POWER** · usado | [Daily API](https://power.larc.nasa.gov/docs/services/api/temporal/daily/), GET em `/api/temporal/daily/point` | `IMERG_PRECTOT,T2M_MIN,T2M_MAX,ALLSKY_SFC_SW_DWN` em UTC. Histórico, não previsão. Cada família de variável tem sua grade; guardar unidades retornadas. |
| P0 · **NASA HLS**, hospedado pela Microsoft · usado | [Planetary Computer STAC](https://planetarycomputer.microsoft.com/api/stac/v1/), `/search` + ativos COG assinados no worker. [Alternativas NASA](https://hls.gsfc.nasa.gov/data-access-and-tools/). | S30 Sentinel-2 e L30 Landsat 8/9, 30 m. RGB e NDVI com controle por Fmask. Conferir cobertura temporal de cada catálogo; não presumir HLS desde 2016 via Microsoft. Nuvens criam lacunas. |
| P0 · **NASA GPM IMERG** · Final usado | [Catálogo CMR](https://cmr.earthdata.nasa.gov/search/site/docs/search/api.html): `/search/collections.json?short_name=GPM_3IMERGDF`; [dados Final diário](https://doi.org/10.5067/GPM/IMERGDF/DAY/07) via GES DISC/Earthdata ou [espelho NCAR](https://gdex.ucar.edu/datasets/d734000/dataaccess/) usado no projeto. | Final V07B, célula de 0,1°, mm/dia UTC. [Early/Late](https://gpm.nasa.gov/data/imerg) para chuva recente observada por satélite, com atraso e revisões. Final para auditoria histórica. Nenhum é previsão nem pluviômetro de talhão. |
| P1 · **NOAA GFS / GEFS** · documentação consultada | [NOMADS GFS 0,25°](https://nomads.ncep.noaa.gov/gribfilter.php?ds=gfs_0p25), GRIB2 recortado. Primeiro GFS, depois verificar [produtos disponíveis no NOMADS](https://nomads.ncep.noaa.gov/gribfilter.php) para ensemble. | Chuva, temperatura e radiação futuras; arquivar ciclo/modelo, prazo e grade. Limitar o MVP a D+1 a D+7. Sem arquivo de previsões emitidas no passado, não alegar teste operacional retrospectivo. Não usar previsão meteorológica determinística para o restante da safra. |
| P1 · **INMET** · documentação consultada | [Estações históricas](https://portal.inmet.gov.br/dadoshistoricos/), arquivos; [BDMEP](https://bdmep.inmet.gov.br/), consulta. | Referência regional independente; conferir estação, distância, altitude, fuso e falhas. Preferir pluviômetro no talhão para verificação local. |
| P1 · **MAPA** · documentação consultada | [Portarias ZARC](https://www.gov.br/agricultura/pt-br/assuntos/riscos-seguro/programa-nacional-de-zoneamento-agricola-de-risco-climatico/portarias) e exportação [SISZARC](https://www.gov.br/agricultura/pt-br/assuntos/riscos-seguro/programa-nacional-de-zoneamento-agricola-de-risco-climatico/siszarc). | Calendário por safra, município, solo/AD, ciclo e regime aplicável. Importar o arquivo oficial; não depender de API pública não documentada. |
| P1 · **ISRIC / solo de campo** · documentação consultada, extração pendente | [SoilGrids WebDAV/VRT](https://docs.isric.org/globaldata/soilgrids/WebDav.html); laudo/descrição do perfil. | Textura e carbono estimados por profundidade, resolução regional e incerteza. Parametrização inicial de cenário, não diagnóstico. Laudo e profundidade de raízes calibram o talhão. |
| P1 · **IBGE e Conab** · consulta de referência | [PAM / SIDRA 5457](https://sidra.ibge.gov.br/tabela/5457), API de [Agregados](https://servicodados.ibge.gov.br/api/docs/agregados?versao=3). [Conab rentabilidade](https://www.gov.br/pt-br/servicos/obter-informacoes-sobre-a-rentabilidade-de-produtos-agricolas): extrair séries e planilhas oficiais; orçamento do produtor tem prioridade. | Área, quantidade e produtividade municipal dão referência regional; não usar o mesmo rótulo para milhares de pixels. Custos e preços precisam de praça, mês, unidade, safra, moeda e cenário de venda. |
| P1 · **Produtor / IoT** · coleta pendente | CSV consentido + API própria para sensores. | Safra por talhão, cultivar, datas, fenologia, irrigação, peso da colheita e área, custos, laudo e umidade/temperatura por profundidade. É a fonte dos rótulos locais. |

## Testar antes de automatizar

**Verificado nesta rodada:** POWER, STAC e CMR deram HTTP 200 para as consultas de amostra. POWER devolveu os quatro campos esperados; STAC devolveu duas cenas S30 no recorte de Uberlândia; CMR listou `GPM_3IMERGDF` versão 07. Isso confirma consulta de catálogo, e não acesso ao download autenticado pelo CMR. O Final já foi recuperado anteriormente pelo espelho NCAR.

Na raiz da pasta, em PowerShell, sem chaves para buscas públicas:

```powershell
$power = 'https://power.larc.nasa.gov/api/temporal/daily/point?parameters=IMERG_PRECTOT,T2M_MIN,T2M_MAX,ALLSKY_SFC_SW_DWN&community=AG&latitude=-18.92&longitude=-48.28&start=20240701&end=20240703&format=JSON&time-standard=UTC'
(Invoke-RestMethod $power).properties.parameter
$stac = 'https://planetarycomputer.microsoft.com/api/stac/v1/search?collections=hls2-s30,hls2-l30&bbox=-48.413,-18.907,-48.405,-18.900&datetime=2024-07-01T00:00:00Z/2024-07-31T23:59:59Z&limit=2'
(Invoke-RestMethod $stac).features | Select-Object id, collection
$cmr = 'https://cmr.earthdata.nasa.gov/search/collections.json?short_name=GPM_3IMERGDF&page_size=5'
(Invoke-RestMethod $cmr).feed.entry | Select-Object short_name,version_id
```

O CMR lista coleções e granules; a autorização de download depende do provedor. Para GFS, abra o filtro, selecione o ciclo disponível e um recorte amplo em torno de Uberlândia, filtre APCP e temperatura (após verificar nível e intervalo GRIB); grave a URL efetivamente gerada, ciclo e passo. Cheque a data válida e as janelas de acumulado antes de transformar em chuva diária. Guardar respostas brutas, checksum e hora de recuperação; nunca gravar tokens SAS ou Earthdata.
