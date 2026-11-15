# Gaiapus — coleta IMERG por subset, comparação de produtos e estudo MLP

Execução de 14/11/2026, a partir do [guia 23](gaiapus-23-prompt-claude-coleta-e-mlp.md). Todos os números abaixo foram medidos nesta execução. **Nenhum modelo foi promovido a produção e o teste de rendimento 2022–2024 continua fechado.**

## 1. Auditoria inicial

| Item | Medido às 08:22 (UTC−3) |
|---|---|
| Git | `main` igual a `origin/main`, árvore limpa |
| Lote IMERG de arquivos globais | **Ativo** (PID 10344, iniciado 08:12 por outra sessão), 218 de 6.576 arquivos, 6,02 GB |
| Ritmo do lote global | ~36 GB/h, ~1 dia completo a cada 8 s; projeção de ~200 GB e ~5 h |
| Disco livre em C: | 487 GB |
| Extrações anteriores | 74 arquivos; relatório com 2 dias comparados (01 e 02/01/2021) |
| Credencial nesta sessão | Ausente no início; token novo fornecido localmente e guardado em variável de ambiente do usuário |

Nenhum segundo gravador foi iniciado sobre `data/raw/imerg_operational_v1`. O lote global seguiu intocado.

## 2. Subset autenticado: equivalência e custo

O guia pedia medir um piloto e avaliar o subset. O serviço OPeNDAP do GES DISC devolve só a caixa que contém as oito células (112 × 80 células de 0,1°).

| Verificação em 02/01/2021 | Resultado |
|---|---|
| Células comparadas contra o NetCDF global já baixado | 24 de 24 (8 municípios × 3 produtos) |
| Diferença máxima de chuva | 0,0 mm/dia |
| Longitude/latitude da célula, unidade (`mm/day`) e contagem de meias horas | Idênticas |
| Tamanho por arquivo | ~52 KB contra ~30 MB (cerca de 600 vezes menor) |
| Tempo por requisição | 1,5 a 2,0 s |

Os atributos `title`, `BeginDate`, `EndDate` e `ProductionTime` vêm preservados, e o coletor recusa produto, dia, unidade ou grade divergentes.

Implementação: [`data/ingest/imerg_subset.py`](../../../data/ingest/imerg_subset.py), testes em [`tests/test_imerg_subset.py`](../../../tests/test_imerg_subset.py).

```powershell
# EARTHDATA_TOKEN no ambiente do processo; o valor nunca é gravado nem impresso
python -m data.ingest.imerg_subset --workers 10      # retoma o que já foi verificado
python -m data.ingest.imerg_subset --build-only      # refaz as tabelas a partir dos brutos
python -m data.ingest.train_imerg_products
```

Cada resposta fica imutável em `data/raw/imerg_subset_v1/<produto>/<ano>/` com recibo em `receipts.jsonl`: URL, granule ID, SHA-256 dos bytes recebidos, horários de início e fim, `Last-Modified` e a caixa pedida. As tabelas em `data/processed/imerg_subset_v1/` são sempre reextraídas dos brutos verificados. A evidência de disponibilidade continua sendo apenas a primeira obtenção local em 2026; `provider_first_published_at` fica vazio.

## 3. Coleta 2016–2021 concluída e comparação Early/Late/Final

### Cobertura medida

| Item | Resultado |
|---|---|
| Arquivos esperados (3 produtos × 2.192 dias) | 6.576 |
| Arquivos verificados por hash e reextraídos | 6.576 |
| Município-dia com os três produtos completos (48 meias horas) | 17.536 de 17.536 |
| Células extraídas | 52.608, nenhuma com chuva ausente ou incompleta |
| Volume bruto | 309 MB (contra ~200 GB projetados para os arquivos globais) |
| Falhas na primeira passada | 209: uma resposta HTTP 401 interrompeu o lote e 13 tentativas de rede se esgotaram. Todas foram recuperadas na retomada |

O 401 isolado não se repetiu na retomada. O coletor para o lote diante de 401/403 por desenho, para não insistir com credencial inválida.

### Equivalência com os arquivos globais

O módulo [`imerg_equivalence.py`](../../../data/ingest/imerg_equivalence.py) compara cada célula do subset com todas as células já extraídas dos NetCDF globais: 12.048 células, 1.506 arquivos e 502 dias. A diferença máxima de chuva foi 0,0 mm/dia, sem divergência de contagem de meias horas, célula, unidade, produto ou `ProductionTime`. Resultado em `data/processed/imerg_subset_v1/equivalence_with_full_files.json`.

### Lote global encerrado

Confirmada a redundância, foi encerrado às 09:19 (UTC−3) somente o processo 10344, depois de conferir executável, comando (`imerg_operational --batch`), processo pai e horário de início. Os 1.517 snapshots globais baixados (43,9 GB) foram preservados, todos com recibo e um único NetCDF por pasta. O relatório desse lote não foi refeito; o subset é a fonte da comparação.

### Diferença entre produtos (mesma célula, mesmo dia UTC, Final como referência)

| Produto | Estado | Pares | MAE (mm/dia) | RMSE | Viés |
|---|---|---:|---:|---:|---:|
| Early | MG | 8.768 | 1,56 | 10,26 | +0,38 |
| Early | MT | 8.768 | 1,34 | 3,03 | +0,01 |
| Late | MG | 8.768 | 1,20 | 10,17 | +0,27 |
| Late | MT | 8.768 | 0,93 | 2,29 | +0,06 |

Late fica mais perto do Final que Early nos dois estados. O RMSE alto de MG vem de poucos dias com diferenças extremas. O Final não é verdade de campo; a comparação mede diferença entre versões do produto.

### Versão e primeira obtenção

| Produto | Coleção | `ProductionTime` dos arquivos | Primeira obtenção local verificada (UTC) |
|---|---|---|---|
| Early | `GPM_3IMERGDE` V07B | 07 a 09/06/2024 | 14/11/2026 11:29 a 12:16 |
| Late | `GPM_3IMERGDL` V07B | 11 a 26/06/2024 | 14/11/2026 11:49 a 12:16 |
| Final | `GPM_3IMERGDF` V07B | 19/12/2023 a 08/01/2024 | 14/11/2026 12:03 a 12:17 |

Todos são **reprocessamentos de 2023–2024**. A disponibilidade histórica na data de cada safra não está comprovada: `provider_first_published_at` continua vazio e a única evidência é a primeira obtenção local em 2026.

### Efeito da troca de produto no rendimento

[`train_imerg_products.py`](../../../data/ingest/train_imerg_products.py) usa os mesmos exemplos para os três produtos, com ET0, demais variáveis e rótulos fixos. Ajuste com 2017–2018, validação com 2020–2021, nos oito municípios: 64 safras, 128 exemplos por produto e nenhum excluído por lacuna.

MAE (kg/ha), validação temporal (12 safras) com Ridge:

| Cultura e corte | Média histórica | Clima: Early / Late / Final | Clima + hídrico: Early / Late / Final |
|---|---:|---:|---:|
| Soja 60 | 170 | 256 / 267 / 251 | 213 / 245 / 216 |
| Soja 90 | 170 | 244 / 186 / 180 | 233 / 225 / 232 |
| Milho 60 | 1.039 | 923 / 913 / 914 | 906 / 879 / 881 |
| Milho 90 | 1.039 | 975 / 967 / 966 | 934 / 898 / 909 |

Com 12 safras de ajuste e 12 de validação, a troca de produto altera o MAE em poucas dezenas de kg/ha, sem direção consistente. Na soja, nenhuma variante supera a média histórica. Conclusão: este recorte não sustenta a escolha de um produto de chuva pelo rendimento. A escolha operacional deve seguir latência e disponibilidade (Early ~4 h, Late ~14 h, Final ~3,5 meses), não esta comparação.

## 4. Estudo MLP contra os baselines

Implementação: [`data/ingest/train_mlp_v4.py`](../../../data/ingest/train_mlp_v4.py), testes em [`tests/test_mlp_training.py`](../../../tests/test_mlp_training.py). Saídas em `data/processed/training_v4/` e `data/models/research_v4/`.

### Desenho

- Mesmas linhas, divisões e contratos de atributos do [v3](gaiapus-20-treinamento-com-deficit-hidrico.md): ajuste 2002–2016 em seis municípios, validação 2019–2021, Uberaba e Sinop fora do ajuste. O arquivo de entrada é conferido por hash e não contém 2017–2018 nem 2022–2024.
- Rótulos independentes no ajuste: 90 safras de soja e 78 de milho de segunda safra. Os dois horizontes repetem as mesmas safras.
- Baselines recalculados no mesmo código: média histórica do treino, tendência (Ridge no ano), Ridge e HGB com clima e com clima + balanço hídrico. O script falha se o MAE divergir do v3 em mais de 1e-6; não divergiu.
- MLP: grade congelada antes de ler a validação, camadas `(8)` ou `(16, 8)`, L2 `alpha` em {0,1; 1; 10}, Adam, lotes de 16. Parada antecipada e escolha da configuração usam só os três últimos anos do treino (2014–2016). Imputação e escala ajustadas apenas no treino. Reajuste final em todo o treino com 10 sementes; reporta-se o conjunto e a variação entre sementes.
- Regra de promoção, também fixada antes: MAE do conjunto menor que o melhor baseline nas duas validações e pelo menos 80% das sementes abaixo dele na validação temporal.
- Solo e HLS não entraram: não existe tabela por exemplo dessas fontes (ver bloqueios).

### Resultado

MAE em kg/ha, validação temporal (n=18) / espaço-temporal (n=6).

| Modelo | Soja 60 | Soja 90 | Milho 60 | Milho 90 |
|---|---:|---:|---:|---:|
| Média histórica | 567,6 / 634,8 | 567,6 / 634,8 | 1.792,3 / 1.584,1 | 1.792,3 / 1.584,1 |
| Tendência (Ridge) | 367,9 / 480,7 | 367,9 / 480,7 | 1.286,3 / 1.133,1 | 1.286,3 / 1.133,1 |
| Ridge clima | 381,4 / 423,7 | 350,5 / 354,8 | 1.331,3 / 1.356,8 | 1.244,7 / 1.317,0 |
| Ridge clima + hídrico | 451,6 / 484,2 | 414,9 / 421,1 | 1.355,5 / 1.372,3 | 1.313,1 / 1.287,8 |
| HGB clima | 506,1 / 582,7 | 507,1 / 513,0 | 1.552,5 / 1.523,4 | 1.513,4 / 1.297,6 |
| HGB clima + hídrico | 475,3 / 558,5 | 530,7 / 470,0 | 1.531,7 / 1.533,2 | 1.485,1 / 1.119,0 |
| MLP clima | 533,1 / 598,5 | 541,0 / 599,6 | 1.782,7 / 1.576,6 | 1.781,2 / 1.575,4 |
| MLP clima + hídrico | 520,2 / 569,7 | 549,8 / 596,0 | 1.794,3 / 1.585,8 | 1.794,1 / 1.585,7 |
| MLP residual clima | 371,7 / 462,6 | 379,3 / 465,1 | 1.292,6 / 1.145,5 | 1.291,2 / 1.145,5 |
| MLP residual clima + hídrico | 364,4 / 478,3 | 366,8 / 471,8 | 1.288,1 / 1.136,9 | 1.280,6 / 1.127,4 |

**Nenhum MLP foi promovido** nas 16 combinações de cultura, horizonte, variante e tipo de rede.

### Por que o MLP direto perde

A busca interna escolheu `alpha=10`, a borda mais regularizada da grade, em todos os casos. A rede ficou quase igual à média do treino: na soja o viés é de −520 a −550 kg/ha, contra −567,6 da média histórica. A produtividade cresce ao longo de 2002–2016 e a validação está três a cinco anos adiante; uma rede regularizada não extrapola essa tendência. O erro vem da tendência, não do clima.

### MLP sobre o resíduo da tendência

Depois dessa primeira leitura foi acrescentada uma segunda rede, que aprende só o desvio em relação à tendência linear. **Essa variante foi declarada após ver a validação** e está marcada assim no manifesto. Usa a mesma grade e a mesma regra.

- Ela empata com a tendência linear: diferença de até 9 kg/ha na soja e 6 kg/ha no milho na validação temporal, dentro da variação entre sementes (desvio de 12 a 108 kg/ha).
- Supera o melhor baseline temporal por 3,5 kg/ha em um único caso (soja 60, clima + hídrico: 364,4 contra 367,9), com 50% das sementes melhores e pior na espaço-temporal (478,3 contra 423,7). Não cumpre a regra.
- Novamente `alpha=10` foi escolhido: os atributos de clima e de balanço hídrico não reduziram o erro interno de forma que justificasse menos regularização.

Comparação pareada contra o Ridge de mesma variante, validação temporal, com bootstrap por município (6 grupos): os intervalos de 95% do MLP residual cruzam zero em todos os oito casos. Na espaço-temporal há só dois municípios; o intervalo calculado ali não tem valor inferencial.

### Leitura por estado (horizonte 90, validação temporal)

| Modelo | Soja MG | Soja MT | Milho MG | Milho MT |
|---|---:|---:|---:|---:|
| Tendência | 384,0 | 351,9 | 1.831,6 | 741,1 |
| Ridge clima | 520,6 | 180,4 | 1.727,1 | 762,3 |
| HGB clima + hídrico | 638,1 | 423,4 | 2.292,8 | 677,5 |
| MLP residual clima + hídrico | 392,6 | 341,0 | 1.829,6 | 731,6 |

O erro de milho em MG segue acima de 1.700 kg/ha em todos os modelos. Todos subestimam 2019–2021 (viés negativo em todas as linhas).

### Decisão

1. **Entregar baselines simples**: tendência para o horizonte 60 e Ridge de clima para o horizonte 90, como referência de pesquisa. O MLP fica documentado e não é servido.
2. A validação 2019–2021 já foi lida em várias rodadas e não é teste independente. A escolha acima não deve ser tratada como estimativa final de erro.
3. O teste 2022–2024 não foi aberto. Antes de abri-lo falta congelar fontes e atributos, o que depende dos bloqueios da seção 6.

### Artefatos do v4

| Arquivo em `data/processed/training_v4/` | Conteúdo |
|---|---|
| `examples.parquet` | 542 linhas, uma por exemplo, 271 famílias de rótulo, com auditoria de corte |
| `feature_dictionary.csv` | Unidade, grupo, janela e fração ausente de cada atributo |
| `quality_report.json` | 0 chaves duplicadas, 0 observações após o corte, 0 janelas incompletas, 0 linhas de teste |
| `model_comparison.csv` | MAE, RMSE e viés de todos os modelos nas mesmas linhas |
| `model_comparison_by_group.csv` | Métricas por estado, município e ano |
| `mlp_inner_search.csv` | Grade, erro interno e épocas escolhidas |
| `mlp_seed_variability.csv` | Média, desvio, mínimo e máximo do MAE entre 10 sementes |
| `paired_mlp_vs_ridge.csv` | Diferença pareada de erro absoluto e intervalo por município |
| `promotion_decisions.csv` | Aplicação da regra de promoção |
| `validation_predictions.csv` | Predições individuais da validação |

`data/models/research_v4/` guarda 16 pacotes joblib (transformador, redes, média e escala do alvo, termo de tendência quando houver) e `manifest.json` com hashes, grade, sementes e `test_evaluated=false`.

Ambiente: Python 3.13.3, scikit-learn 1.7.2, pandas 2.3.3, pyarrow 26.0.1 em venv `uv` fora do OneDrive (`%USERPROFILE%\.venvs\gaiapus`, com os pacotes do sistema).

## 5. Bloqueios registrados

| Fonte | Situação medida | Consequência |
|---|---|---|
| MapBiomas | Os rasters públicos devolvem HTTP 403 em todas as URLs testadas, inclusive `collection_10/lulc/coverage/brazil_coverage_<ano>.tif`, citada no site oficial | Sem máscara agrícola; os pontos seguem como ponto representativo do município |
| HLS | Disponível a partir de 2013; o treino começa em 2002 | Comparação exige uma coorte menor e separada; não executada |
| MODIS MOD13C1 | O OPeNDAP da LP DAAC devolve 404 (falta `.dmrpp`) | Alternativa seria baixar arquivos HDF4 completos (~44 GB) |
| Datas de publicação | Não há registro histórico para IMERG, POWER ou PAM | Todos os resultados são retrospectivos |

A continuação (coorte de 15 estados, treino v6, Meteomatics, projeção hídrica e contratos) está no [guia 26](gaiapus-26-v6-produtos-e-contratos.md).
