# Gaiapus · Globo, navegação territorial e observações do talhão

**Objetivo.** Demonstrar exploração global, com análise agrícola restrita ao Brasil na competição. O primeiro pacote verificável contém um recorte rural ilustrativo em Uberlândia, MG, uma imagem orbital real e duas séries alinhadas no tempo. A amostra rural é um polígono técnico, sem vínculo cadastral ou proprietário identificado. O projeto não presume disponibilidade para todos os municípios brasileiros.

**Arquitetura.** React e MapLibre GL JS formam o observatório. Um pipeline Python lê imagens do catálogo público STAC, recorta bandas HLS e combina datas com chuva NASA POWER. Os arquivos derivados são servidos localmente junto da interface. As consultas e a assinatura dos GeoTIFFs ficam no processamento de dados. Integrar à API FastAPI planejada quando houver mais recortes e usuários.

## Experiência e regras de cobertura

| Nível | Fonte geográfica | Ação de câmera | Disponibilidade |
|---|---|---|---|
| Globo | MapLibre com projeção globe e mapa vetorial mundial | Rotação e seleção de país | Exploração mundial |
| Brasil | Malha IBGE do país | Enquadrar limites nacionais | País habilitado para competição |
| Estado | Malhas IBGE por código UF | Enquadrar polígono da UF | 27 UFs exploráveis. MG abre a próxima etapa do piloto |
| Município | Malhas IBGE dos 853 municípios de MG | Enquadrar município e destacar os recortes habilitados | Uberlândia, código 3170206, tem pacote de análise |
| Recorte | GeoJSON em WGS84 | Aproximar e sobrepor a imagem georreferenciada | Série estimada para o recorte, com origem e qualidade por cena |

A seleção administrativa vem do catálogo de limites. A capacidade de mostrar um município vem da existência de geometria. A capacidade de mostrar NDVI vem das cenas e dos pixels válidos dentro do polígono. A chuva tem escala distinta, cerca de 0,1° no IMERG. Dar zoom não aumenta a resolução dos dados. A interface oferece seletor e trilha de navegação além do mapa, respeita redução de movimento e mantém o gráfico legível se WebGL estiver indisponível.

**Catálogo de disponibilidade.** Na próxima etapa, persistir `country_iso2, admin1_id, admin2_id, aoi_id, data_status, dataset_version`. Separar `explorable`, `available`, `insufficient_observations` e `not_enabled`. Um país fora do Brasil pode ter imagem no catálogo global, mas o produto agronômico depende de regras, bases locais e validação.

## Obtenção e tratamento

1. **Limites.** Fixar as malhas do [IBGE v4](https://servicodados.ibge.gov.br/api/docs/malhas?versao=4) e o código do município. Para expansão, carregar UFs do país e municípios apenas ao chegar ao estado. Usar [API de localidades do IBGE](https://servicodados.ibge.gov.br/api/docs/localidades) para nomes e IDs. Manter país, UF e município distintos dos polígonos de análise.
2. **Imagens.** Consultar `hls2-l30` e `hls2-s30` por área e intervalo de 01/04 a 31/07/2024, seguindo paginação e deduplicando IDs. Preservar coleção, cena, carimbo UTC e janela. Recortar COGs por faixa de bytes. Reusar a assinatura temporária de leitura do contêiner durante a execução. Nunca incluir URLs assinadas no JSON público. [STAC Microsoft](https://planetarycomputer.microsoft.com/docs/concepts/data-catalog/) · [HLS v2](https://planetarycomputer.microsoft.com/dataset/group/hls2)
3. **NDVI.** Usar B04 e B05 no L30, B04 e B8A no S30. Ler somente pixels cujo centro está no recorte. Aplicar nodata e Fmask para nuvem, sombra, adjacência, água e neve, rejeitar reflectância negativa e denominador nulo. `(NIR − vermelho)/(NIR + vermelho)`. O fator comum de reflectância cancela no índice. Conservar contagem, fração e mediana por cena. O mínimo inicial é 65% dos pixels esperados. Uma cena sem cobertura suficiente ou com erro de download mantém a data e NDVI nulo. Não interpolar. [Guia de HLS e Fmask](https://hls.gsfc.nasa.gov/data-products/)
4. **Imagem de demonstração.** Entre as cenas aceitas, escolher pela maior fração válida e depois pela mais recente. Renderizar RGB B04/B03/B02, reprojetar a imagem para EPSG:4326, guardar os quatro cantos em ordem superior esquerda, superior direita, inferior direita e inferior esquerda. Mostrar a cena e a data ao aproximar o recorte. Os limites do polígono ficam sobre a imagem.
5. **Chuva.** Buscar `IMERG_PRECTOT` via [NASA POWER Daily AG](https://power.larc.nasa.gov/docs/services/api/temporal/daily/) para o centro do recorte demonstrativo e intervalo UTC de 25/03 a 31/07/2024. A semana extra sustenta o acumulado antecedente de sete dias, excluindo o dia da imagem. Tratar zero e ausência separadamente. A comparação Final V07B anterior pertence a outra célula de Uberlândia. Não transferir a validação para esta área.
6. **Interface.** Mostrar NDVI por cena e chuva diária em painéis com eixo temporal comum, escalas separadas e tabela de observações. Rotular a escala de cada fonte. NDVI indica vegetação ativa. Não identifica cultura, palhada seca, causa de estresse ou risco agronômico por si só. A recomendação futura combina ZARC, janela, solo e regras do catálogo.

## Entregas em sequência

- [x] **Dados e imagem.** O recorte está dentro do município. A série entre 01/04 e 31/07/2024 tem 51 observações de NDVI aproveitáveis em 64 cenas e 122 dias UTC de chuva, sem lacunas de precipitação. O PNG é da cena `HLS.S30.T22KGE.2024212T133151.v2.0`, adquirida em 30/07/2024. Resultado em `gaiapus-mvp/data/field.json` e `scene.png`.
- [x] **Navegação do piloto.** Chrome renderizou o globo, aceitou o clique em Brasil e percorreu Brasil, Minas Gerais, Uberlândia e recorte pelo seletor. Conferidos retorno, sobreposição RGB, gráfico, tabela e largura em viewport móvel.
- [ ] **Teste de navegação fora do piloto.** Exercitar no próximo ensaio cliques em países fora do Brasil, nas outras UFs e nos demais municípios de Minas Gerais. O código já permite consultar seus limites sem atribuir a eles o pacote agrícola de Uberlândia.
- [x] **Qualidade do pacote.** O JSON preserva IDs e frações válidas por cena. As 13 cenas descartadas têm NDVI nulo e ficam na tabela. Os dias de chuva zero são preservados na série.
- [ ] **Competição.** Preparar recortes adicionais em outras UFs brasileiras com dados e critérios equivalentes. Liberar cada pacote apenas após conferir limites, cobertura, produtos e janela, sem inferir disponibilidade pelo zoom.
- [ ] **Expansão global.** Trocar a fonte das divisões subnacionais por uma camada global com identificadores versionados, mantendo IBGE para o Brasil. Testar a projeção e as geometrias na antimeridiana. Fazer busca HLS sob demanda com fila, cache e limites de uso. Configurar calendário, regras, idioma e unidades por país antes de liberar recomendações. Abertura global de dados não equivale a suporte agronômico global.

**Separação de escopo.** Este incremento adiciona observação e navegação. O mapa não depende do motor agronômico nem do ESP32. A apresentação é outro entregável. Não publicar o MVP com polígonos de produtores ou telemetria privada sem definir acesso.

## Execução local

```powershell
bun gaiapus-mvp/build.cjs
python -m http.server 8765 --bind 127.0.0.1 --directory gaiapus-mvp
```

Abra `http://127.0.0.1:8765/`. Os arquivos do recorte são locais. O mapa mundial depende do serviço de tiles MapLibre. Para regenerar os derivados, preservar o JSON bruto do POWER e as malhas locais e executar:

```powershell
$env:GDAL_DISABLE_READDIR_ON_OPEN = 'EMPTY_DIR'
uv run --with rasterio==1.4.4 --with requests --with pillow python -m data.ingest.field_imagery
```
