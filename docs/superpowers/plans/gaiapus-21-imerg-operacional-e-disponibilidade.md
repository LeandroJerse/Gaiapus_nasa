# Gaiapus — IMERG Early/Late, versões e treinamento operacional

Entrega de 14/11/2026. **Coleta piloto real concluída; treinamento operacional ainda não liberado.** Foram baixados e validados os três produtos diários de 01/01/2021 e extraídas as oito células municipais MG/MT. O catálogo 2016–2021 foi arquivado, mas o histórico de chuva Early/Late desse período **não foi baixado integralmente**.

O principal resultado metodológico é distinguir três coisas: data observada, produção/reprocessamento do arquivo e primeira disponibilidade comprovada para nosso sistema. Trocar o nome Final por Early/Late não torna um arquivo reprocessado em 2024 disponível numa decisão de 2021.

## 1. O que foi coletado e onde está

| Item | Quantidade / cobertura |
|---|---|
| Metadados CMR arquivados | 6.576 granulos: 2.192 por produto, 2016–2021 |
| Arquivos de chuva baixados | 3 NetCDF V07B, observação 01/01/2021 |
| Recortes extraídos | 24 registros: 8 municípios × Early/Late/Final |
| Pares comparáveis | 16: Early−Final e Late−Final por município |
| Completude do piloto | Todas as células com 48 intervalos válidos de meia hora |
| Testes da suíte | 38 passaram |
| Candidatos de treino auditados | 672 combinações amostra/horizonte/run; zero elegíveis |

Municípios: Uberlândia, Uberaba, Unaí e Paracatu, em MG; Sorriso, Sinop, Lucas do Rio Verde e Campo Novo do Parecis, em MT. Usam os pontos da coorte v1, não áreas cultivadas ponderadas nem talhões medidos.

Pastas: `data/raw/imerg_operational_v1/` e `data/processed/imerg_operational_v1/`. Os originais baixados pelo navegador também permanecem na raiz, onde o usuário os salvou; não foram excluídos. A cópia canônica para processamento fica em `snapshots/<sha256>/`, acompanhada de `receipt.json`. As três cópias canônicas somam **92.365.584 bytes**.

## 2. Fontes e APIs efetivamente consultadas

| Execução | Produto diário | DOI |
|---|---|---|
| Early | GPM_3IMERGDE, versão 07 | 10.5067/GPM/IMERGDE/DAY/07 |
| Late | GPM_3IMERGDL, versão 07 | 10.5067/GPM/IMERGDL/DAY/07 |
| Final | GPM_3IMERGDF, versão 07 | 10.5067/GPM/IMERGDF/DAY/07 |

**Descoberta:** [CMR NASA](https://cmr.earthdata.nasa.gov/search/granules.json), consulta GET por `short_name`, `version=07`, intervalo `temporal`, paginação `page_size=2000` e `page_num`. Foram guardadas as respostas JSON, seus hashes e os links retornados pelo catálogo.

**Download:** domínio `data.gesdisc.earthdata.nasa.gov`, caminho devolvido pelo CMR, sob `/data/GPM_L3/<produto>.07/<ano>/<mês>/`. Os acessos programáticos iniciais retornaram HTTP 401. O usuário autenticou no Earthdata e autorizou o NASA GESDISC DATA ARCHIVE no Chrome; os arquivos foram então baixados pelo navegador e importados localmente. Não se exportaram cookies, senhas ou tokens do navegador. A sessão do Chrome não foi transferida para Python.

**Subconjuntos:** o catálogo também informa serviços `opendap.earthdata.nasa.gov`. As coleções encontradas foram `C2723754850-GES_DISC` (Early), `C2723754859-GES_DISC` (Late), `C2723754864-GES_DISC` (Final). O acesso OPeNDAP sem autenticação também retornou 401; não foi usado para produzir os recortes desta entrega. Preferir subconjuntos autenticados no lote futuro para evitar baixar grades globais de aproximadamente 30 MB só para oito células.

**PPS:** foi consultado o endpoint oficial `jsimpsonhttps.pps.eosdis.nasa.gov/text/imerg/early/202101/`, também com resposta 401. Cadastro PPS é uma alternativa documentada, não foi criado nem usado nesta rodada.

A [NASA descreve Early/Late/Final](https://gpm.nasa.gov/data/imerg) e suas latências aproximadas: Early 4 h, Late 14 h e Final cerca de 3,5 meses. **As 4/14 horas referem-se ao processamento de meia hora, não são recibos de publicação de cada agregado diário.** O dia inteiro só pode estar completo após seu último intervalo e a agregação. Não gerar `available_at` artificialmente somando essas latências.

## 3. Evidência temporal dos arquivos obtidos

Todos os arquivos abaixo observam 01/01/2021 em UTC:

| Run | ProductionTime interno | Atualização CMR | Primeira importação local conservadora, UTC |
|---|---|---|---|
| Early | 09/06/2024 02:29:26,497 | 09/06/2024 02:37:05 | 14/11/2026 03:51:24,791055 |
| Late | 26/06/2024 12:32:33,143 | 26/06/2024 12:37:19 | 14/11/2026 03:52:41,591035 |
| Final | 05/01/2024 17:49:27,432 | 05/01/2024 17:52:50 | 14/11/2026 03:53:48,976517 |

`provider_first_published_at` permanece **desconhecido**. ProductionTime é a geração do arquivo; CMR updated é atualização do catálogo; Last-Modified seria alteração do recurso HTTP. Nenhum deles, isoladamente, comprova a primeira publicação daquela versão. A data de modificação do arquivo salvo no Windows também não foi usada como publicação NASA.

`first_seen_at` é a primeira importação local do conteúdo identificado por hash. É conservador: o navegador pode ter concluído o download antes, mas não foi reconstruída uma evidência confiável desse instante. Reimportar o mesmo hash preserva o recibo anterior. Conteúdo diferente cria outro snapshot, sem substituir a versão anterior.

Os registros desta entrega **não podem entrar como observações disponíveis em previsões históricas de 2021**. Podem servir a comparação retrospectiva de produtos e, respeitando datas e finalidade, a informação já conhecida em decisões futuras.

## 4. Extração e controle de qualidade

O importador confere assinatura HDF5, nome catalogado, identidade do produto, datas internas, dimensão `precipitation(time, lon, lat)`, unidade mm/dia e data da coordenada temporal. A célula mais próxima é selecionada pelas coordenadas nativas de 0,1°. Ausências e valores inválidos não viram zero.

`precipitation_cnt` acompanha a chuva; a comparação exige **48 intervalos válidos** em ambos os produtos. Chuva com contagem inferior pode estar presente no arquivo, mas fica fora deste pareamento estrito. Early e Late são comparados ao Final no mesmo dia UTC e na mesma célula. Não há conversão de fuso, correção de viés ou ajuste aprendido neste piloto.

Antes de publicar métricas, o relatório verifica o hash de cada NetCDF e **reextrai** os valores usando o recibo e o catálogo. Valores, coordenadas, disponibilidade e completude dos CSVs precisam coincidir com essa reextração. Isso evita aceitar um CSV alterado só porque o arquivo bruto continua íntegro.

## 5. Comparação real do piloto

Chuva de 01/01/2021, em mm:

| Município | Early | Late | Final |
|---|---:|---:|---:|
| Uberlândia | 0,020 | 0,200 | 0,045 |
| Uberaba | 0,205 | 0,015 | 0,040 |
| Unaí | 2,690 | 1,665 | 1,840 |
| Paracatu | 6,190 | 5,095 | 5,405 |
| Sorriso | 0,295 | 0,310 | 0,340 |
| Sinop | 1,600 | 1,220 | 1,295 |
| Lucas do Rio Verde | 4,665 | 2,820 | 2,765 |
| Campo Novo do Parecis | 5,240 | 4,320 | 1,920 |

| Comparação | MAE MG, 4 pares | MAE MT, 4 pares | MAE conjunto, 8 pares |
|---|---:|---:|---:|
| Early − Final | 0,456 mm | 1,392 mm | 0,924 mm |
| Late − Final | 0,166 mm | 0,640 mm | 0,403 mm |

O Late ficou mais próximo do Final neste dia. **Um único dia não mede desempenho climatológico e não justifica escolher um produto para treinamento.** Final é referência de produto, não verdade de campo. As estações INMET e suas lacunas continuam necessárias para validação observacional.

## 6. Contrato sem vazamento por disponibilidade

Implementado em [`operational_availability.py`](../../../data/ingest/operational_availability.py):

1. Cada valor tem chave município/dia/variável, fim da observação, primeira obtenção comprovada, hash e valor. Horários precisam incluir fuso.
2. `select_asof(records, decision_at)` só aceita observações concluídas e versões obtidas até a decisão. Não usa latência nominal nem CMR updated como substitutos.
3. Todas as revisões chegam à seleção temporal. Uma revisão posterior não apaga a versão que já estava disponível no passado. A seleção de versão mais recente para comparação retrospectiva é separada desse caminho.
4. `training_gate` exige atributos disponíveis na decisão e rótulo disponível até o ajuste. A decisão histórica da amostra não pode ser posterior ao ajuste. Calendário, produto/run e conjunto de treino devem ser fixados antes da avaliação.
5. `build_asof_hydro_features` exige as **oito dependências diárias**: chuva, Tmin, Tmax, radiação, RH, vento, ponto de orvalho e pressão. Só depois recalcula ET0 e os atributos hídricos do prefixo de 60/90 dias. Não aceita ET0 derivada de meteorologia publicada depois da decisão.

O catálogo de metadados e os recibos são parte confiável do pipeline, não campos arbitrários enviados pelo cliente web. Não preencher `first_seen_at` com a data do satélite para passar o gate. Hash prova identidade do conteúdo; a credibilidade do recibo depende de coleta e armazenamento controlados. Na produção, proteger os registros contra alteração e manter trilha de auditoria.

### Quando rodar a previsão diária

Definir separadamente `observation_cutoff` e `decision_at`. Uma previsão feita às 00:00 UTC não pode presumir que toda a chuva do dia recém-terminado já esteja no Early/Late. Esperar a versão efetivamente obtida; só calcular quando os intervalos exigidos estiverem completos. Se houver atraso, retornar cobertura insuficiente ou usar um modelo previamente treinado para aquele atraso, sem preencher com Final futuro.

Para fontes derivadas, disponibilidade é no mínimo a maior disponibilidade entre todas as dependências, acrescida do processamento. A versão do modelo também deve ter sido ajustada e disponibilizada antes da previsão.

## 7. Resultado da auditoria do treinamento

Foram auditadas as combinações de treino v3: 168 safras municipais de ajuste × dois horizontes × dois produtos = **672 candidatos**. Todos foram recusados. Motivos: série Early/Late incompleta, ausência de versões disponíveis no instante histórico, falta de disponibilidade da meteorologia usada na ET0 e falta de datas/versões dos rótulos IBGE.

**Nenhum treinamento operacional novo foi executado.** Rodá-lo agora usando datas de disponibilidade fictícias violaria o requisito principal. Modelos v3 continuam como pesquisa retrospectiva; não foram substituídos. O teste de rendimento 2022–2024 continua fechado.

Isso não impede o desenvolvimento: seleção temporal, importação, cálculo hídrico e auditoria estão implementados e testados. A liberação depende dos dados e da evidência temporal, não de remover uma flag.

## 8. Arquivos e comandos

| Arquivo | Conteúdo |
|---|---|
| `raw/imerg_operational_v1/cmr_*.json` | Respostas oficiais completas, não valores de chuva |
| `raw/imerg_operational_v1/access_attempts_*.json` | Tentativas HTTP antes da autorização; 401 preservados |
| `raw/imerg_operational_v1/snapshots/<hash>/` | NetCDF original e recibo da obtenção local |
| `processed/imerg_operational_v1/catalog.csv` | 6.576 identificadores, datas, links e hashes de catálogo |
| `extractions/*.csv` | Valores e qualidade por célula/snapshot |
| `availability_ledger.csv` | Datas observadas/produção/catálogo/primeira obtenção |
| `rain_comparison_daily.csv` | 16 diferenças pareadas do piloto |
| `rain_comparison_summary.csv` | MAE/RMSE/viés por run e estado |
| `operational_training_gate.csv` | 672 decisões de elegibilidade com motivos |
| `readiness.json` | Estado consolidado atual; não confundir com tentativa HTTP antiga |
| `delivery_manifest.json` | Inventário final e checksums |

```powershell
# Verificar implementação e refazer relatórios sem rede
python -m unittest discover -s tests
python -m data.ingest.report_imerg_operational

# Importar outro original já baixado pelo navegador
python -m data.ingest.imerg_operational --reuse-catalog --import-file 'CAMINHO_DO_ARQUIVO.nc4'

# Tentar outro dia do catálogo com autenticação programática configurada externamente
python -m data.ingest.imerg_operational --reuse-catalog --probe-day 2021-01-02

# Congelar a entrega após a verificação
python -m data.ingest.freeze_imerg_operational
```

O coletor Requests aceita o mecanismo padrão netrc quando previamente configurado pelo usuário; **não foi configurado nesta entrega**. Login no navegador não autentica automaticamente o processo Python. Sem autenticação programática, o comando de coleta pode continuar retornando 401. Não colocar credenciais no repositório ou em argumentos de comando. Para novo intervalo, `--start`/`--end` consultam novamente o CMR e substituem o índice CSV corrente; os brutos datados permanecem arquivados. Não alterar o intervalo do índice de uma entrega congelada: usar outra versão da pasta/coleta.

## 9. Plano para liberar operação

1. Concluir uma sessão programática autenticada pelo método oficial NASA, sem exportar cookies do navegador. Validar um subconjunto OPeNDAP antes de qualquer lote grande.
2. Ampliar a comparação com uma janela pré-definida cobrindo estação seca/chuvosa e ciclos completos de soja/milho. Usar os metadados já arquivados, limites de concorrência e cache por hash. Não escolher dias olhando previamente onde um produto ganha do outro.
3. Comparar por mês/célula/estado e depois contra INMET, respeitando dias completos e ausência de observação local. Ajustes de viés somente no treino e com avaliação temporal independente.
4. Para histórico operacional, obter versões originais e evidência de publicação daquela época, ou reconhecer que o experimento será retrospectivo. A produção em 2024 dos arquivos deste piloto descarta seu uso como versão de 2021.
5. Para operação prospectiva, começar a guardar versões recebidas com recibos de ingestão; incluir meteorologia de baixa latência com pressão/orvalho/radiação/vento, suas versões e qualidade. Nenhum agendamento automático foi criado nesta tarefa.
6. Registrar publicação e revisão dos rótulos de colheita. Definir data de ajuste e splits por disponibilidade real, mantendo municípios reservados e teste final fechado.
7. Só após haver ciclos completos elegíveis, ajustar modelos separados ou um contrato explícito por produto/latência; comparar com as ablações v3 e avaliar degradação de Early/Late frente ao Final. Não treinar com Final e trocar silenciosamente para Late na inferência.

O status continua `production_approved=false`. O guia [20](gaiapus-20-treinamento-com-deficit-hidrico.md) descreve os modelos retrospectivos existentes; este documento acrescenta o caminho e os bloqueios reais para operação.
