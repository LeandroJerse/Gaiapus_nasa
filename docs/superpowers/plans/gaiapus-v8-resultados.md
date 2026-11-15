# Gaiapus v8 — resultados da melhoria preditiva

Plano executado: `docs/superpowers/plans/2026-11-14-gaiapus-v8-melhoria-preditiva.md`.
Prioridade: soja MG/MT → milho safrinha → café. Data de execução: 2026-11-14.

## 0. Resumo executivo

Classificação da avaliação 2025: **retrospectiva, com exposição prévia documentada** (clima final sem vintage comprovado; PAM 2025
vista em depuração, margens e snapshots ao vivo, sem influência conhecida na seleção). Nada aqui é validação operacional.
Ganho relativo = (MAE_ref − MAE_modelo)/MAE_ref; negativo = piora. MAE em kg/ha por safra, municípios de treino.

| Cultura / escopo | Modelo servido hoje (job ao vivo) | Candidato v8 congelado | Referência primária | Desenvolvimento 2019–2021: v8 / ref. / v7 | 2025: v8 / ref. / v7 servido | Status científico | Elegibilidade operacional | Recomendação |
|---|---|---|---|---|---|---|---|---|
| Soja MG/MT | v7 MLP regional (MG+MT); Ridge site v7 nos demais | history + F3 + HGB, pool MG+MT | history | 175,0 / 209,7 / 174,1 (MLP reg.) | 198,5 / 353,6 / 237,0 (MLP reg.); Ridge site 198,8 | Passa a regra nos dois períodos contra history; superioridade sobre a v7 **não demonstrada** (desenvolvimento Δ +0,9 [−14,1; +15,3]; 2025 contra Ridge site Δ −0,3 [−14,1; +13,5], equivalência também não demonstrada); melhor que a MLP regional servida em 2025 (Δ −38,5 [−57,2; −18,9]) | Não elegível (retrospectivo, sem confirmação prospectiva) | Manter v7; candidato à confirmação prospectiva junto com o Ridge site v7 e a MLP regional |
| Milho safrinha MG/MT | v7 Ridge site | ref_lag_mean + F1 + MLP, pool nacional | history | 1.937,2 / 1.688,8 / 1.544,0 (pior em tudo) | 885,4 / 1.179,9 / 1.082,8 | Resultados **contraditórios** (pior no desenvolvimento, melhor em 2025): inconclusivo | Não elegível | Manter Ridge v7; registrar para confirmação prospectiva sem expectativa de ganho |
| Milho safrinha nacional | v7 Ridge site | ref_lag_mean + F2 + MLP | history | 1.206,8 / 1.203,0 / 1.146,7 | 1.367,3 / 1.430,3 / 1.326,6 | Não passa; pior que v7 nos dois períodos | Não elegível | Manter Ridge v7 |
| Café arábica MG | v7 site HGB | ref_last_available + F2 + HGB | history | 326,1 / 281,8 / 262,7 (v8 pior) | 223,5 / 277,1 / 224,8; ref_cycle_mean 227,9 | Passa contra history em 2025; diferença contra ref_cycle_mean e v7 não demonstrada; pior no desenvolvimento: inconclusivo. A v7 servida falhou no teste 2022–2024 (453,6 × history 426,0) | Não elegível | Manter o servido até revisão do catálogo; nenhum modelo de aprendizado confirmado em teste para arábica |
| Café total MG | v7 hydro HGB | ref_cycle_mean + F3 + HGB | history | 332,0 / 301,7 / 292,3 | 246,4 / 231,3 / 250,4 | Não passa; history foi o melhor em 2025 | Não elegível | Manter o servido até revisão; history é a referência mais sólida |
| Café conilon ES | ref_last_available | modelo direto + F3 + MLP | ref_last_available | 971,1 / 615,6 (muito pior) | 358,4 / 451,1; ref_cycle_mean 382,2 | Passa contra a primária em 2025; diferença contra ref_cycle_mean não demonstrada; muito pior no desenvolvimento: inconclusivo | Não elegível | Manter ref_last_available (única escolha confirmada no teste 2022–2024) |

**Conclusão: nenhum candidato demonstrou superioridade consistente suficiente para promoção.** A rodada v8 está encerrada; a v8
permanece experimental e o catálogo servido (v7) não muda.

- Soja MG/MT (candidato congelado: base history + corretor **HGB**, não MLP): supera as referências simples em 2019–2021 e em 2025
  e supera a MLP regional v7 servida em MG/MT em 2025 (Δ −38,5 kg/ha [−57,2; −18,9]). Fica próxima do Ridge site v7 em 2025
  (198,5 × 198,8; Δ −0,3 [−14,1; +13,5]) — o intervalo não exclui diferenças de ±14 kg/ha e **não** demonstra equivalência.
- Famílias dos candidatos congelados: soja history + HGB; milho MG/MT e nacional ref_lag_mean + MLP; arábica ref_last_available +
  HGB; café total MG ref_cycle_mean + HGB; conilon modelo direto (média) + MLP.
- Negativos: milho e café no desenvolvimento; milho nacional e café total MG também em 2025; milho MG/MT, arábica e conilon com
  sinais opostos entre os dois períodos. Irrigação sem efeito; janela de 11 anos e perda MSE seguem como hipóteses exploratórias.
- 2025 não será reaberto para ajustar modelos; qualquer ajuste novo exige protocolo novo e avaliação em dados ainda não vistos
  (plano: `docs/superpowers/plans/gaiapus-v9-plano-avaliacao-operacional.md`).

## 1. Auditoria e protocolo

- Auditoria: `data/ingest/audit_v8.py` → `data/processed/training_v8/audit.json` (manifesto de desenvolvimento com sha256,
  inventário, disponibilidade de lags, definição das referências). Calendário real de divulgação da PAM (IBGE, calendário 9117):
  2019→2020-10-01, 2020→2021-09-22, 2021→2022-09-15, 2022→2023-09-14, 2023→2024-09-12, 2024→2025-09-11, 2025→2026-09-17.
- Inventário de desenvolvimento (≤2021): soja 15.744 safras/790 municípios (MG+MT 2.418); milho safrinha 6.687/375 (MG+MT 1.433);
  café arábica 2.476/311; café total MG 5.996/317; conilon ES 480/60. Nenhuma linha com lag não publicado na decisão.
  `high_year` (derivado do rótulo) não é feature.
- Protocolo registrado **antes** de qualquer score v8: `data/frozen/training_v8_protocol.json` (commit `3774ce1`): partições,
  escopos, candidatos, experimentos, métricas, critérios de promoção (≥5% de redução relativa de MAE contra a referência simples
  mais forte, IC95% superior < 0, nenhuma UF prioritária piorando >5%) e o procedimento da avaliação independente 2025.
- Contrato temporal: `data/ingest/cohort_v8.py` (allowlist + checksum, rejeição de safra >2021, chaves duplicadas, lag não publicado,
  previsão emitida/disponível após a decisão, observação após a decisão). Testes: `tests/test_v8_leakage.py` (7),
  `tests/test_train_v8.py` (5), `tests/test_evaluate_2025_v8.py` (4), `tests/test_v8_package.py` (4). Suíte completa: 152 testes OK
  antes da exportação.
- Desenho: folds externos 2019, 2020, 2021 (treino ≤ Y−2, holdout espacial ~20% excluído do treino); seleção aninhada dentro do treino
  de cada fold (validação interna = 2 últimos anos de treino, ajuste interno ≤ primeiro ano de validação − 2). Cortes agregados por
  safra antes de agregar safras; MG/MT com peso 50% por UF; bootstrap por município estratificado por UF (2.000 reamostragens).
- O teste 2022–2024 publicado (`data/frozen/test_2022_2024_v7.json`) **não** foi aberto para seleção, features ou correção de viés.

Ressalva registrada: os anos 2019–2021 já foram inspecionados em rodadas anteriores (v6/v7); a validação v8 é evidência condicionada
ao processo, não confirmação independente.

## 2. Fontes (treino × operação)

Comando: `python -m data.ingest.source_report_v8` → `data/processed/training_v8/sources/`.

Definições comuns: erro = produto comparado − referência; MAE = média de |erro|; viés = média do erro; correlação de Pearson;
dia chuvoso = ≥1 mm; "falso alarme" = referência <1 mm e produto ≥1 mm; "perda" = referência ≥1 mm e produto <1 mm.
Nenhuma métrica abaixo foi usada para corrigir chuva, escolher fonte, normalizar ou ajustar hiperparâmetros (ver §2.4).

### 2.1 IMERG Late/Early × IMERG Final (consistência entre produtos, sem verdade de solo)

- Período: 2016-01-01 a 2021-12-31 (dias UTC). Locais: 8 municípios MVP (MG 3170206, 3170107, 3170404, 3147006; MT 5107925,
  5107909, 5105259, 5102637), célula IMERG 0,1° do ponto representativo do município.
- Dados: subconjunto OPeNDAP GES DISC V07 (`data/processed/imerg_subset_v1/points.csv`), só dias com as 48 meias-horas completas
  nas três corridas. Cobertura: 17.536 município-dias pareados (MG 8.768, MT 8.768).
- Agregação: diária por célula; linha "somas mensais" = soma por município × mês (576 pares).

| Comparação | Pares | MAE mm/dia | Viés mm/dia | RMSE mm/dia | Correlação | Soma referência (Final) | Soma produto |
|---|---|---|---|---|---|---|---|
| Early × Final, todos | 17.536 | 1,45 | +0,19 | 7,57 | 0,760 | 78.853 mm | 82.228 mm (+4,3%) |
| Late × Final, todos | 17.536 | 1,07 | +0,16 | 7,37 | 0,792 | 78.853 mm | 81.719 mm (+3,6%) |
| Late × Final, MG | 8.768 | 1,20 | +0,27 | 10,17 | 0,638 | 32.956 mm | 35.339 mm (+7,2%) |
| Late × Final, MT | 8.768 | 0,93 | +0,06 | 2,29 | 0,977 | 45.897 mm | 46.379 mm (+1,1%) |
| Late × Final, somas mensais | 576 | 26,8 mm/mês | +5,0 mm/mês | 68,3 | 0,881 | — | — |

Dias chuvosos (Late × Final, todos): 6.220 acertos, 347 perdas, 277 falsos alarmes; dias ≥50 mm: 133 no Final, 146 no Late.

### 2.2 IMERG Final e Late × pluviômetros INMET (2025)

- Período: 2025-01-01 a 2025-12-31. Horas INMET (UTC) somadas em dias UTC, como em `rain_validation_v2`; dia aceito só com as
  24 horas presentes.
- Estações selecionadas: 12 (MT: A904 Sorriso, A917 Sinop, A903 S.J. do Rio Claro, A902 Tangará da Serra, A911 Sapezal,
  A905 Campo Novo dos Parecis, A928 Nova Maringá, A927 Brasnorte; MG: A507 Uberlândia, A568 Uberaba, A542 Unaí, A571 Paracatu).
  **Sem nenhum dia válido em 2025: A903, A905, A917, A928** → 8 estações efetivas.
- Final: POWER `IMERG_PRECTOT` nas coordenadas da estação. Late: GES DISC IMERG Late V07B, célula da estação, um grânulo diário
  por dia (cache `data/cache/v8_station_late`). Mesmos dias e estações nos dois produtos.
- Cobertura (dias pareados de 365): A507 357, A911 360, A571 272, A902 264, A904 256, A568 231, A542 206, A927 100 (só a partir de
  16/09). Total 2.046 estação-dias.

| Produto | Pares | MAE mm/dia | Viés mm/dia | RMSE mm/dia | Correlação | Soma INMET | Soma produto | Acertos/perdas/falsos alarmes | Dias ≥50 mm (INMET/produto) |
|---|---|---|---|---|---|---|---|---|---|
| IMERG Final | 2.046 | 3,26 | +0,04 | 7,68 | 0,685 | 8.110 mm | 8.194 mm (+1,0%) | 507 / 60 / 299 | 19 / 12 |
| IMERG Late | 2.046 | 3,45 | +0,45 | 7,88 | 0,684 | 8.110 mm | 9.022 mm (+11,2%) | 501 / 66 / 325 | 19 / 15 |

Somas mensais nos dias pareados (86 estação-meses): Final MAE 25,9 mm/mês, viés +1,0, correlação 0,923; Late MAE 26,2,
viés +10,6, correlação 0,927.

Por estação (MAE mm/dia, Final → Late; viés Final → Late): A507 2,73 → 3,00 (+0,68 → +1,08); A542 1,32 → 1,32 (+0,30 → +0,25);
A568 1,64 → 1,94 (+0,07 → +0,20); A571 2,08 → 2,21 (+1,26 → +1,35); A902 5,19 → 5,40 (−0,13 → +0,27); A904 4,48 → 4,57
(−0,22 → +0,27); A911 4,11 → 4,40 (−0,83 → +0,22); A927 4,92 → 4,91 (−1,90 → −1,58).

Localização, cobertura sazonal e lacunas (`python -m data.ingest.inmet_coverage_v8` → `sources/rain_inmet_2025_coverage.json`):

| Estação | UF | Lat, lon | Dias pareados | Chuvoso out–mar / seco abr–set | Maior lacuna (dias) | Meses sem par |
|---|---|---|---|---|---|---|
| A507 Uberlândia | MG | −18,92, −48,26 | 357 | 175 / 182 | 2 | — |
| A571 Paracatu | MG | −17,24, −46,88 | 272 | 140 / 132 | 32 | — |
| A568 Uberaba | MG | −19,71, −47,96 | 231 | 61 / 170 | 10 | — |
| A542 Unaí | MG | −16,55, −46,88 | 206 | 39 / 167 | 35 | — |
| A911 Sapezal | MT | −13,30, −58,76 | 360 | 177 / 183 | 2 | — |
| A902 Tangará da Serra | MT | −14,65, −57,43 | 264 | 173 / 91 | 63 | ago |
| A904 Sorriso | MT | −12,56, −55,72 | 256 | 145 / 111 | 51 | ago |
| A927 Brasnorte | MT | −12,52, −58,23 | 100 | 85 / 15 | 2 | jan–ago (início 16/09) |
| A903, A905, A917, A928 | MT | — | 0 | — | — | todos |

Em Unaí e Uberaba o período chuvoso está sub-representado (39 e 61 dias), justamente onde a chuva importa mais.

Incerteza sem tratar os 2.046 pares como independentes: bootstrap de blocos estação × mês (86 blocos) e de estações inteiras (8),
2.000 reamostragens:

| Medida | Pontual | IC95 blocos estação×mês | IC95 estações inteiras |
|---|---|---|---|
| MAE(Late) − MAE(Final), mm/dia | +0,19 | [+0,09; +0,30] | [+0,10; +0,25] |
| Viés Final, mm/dia | +0,04 | [−0,31; +0,39] | [−0,51; +0,55] |
| Viés Late, mm/dia | +0,45 | [+0,10; +0,79] | [+0,03; +0,81] |
| Razão de somas Final/INMET | 1,01 | [0,93; 1,11] | [0,90; 1,18] |
| Razão de somas Late/INMET | 1,11 | [1,02; 1,22] | [1,01; 1,26] |

Denominadores: "viés Late" e "viés Final" nesta tabela são **produto − INMET** (erro contra observação, mm/dia). A linha
MAE(Late) − MAE(Final) é a diferença entre os dois erros contra o INMET. Já em §2.1, "Late × Final" é **Late − Final**
(+0,16 mm/dia, 2016–2021, MVP): diferença entre produtos, não erro.

Por estação do ano (produto − INMET): período chuvoso (995 pares) Late +0,74, Final +0,22 mm/dia; período seco (1.051 pares)
Late +0,16, Final −0,13. Com 8 estações e um ano, o IC por estações inteiras é o mais honesto: o viés Late − INMET (+0,45,
IC [+0,03; +0,81]) é positivo, enquanto o viés Final − INMET (+0,04, IC [−0,51; +0,55]) não se distingue de zero. A cobertura
desigual (Unaí e Uberaba com poucos dias chuvosos; Brasnorte só a partir de setembro; 4 estações sem dados) limita a generalização.

Limites: ponto × célula de ~10 km (afeta os dois produtos igualmente); 2025 é um ano; versão POWER do Final em 2025 não é
informada dia a dia pela API (assumido Final V07 por já terem passado >3,5 meses). Classificação: estudo retrospectivo das fontes.

### 2.3 ET0: radiação CERES (treino) × ERA5 (operação)

Cadeia de cálculo (igual nos dois lados): ET0 FAO-56 Penman-Monteith diária (`data/models/water_balance.et0_daily`) com
T2M_MIN, T2M_MAX, WS2M, PS e T2MDEW do NASA POWER, latitude e elevação do município. **Só a radiação de onda curta é substituída**:
- treino: POWER `ALLSKY_SFC_SW_DWN` (CERES), MJ/m²/dia;
- operação: Open-Meteo archive `shortwave_radiation_sum` com `models=era5`, MJ/m²/dia; ET0 recalculada com `swap_sources`.
- No job ao vivo a radiação CERES é mantida onde existe e o ERA5 cobre apenas os dias recentes sem CERES; a chuva é substituída
  integralmente pelo IMERG Late.

Período 2016–2021, 8 municípios MVP, 2.192 dias cada, agregação diária:

| Município | ET0 MAE mm/dia | ET0 viés mm/dia | ET0 média CERES mm/dia | Radiação MAE MJ/m²/dia | Radiação viés |
|---|---|---|---|---|---|
| 3170206 Uberlândia | 0,262 | −0,009 | 4,50 | 2,02 | −0,04 |
| 3170107 Uberaba | 0,234 | +0,035 | 4,54 | 1,81 | +0,22 |
| 3170404 | 0,247 | −0,053 | 4,78 | 1,84 | −0,45 |
| 3147006 | 0,246 | −0,040 | 4,73 | 1,85 | −0,33 |
| 5107925 Sorriso | 0,385 | −0,018 | 3,63 | 2,39 | −0,21 |
| 5107909 Sinop | 0,387 | −0,064 | 3,64 | 2,38 | −0,47 |
| 5105259 | 0,370 | −0,023 | 3,94 | 2,37 | −0,21 |
| 5102637 | 0,345 | +0,022 | 4,01 | 2,30 | +0,08 |

O erro de ET0 (5–11% da média diária) vem só da diferença de radiação; não mede erro da ET0 contra lisímetro.
A meteorologia POWER do job ao vivo (dias recentes) não foi comparada nesta rodada.

### 2.4 O que foi visto de 2025 e se influenciou decisões

| Análise com dados de 2025 | Conteúdo | Influenciou alguma decisão do modelo? |
|---|---|---|
| Comparação IMERG/INMET 2025 (§2.2) | só chuva, sem rendimento | Não. As fontes de treino e da avaliação (POWER: IMERG Final + CERES) já estavam fixadas no protocolo de `3774ce1`; nenhuma correção de chuva, troca de fonte ou ajuste foi feito a partir dela. Ela descreve a diferença entre as fontes do serviço em execução (IMERG Late) e as de treino; **não valida o rendimento previsto** nem torna o serviço cientificamente validado para operação. "Modo operacional" no job significa apenas "fontes disponíveis no dia" |
| Prefetch do clima POWER 2023–2025 (780 municípios) | entradas de feature | Não. Nenhuma estatística calculada; usado só na etapa `predict` |
| Job ao vivo (2026-11-14) | leu PAM 2025 em memória como lag das decisões da safra 2027 e gravou estimativas | Não. Ver auditoria abaixo |
| Print de depuração | café arábica e café total MG, Uberlândia (3170206), PAM 2025 = **1.920 kg/ha** | Não |
| Rodada de margens (guia 11, 2026-11-13) | PAM 2016–2025 de soja e milho (total, 1ª e 2ª safras) de Uberlândia e Sorriso | Não |

### 2.5 Auditoria da exposição prévia à PAM 2025

Comando: `python -m data.ingest.audit_pam2025_v8` → `data/processed/training_v8/pam2025_exposure.json`. Varredura de `data/raw`,
`data/cache`, `data/processed`, `dist` e `logs` (payloads SIDRA com período 2025 e tabelas com rendimento 2025), dos snapshots
ao vivo e das entradas do v8.

| Exposição | Arquivos / processo | Culturas | Municípios | Chegou ao v8? |
|---|---|---|---|---|
| Rodada de margens | `data/raw/margins/{pam,pam839,pam_history}.json`, `data/processed/margins/pam_history.csv` (`data/ingest/margins.py`, que usa só 2024) | soja, milho total, 1ª e 2ª safras | 3170206 Uberlândia, 5107925 Sorriso | Não: nenhum código de coorte, matriz, seleção ou avaliação lê esses arquivos |
| Job ao vivo | `daily_job` → `LiveProvider.history` → `fetch_pam_history`, valores só em memória; estimativas gravadas em `dist/snapshots_live/2026-11-14/yield/` | feijão 1ª (167 estimativas, referência), milho 1ª (437, referência), conilon (53, referência), arroz (47, HGB), soja (78, Ridge/MLP) | os da cobertura do job | Não: o `predict` 2025 lê só arquivos da allowlist (PAM ≤2024) e nunca lê `dist/` nem o provedor ao vivo |
| Depuração | saída de terminal desta sessão | café arábica, café total MG | 3170206 (1.920 kg/ha) | Não |

Nas estimativas de referência do job ao vivo (feijão 1ª, milho 1ª, conilon), o valor estimado pode ser o próprio PAM 2025 ou uma
média que o contém. Essas culturas/escopos não fazem parte da avaliação v8 (exceto conilon, cujo `predict` 2025 não lê snapshots).
Separação garantida em código: allowlist de entradas com sha256 gravada no congelamento, verificação antes do `predict`, rejeição
de qualquer arquivo de entrada com rendimento ≥2025 (testes em `tests/test_evaluate_2025_v8.py`), histórico de lags ≤2024,
treino ≤2023, desenvolvimento ≤2021.

**Classificação resultante:** avaliação 2025 **retrospectiva, com exposição prévia documentada** (um valor de café de um município
visto em depuração; valores de soja e milho de Uberlândia e Sorriso presentes em arquivos de margens; snapshots ao vivo com
estimativas que usam PAM 2025), **sem influência conhecida na seleção**. Não é afirmada independência plena: os responsáveis já
tiveram acesso a esses valores. Se o modelo passar, a confirmação prospectiva definida por datas (§7.3) continua necessária
antes de promoção operacional.

## 3. Soja MG/MT

Comando: `python -m data.ingest.train_v8 --scopes soy_mgmt` (RTX 4070 Ti) e `python -m data.ingest.evaluate_v8 --scopes soy_mgmt`.
Saídas: `data/processed/training_v8/soy_mgmt/{predictions.parquet,selection.csv,inner_top15.csv,metrics.csv,metrics_by_cutoff.csv,decision.json}`.

Resultado final após o retreino com a imputação por UF (§6.1; segundo run, 812 s).

Fórmula usada em todo o relatório: **ganho relativo = (MAE_referência − MAE_modelo) / MAE_referência**; positivo = melhora,
negativo = piora. Δ = MAE_modelo − MAE_referência (kg/ha; negativo = melhora), IC95 por bootstrap de municípios estratificado por UF.

Escolhas da seleção aninhada (por fold):

| Pool | 2019 | 2020 | 2021 |
|---|---|---|---|
| nacional | ref_lag_mean + F1 + MLP(64,32), shrink 1 (antes: history + F1 + MLP(32,16), shrink 0,75) | history + F1 + MLP(32,16), shrink 1 | history + F3 sem lat/lon + MLP(64,32), shrink 1 |
| MG+MT | history + F3 sem lat/lon + MLP(32,16) | history + F3 sem lat/lon + MLP(64,32) | idem |

Causa rastreada da única mudança (pool nacional, fold 2019): no pool nacional há linhas de **treino** sem lag (anos iniciais);
a imputação por UF muda a base `ref_lag_mean` dessas linhas e, portanto, os resíduos e scores internos dos candidatos com essa base
(o melhor passou de >237,1 para 234,9). Os candidatos com base `history`, que não usam o preenchimento, repetiram o score interno
anterior até a 6ª casa (237,085931), assim como todas as escolhas e scores do pool MG+MT (253,5 / 327,4 / 275,0), e o v8 MG+MT
repetiu MAE 175,0 — evidência de determinismo (mesma ordem de dados, sementes e algoritmos determinísticos em CUDA).
Correção de uma afirmação anterior: a imputação não afeta só linhas externas de milho e café; afeta também linhas de treino e
de ajuste interno dos pools nacionais (contagem em §6.1).

Todas as MLPs escolhidas pararam com 1–5 épocas (correção pequena e suave sobre a base).

MAE por safra (municípios de treino, MG/MT 50/50, folds 2019–2021, 273 safras, mesmas chaves para todos):

| Candidato | MAE kg/ha | Viés | MG | MT | Fora do treino (93 safras) |
|---|---|---|---|---|---|
| v7 MLP regional (fixo, guia 30) | 174,1 | +0,9 | 200,8 | 147,5 | 207,1 |
| **v8 pool MG+MT** | **175,0** | −74,4 | 204,5 | 145,6 | 196,7 |
| v8 pool nacional | 199,6 | +111,9 | 239,0 | 160,1 | 232,7 |
| history (referência simples mais forte) | 209,7 | +161,9 | 224,5 | 194,9 | 244,9 |
| ref_lag_mean (média Y−2..Y−4) | 241,0 | −187,3 | 257,0 | 225,0 | 252,6 |
| v7 Ridge site (fixo) | 243,9 | — | — | — | — |

Contra cada referência (ganho relativo; Δ kg/ha [IC95]):

| Candidato | history (209,7) | ref_lag_mean (241,0) | v7 MLP regional (174,1) | v7 Ridge site (243,9) |
|---|---|---|---|---|
| v8 pool MG+MT (175,0) | +16,5%; −34,7 [−59,2; −9,0] | +27,4%; −66,0 [−92,5; −44,8] | −0,5%; +0,9 [−14,1; +15,3] | +28,2%; −68,9 [−94,4; −42,5] |
| v8 pool nacional (199,6) | +4,8%; −10,1 [−19,8; −0,9] | +17,2%; −41,5 [−77,8; −8,0] | −14,6%; +25,4 [+9,1; +40,1] | +18,2%; −44,3 [−55,3; −33,3] |

Decisão (critérios registrados, referência = history, a referência **simples** mais forte):
- **v8 pool MG+MT: promovível no desenvolvimento** — +16,5%, IC abaixo de zero, MG +8,9% e MT +25,3% de ganho; fora do treino
  Δ −48,2 [−90,5; −3,1]; 5 sementes 170–180 kg/ha. Diferença contra o v7 MLP regional não demonstrada (Δ +0,9 [−14,1; +15,3]).
- v8 pool nacional: experimental — +4,8% (abaixo de 5%) e MG piora 6,5% (critério: nenhuma UF >5% pior).

Duas comparações distintas, registradas separadamente:
1. **v8 MG+MT × referência simples (history):** vantagem de 34,7 kg/ha no desenvolvimento, IC abaixo de zero — é o que o critério de
   promoção mede.
2. **v8 × v7 (MLP regional):** 175,0 × 174,1 kg/ha, Δ +0,9 [−14,1; +15,3] — **sem diferença demonstrada**. A v8 não é superior à v7.
   Como a v7 regional já falhou no teste 2022–2024 com desempenho de desenvolvimento igual, a vantagem contra history no
   desenvolvimento não basta; a avaliação 2025 (§7) compara as duas versões e history nas mesmas chaves.
- Por corte, o v8 MG+MT fica estável (173–178 kg/ha de 30 a 105 dias); o ganho sobre history já existe no corte de 30 dias.
- Por ano: 170 / 178 / 178 (v8 MG+MT) contra 257 / 159 / 213 (history): o v8 reduz a dispersão entre anos, mas perde para history em 2020.

Ressalva crítica: o v7 MLP regional teve desempenho de desenvolvimento equivalente (174,1) e **não** se confirmou no teste 2022–2024
(252,7 contra 247,7 da referência). A proximidade entre v8 MG+MT e v7 regional no desenvolvimento não dá motivo para esperar resultado
diferente; só a avaliação independente pode decidir. O viés negativo do v8 MG+MT (−74 kg/ha) também deve ser observado.

## 4. Milho safrinha

Comando: `python -m data.ingest.train_v8 --scopes maize2_mgmt,maize2_national` (retreino: 539 s + 368 s) e `python -m data.ingest.evaluate_v8`.
Todos os candidatos e referências avaliados nas mesmas chaves (2.196 linhas MG/MT; checagem `key_coverage` no `decision.json`).

Referências simples previstas no protocolo para culturas anuais: `ref_lag_mean` (média publicada Y−2..Y−4) e `history` (Ridge de
tendência + lags). Referências fixas adicionais: `v7_site_ridge` (configuração campeã v7).

### 4.1 Milho safrinha MG/MT (MG e MT com 50% cada)

Cobertura de treino-validação: 59 municípios usados no treino (MG 6, MT 53) × 3 safras = 177 safras; 22 municípios fora do treino (66 safras).
Rendimento médio MG: 7.770 / 7.062 / 4.076 kg/ha em 2019 / 2020 / 2021 (quebra de 2021); MT: 6.168 / 6.199 / 5.323.

Números após o retreino com a imputação por UF (539 s; ganho relativo = (MAE_ref − MAE_modelo)/MAE_ref, negativo = piora;
Δ = MAE_modelo − MAE_ref):

| Candidato | MAE kg/ha | Viés | MG | MT | Fora do treino |
|---|---|---|---|---|---|
| v7 Ridge site (fixo) | **1.544,0** | −310 | 2.249 | 839 | 1.359,9 |
| history | 1.688,8 | −398 | 2.538 | 839 | 1.501,4 |
| ref_lag_mean | 1.884,6 | −545 | 2.823 | 946 | 1.693,6 |
| v8 pool nacional | 1.937,2 | −407 | 2.877 | 998 | 1.821,5 |
| v8 pool MG+MT | 2.013,5 | −402 | 2.984 | 1.043 | 1.920,1 |

| Candidato | vs history | vs ref_lag_mean | vs v7 Ridge site |
|---|---|---|---|
| v8 pool nacional | −14,7%; Δ +248,4 [+205,6; +291,4] | −2,8%; Δ +52,6 [+3,1; +103,7] | −25,5%; Δ +393,2 [+357,8; +432,5] |
| v8 pool MG+MT | −19,2%; Δ +324,8 [+275,4; +374,1] | −6,8%; Δ +129,0 [+68,4; +183,7] | −30,4%; Δ +469,5 [+427,7; +512,5] |

O que eram os "−16% e −19%" do primeiro run: o ganho relativo contra history do v8 treinado no pool nacional (−16,1%) e no pool
só MG+MT (−19,0%), ambos avaliados no escopo MG/MT; após o retreino, −14,7% e −19,2%. Negativo = piora.

**Resultado negativo:** os dois candidatos v8 ficam piores que todas as referências simples e que o Ridge v7; MG piora 13,3–17,6% e
MT 18,9–24,3% contra history. Status: experimental. MAE por linha (sem o peso 50/50) é menor (~1.000 kg/ha) porque MG tem só 6
municípios no treino e pesa metade da métrica.

Leitura: a seleção interna escolheu quase sempre a base `ref_lag_mean` + MLP; a validação interna (2015–2016 a 2017–2018) não
antecipou a quebra de 2021 nem a queda de MG; com só 6 municípios MG no treino, o peso de 50% de MG torna a métrica instável.
O ganho do Ridge v7 sobre history aparece nos cortes finais (1.420 kg/ha aos 105 dias contra 1.689).

### 4.2 Milho safrinha nacional (8 UFs)

287 municípios no treino (859 safras), 86 fora (258 safras).

| Candidato | MAE kg/ha | Viés | Fora do treino | v8: ganho relativo; Δ [IC95] |
|---|---|---|---|---|
| v7 Ridge site (fixo) | **1.146,7** | +91 | 1.151,9 | −5,2%; +60,1 [+36,5; +83,4] |
| history | 1.203,0 | +260 | 1.250,5 | −0,3%; +3,8 [−28,2; +34,6] |
| v8 pool nacional | 1.206,8 | −25 | 1.258,8 | — |
| ref_lag_mean | 1.216,9 | −46 | 1.271,8 | +0,8%; −10,1 [−22,7; +3,0] |

(`ref_lag_mean` mudou de 1.223,6 para 1.216,9 com a imputação por UF nas 12 linhas-alvo sem lag.)

**Resultado negativo:** diferença do v8 contra history e ref_lag_mean não demonstrada (IC cruzam zero) e é pior que o Ridge v7. Por UF contra history:
piora em MG (+12,9%), MT (+13,7%), GO (+8,1%), DF (+7,4%), SP (+5,5%); melhora em BA (−14,4%) e MS (−11,7%). Status: experimental.

Experimento registrado de irrigação (Censo 2017): ver §6.

## 5. Café

Comando: `python -m data.ingest.train_v8 --scopes coffee_arabica,coffee_total_mg,coffee_conilon` (277 s + 541 s + ~150 s) e
`evaluate_v8`. Pool = escopo (uma UF). Cortes = marcos pré-florada, pós-florada e enchimento de grão. Todos os candidatos nas mesmas
chaves. Referências simples do protocolo: `ref_last_available`, `ref_same_phase`, `ref_biennial_mean`, `ref_cycle_mean`, `history`;
referência fixa: campeão v7.

Falha técnica corrigida antes de qualquer score de café: no ajuste interno do fold 2019 (safras 2012–2014) a Ridge de histórico não
tinha nenhuma feature variável. Esse corte passou a prever a média do corte nas linhas de ajuste daquele fold
(`train_v8.History`, teste `test_history_falls_back_to_the_cutoff_mean_without_usable_features`). Uso: só na seleção interna do
fold 2019 — arábica, corte 92 (251 linhas de ajuste, 500 de validação interna); conilon, corte 61 (52 e 104). Nenhuma previsão
externa usou esse fallback.

Redução relativa do erro = (MAE referência − MAE candidato) / MAE referência; **negativo = piora**.

| Escopo | Safras (municípios) | Melhor referência simples | MAE ref. | MAE v8 | Redução vs melhor ref. [Δ kg/ha, IC95] | Campeão v7 (MAE) | Status |
|---|---|---|---|---|---|---|---|
| Arábica MG | 753 (251) | history | 281,8 | 326,1 | −15,7% [+44,4; +33,7…+55,0] | Site HGB (262,7) | experimental |
| Café total MG | 768 (256) | history | 301,7 | 332,0 | −10,1% [+30,3; +18,6…+42,7] | Hydro HGB (292,3) | experimental |
| Conilon ES | 156 (52) | ref_last_available | 615,6 | 971,1 | −57,7% [+355,5; +291,1…+424,7] | — | experimental |

Contra cada referência (redução relativa; IC95 do Δ em kg/ha):
- Arábica: history −15,7% [+33,7; +55,0]; ref_biennial_mean −7,4% [+4,2; +40,2]; ref_same_phase −1,6% [−13,5; +24,4];
  ref_cycle_mean +1,7% [−23,1; +12,7]; ref_last_available +3,1% [−29,1; +8,9]; v7 −24,1% [+50,7; +75,6].
- Total MG: history −10,1% [+18,6; +42,7]; ref_biennial_mean −8,1% [+9,2; +40,7]; ref_same_phase −3,0% [−8,5; +27,9];
  ref_cycle_mean −0,5% [−14,1; +17,4]; ref_last_available +1,0% [−21,9; +14,8]; v7 −13,6% [+28,2; +50,7].
- Conilon: piora de 36–58% contra todas as referências (IC inteiramente acima de zero).

Por marco (MAE kg/ha, arábica): pré-florada history 265 / v7 257 / v8 329; pós-florada e enchimento history 290 / v7 265–267 / v8 320–329.
Viés v8: arábica +152, total MG −45, conilon −891 kg/ha (conilon subestima fortemente: tendência de alta não captada).

Desempenho por histórico disponível (`python -m data.ingest.lag_breakdown_v8` → `lag_availability_breakdown.csv`, MAE por linha):
nos alvos 2019–2021 de café só há 4 linhas sem nenhum lag (café total MG; history 297, v7 282, v8 410, referências 399 kg/ha) e
5 com 1–2 lags; todas as demais têm ≥3 lags. Imputação de referência ausente: ver §6.1.

Tabela completa nas mesmas chaves e cortes (desenvolvimento 2019–2021, municípios de treino; MAE kg/ha por safra; v7 = configuração
v7 reajustada em cada fold, como as demais):

| Escopo | Candidato | Pré-florada | Pós-florada | Enchimento | Geral | Viés |
|---|---|---|---|---|---|---|
| Arábica MG (753 safras) | v7 site HGB | 257 | 267 | 265 | **262,7** | +12 |
| | history | 265 | 290 | 290 | 281,8 | +56 |
| | ref_biennial_mean | 341 | 285 | 285 | 303,8 | −85 |
| | ref_same_phase | 366 | 298 | 298 | 320,8 | −42 |
| | v8 | 329 | 329 | 320 | 326,1 | +152 |
| | ref_cycle_mean | 348 | 324 | 324 | 331,9 | −8 |
| | ref_last_available | 417 | 296 | 296 | 336,5 | +4 |
| Café total MG (768) | v7 hydro HGB | 303 | 292 | 282 | **292,3** | +76 |
| | history | 324 | 290 | 290 | 301,7 | +95 |
| | ref_biennial_mean | 348 | 287 | 287 | 307,1 | −88 |
| | ref_same_phase | 370 | 299 | 299 | 322,5 | −43 |
| | ref_cycle_mean | 348 | 322 | 322 | 330,3 | −11 |
| | v8 | 355 | 320 | 320 | 332,0 | −45 |
| | ref_last_available | 412 | 297 | 297 | 335,4 | +2 |
| Conilon ES (156) | ref_last_available | 773 | 537 | 537 | **615,6** | −432 |
| | ref_cycle_mean | 829 | 594 | 594 | 672,0 | −602 |
| | ref_same_phase | 946 | 537 | 537 | 673,2 | −516 |
| | history | 774 | 675 | 675 | 708,2 | −574 |
| | ref_biennial_mean | 835 | 650 | 650 | 711,8 | −686 |
| | v8 | 1.010 | 1.009 | 895 | 971,1 | −891 |

(Conilon não tem campeão v7 de aprendizado: a escolha v7 já era `ref_last_available`.)

**Resultado negativo do v8 nos três escopos de café.** A seleção interna escolheu quase sempre o modelo direto (base `mean`),
porque nos anos de ajuste interno (2012–2016) os lags de café por espécie quase não existem; isso não se transfere para 2019–2021.
O experimento registrado "modelo direto × correção sobre ref_cycle_mean" fica respondido: o direto não difere de forma demonstrada de ref_cycle_mean em MG
e perde em conilon.

Versão em operação × melhor resultado confirmado em teste (não se conclui superioridade do v7 só porque o v8 perdeu):

| Escopo | Em operação no job ao vivo | Melhor no desenvolvimento 2019–2021 | Teste v7 2022–2024 (já aberto) | Melhor resultado confirmado em teste |
|---|---|---|---|---|
| Arábica MG | v7 site HGB | v7 site HGB (262,7) | v7 453,6 × history 426,0: **pior** (Δ +27,6 [+21,2; +34,6]) | nenhum modelo de aprendizado; history é a referência mais sólida |
| Café total MG | v7 hydro HGB | v7 hydro HGB (292,3) | v7 360,8 × history 349,6: não confirmado (Δ +11,2 [−2,6; +25,2]) | nenhum confirmado; diferença contra history não demonstrada |
| Conilon ES | `ref_last_available` | `ref_last_available` (615,6) | 398,1 × history 888,3: confirmado (Δ −490 [−581; −401]) | `ref_last_available` |

Os modelos v7 de café seguem em operação por decisão anterior de catálogo, não por superioridade confirmada em teste; a v8 não
altera essa decisão, que deve ser revista separadamente (arábica: candidato a voltar para history).

## 6. Imputação, experimentos registrados

### 6.1 Imputação de referência ausente por UF

Regra (`train_v8.reference_predictor`, aplicada igualmente a candidatos com base de referência e às referências simples): a linha sem
a referência publicada recebe a média de rendimento da **mesma UF** nas linhas de treino **daquele fold** — linhas de ajuste interno
durante a seleção, linhas de treino do fold na previsão externa; UF ausente do treino → média de todas as linhas de treino do fold.
Nunca usa linhas do alvo. Antes era a média de todo o pool (nacional quando o pool é nacional). Teste:
`test_missing_reference_uses_the_same_state_mean_of_the_fold_training`.

Casos afetados (`python -m data.ingest.imputation_audit_v8` → `imputation_audit.csv`; somas sobre os 3 folds, linhas linha×corte):

| Escopo / pool | Seleção (ajuste + validação interna) | Externo (treino + alvo) | Alvos externos preenchidos | UF sem treino |
|---|---|---|---|---|
| Soja, pool nacional | 11.862 de 180.942 | 11.862 de 194.490 | 0 | 0 |
| Soja, pool MG+MT | 1.764 de 25.974 | 1.764 de 29.808 | 0 | 0 |
| Milho MG/MT, pool nacional | 10.350 de 72.342 | 10.350 de 78.894 | 0 | 0 |
| Milho MG/MT, pool MG+MT | 2.052 de 14.940 | 2.052 de 17.460 | 0 | 0 |
| Milho nacional | 10.350 de 72.342 | 10.362 de 84.138 | 12 | 0 |
| Arábica (ref_cycle_mean) | 761 de 9.024 | 762 de 14.058 | 0 | 0 |
| Café total MG (ref_cycle_mean) | 855 de 34.389 | 859 de 39.522 | 4 | 0 |
| Conilon (ref_cycle_mean) | 156 de 1.872 | 156 de 2.880 | 0 | 0 |

Recalculado sob a mesma regra: soja e milho (candidatos e referências, retreino completo); café não muda (pool de uma UF: a média da
UF é a média do pool). O experimento de irrigação foi refeito com a regra nova.

Comando: `python -m data.ingest.experiments_v8` (folds de desenvolvimento apenas; nenhum resultado altera o congelamento —
mudanças sugeridas ficam como hipótese para a confirmação prospectiva).

**Curva de aprendizado — exploratória (soja MG/MT).** Desenho: em cada fold externo, a configuração escolhida pelo pool nacional
naquele fold (após o retreino com a imputação por UF) é reajustada com subconjuntos do treino e pontuada nos **mesmos** alvos
(municípios de treino MG/MT do ano do fold, todos os 6 cortes), mesmas features e mesma ponderação 50/50 por UF; 3 sementes
(101–103) por linha. Janelas experimentadas: 4 em anos (5, 8, 11 e todos os anos) e 3 frações de municípios (25%, 50%, 100%,
amostra por hash); 7 linhas × 3 folds. MAE kg/ha:

| Treino | 2019 | 2020 | 2021 | Média |
|---|---|---|---|---|
| últimos 5 anos (~3.150 safras) | 222,0 | 342,3 | 183,7 | 249,3 |
| últimos 8 anos (~5.050) | 234,5 | 192,1 | 215,7 | 214,1 |
| últimos 11 anos (~6.930) | 212,3 | 133,9 | 171,8 | 172,7 |
| todos (16–18 anos, ~10.700) | 254,4 | 182,8 | 159,5 | 198,9 |
| 25% dos municípios (~2.940) | 185,8 | 161,4 | 206,4 | 184,5 |
| 50% dos municípios (~5.400) | 218,0 | 181,5 | 197,9 | 199,1 |

A linha "todos" difere dos 199,6 kg/ha do candidato v8 nacional (§3) só pelo número de sementes (3 × 5). As janelas foram
comparadas **diretamente nos folds externos**, sem seleção interna: o resultado é **exploratório**. Nenhuma janela foi escolhida;
a configuração congelada usa todos os anos. Testar "11 anos" exigiria escolhê-la na validação interna ou avaliá-la numa safra
ainda elegível (§7.3).

**Função de perda (mesma configuração MLP, soja MG/MT, 5 sementes):** MSE 195,3 contra smooth L1 199,6 kg/ha de MAE médio; nas 10%
safras de menor rendimento, 325,2 contra 333,4. Diferença pequena e de sinal variável por ano (2021: smooth L1 melhor). Mantida a
perda registrada (smooth L1); resultado exploratório.

**Irrigação (Censo Agro 2017) no milho safrinha — diagnóstico.** No protocolo registrado (`training_v8_protocol.json`) ela consta
apenas em `registered_experiments`; os conjuntos de candidatos (`feature_sets_annual`: F1, F2, F3, F3 sem lat/lon) não a incluem, e o
atributo só é elegível para decisões após 2019-10-25 (folds 2020–2021). Por isso **não participa da seleção** nem do congelamento
da avaliação 2025, e seu resultado não altera o candidato congelado.

Resultado (refeito com a imputação por UF, terminado após o congelamento; folds 2020–2021, mesmas chaves; seleção interna podia
escolher F4 = F3 + participação de pivô/irrigada na área de milho safrinha):

| Escopo | Seleção | v8 com F4 disponível | v8 sem F4 | history | Ridge v7 | Δ F4 − sem F4 [IC95] |
|---|---|---|---|---|---|---|
| Milho MG/MT (50/50) | 2020: F2 (HGB); 2021: F4 (MLP) | 1.807,7 | 1.776,7 | 1.575,5 | 1.483,0 | +31,0 [−4,6; +63,7] |
| Milho nacional | 2020: F2; 2021: F4 | 1.368,8 | 1.370,0 | 1.387,2 | 1.331,5 | −1,2 [−7,4; +4,8] |

Sem efeito demonstrado do atributo de irrigação; ambos continuam piores que o Ridge v7. Diagnóstico apenas; nada alterado no congelamento.

## 7. Avaliação independente (safra 2025)

### 7.1 Disponibilidade temporal por corte (`python -m data.ingest.availability_v8` → `data/processed/training_v8/availability.json`)

Regra: `available_at ≤ decision_at` para cada entrada efetivamente lida, com `decision_at` = dia seguinte ao corte, por cultura,
UF e corte (69 combinações). Os anos de PAM verificados são os que os construtores de features leem (regra de lag de cada um),
não uma lista fixa.

| Entrada | Status da data | Fonte da data | Resultado |
|---|---|---|---|
| PAM (lags) 2016+ | **comprovada** | calendário IBGE 9117 | 0 violações; ex.: soja MT 30 dias decide 2024-11-04 com PAM 2023 (2024-09-12); café pré-florada 2024-09-01 com PAM ≤2022 |
| PAM antes de 2016 | estimada (31/out do ano seguinte) | regra conservadora | não usada nos lags de 2025 |
| IMERG Final (chuva) | **estimada** (~3,5 meses) | documentação GPM | **69/69 cortes violam** para o último dia da janela; fração da janela não publicada: soja e milho 0,99–1,00, café 0,16–0,24 |
| Radiação CERES (POWER) | **desconhecida** | sem registro de versão | não verificável |
| Meteorologia POWER | **desconhecida** | sem registro de versão | não verificável |

- PAM 2024 (2025-09-11) não aparece em nenhum corte de 2025 porque a data verificada é posterior a todas as decisões, não por regra
  fixa (teste `test_every_pam_lag_used_in_2025_is_available_at_its_decision`).
- **Classificação: avaliação retrospectiva** (hindcast com produtos finais), não reprodução em tempo real. O erro operacional real
  inclui a troca para IMERG Late/ERA5 (§2) e não é medido por esta avaliação.

### 7.2 Cronologia por etapa

| Etapa | Rendimentos usados como rótulo | Rendimentos usados como lag | Regra de disponibilidade |
|---|---|---|---|
| Desenvolvimento (seleção + folds 2019–2021) | ≤2021 | ≤2019 | lag só se publicado antes da decisão de cada corte (`cohort_v8.validate`) |
| Ajuste para a avaliação 2025 | ≤2023 (2022–2023 do teste já aberto, só ajuste) | ≤2021 nas linhas de treino | idem |
| Features da safra 2025 | nenhum (alvo sem rótulo) | histórico carregado até 2024; entram os anos que a regra de lag lê (anuais 2021–2023; café ≤2022 ou ≤2023 conforme o marco), todos com data comprovada ≤ decisão | verificado data a data (§7.1) |
| Pontuação 2025 | PAM 2025 (pasta selada, depois dos checkpoints) | — | — |

### 7.3 Confirmação prospectiva (definida por datas, não por ano fixo)

Unidade elegível: cultura × município × safra × corte cuja data de decisão seja **posterior ao commit do arquivo congelado**
(a previsão é emitida antes de o resultado existir). Nível mais fraco: safra cuja PAM ainda não foi publicada quando as previsões
são emitidas e hasheadas. As previsões são gravadas e hasheadas até a data de cada decisão, com as entradas disponíveis naquela data
(fontes operacionais), nunca recalculadas depois da divulgação da PAM, e pontuadas uma vez contra a primeira divulgação, com as
mesmas referências e critérios. As safras são escolhidas por essas datas no momento da emissão (registrado em
`rules.prospective_confirmation` do arquivo congelado).

### 7.4 Desenho congelado (antes de qualquer rótulo 2025)

Comandos, em ordem: `freeze` (commit do arquivo) → `predict` → `labels` → `score`
(`python -m data.ingest.evaluate_2025_v8 <etapa>`).

- `data/frozen/v8_frozen_choices.json`, por cultura × região: pool, candidato (base, conjunto de features e lista de colunas,
  aprendiz, hiperparâmetros, shrinkage, épocas, sementes), municípios cobertos, cortes, status e referência do desenvolvimento,
  referências comparadas; regras comuns: fontes (POWER IMERG Final + CERES; sem fontes operacionais, sem correção de chuva),
  pré-processamento (imputação mediana e padronização ajustadas só no treino; nenhuma estatística de 2025), exclusões (corte
  descartado se faltar qualquer dia de clima na janela; safra só pontuada com PAM 2025 consistente), limites de cobertura,
  tratamento fora da distribuição (sem truncar; contado e reportado), treino ≤2023 e regra de decisão; hashes do protocolo e do código.
- `predict` constrói as features 2025 **sem rótulo** (o alvo é rejeitado se tiver `yield_kg_ha`), reajusta as escolhas congeladas e
  todas as referências e grava um checkpoint por escopo com sha256 em `run_log.jsonl`. `labels` só roda quando todos os checkpoints
  existem. `score` confere os hashes das previsões e dos rótulos e grava `score_<escopo>.json` uma única vez (nunca sobrescreve).
- Retomada: clima e PAM ficam em cache imutável (`data/raw/v8_eval_2025`, gravação atômica, novas tentativas com backoff); uma
  falha técnica reinicia a etapa interrompida sem refazer as concluídas. O hash do arquivo congelado é registrado no início e
  conferido em cada etapa.

### 7.5 Escolhas congeladas (commit `d1bbae6`, 2026-11-14 15:52 −03, antes de qualquer rótulo 2025)

Reseleção com todos os dados de desenvolvimento (treino ≤2021, validação interna 2020–2021), pool de menor MAE de desenvolvimento:

| Escopo | Pool | Base | Features | Corretor | Shrink |
|---|---|---|---|---|---|
| Soja MG/MT | MG+MT | history | F3_full | HGB (400 it., 15 folhas) | 0,75 |
| Milho MG/MT | nacional | ref_lag_mean | F1_weather | MLP (128,64), 2 épocas, 5 sementes | 1,0 |
| Milho nacional | nacional | ref_lag_mean | F2_weather_hydro | MLP (128,64), 2 épocas, 5 sementes | 1,0 |
| Arábica MG | MG | ref_last_available | F2_weather_hydro | HGB | 0,5 |
| Café total MG | MG | ref_cycle_mean | F3_full | HGB | 1,0 |
| Conilon ES | ES | mean (direto) | F3_full | MLP (32,16), 84 épocas, 5 sementes | 1,0 |

A reseleção final pode diferir das escolhas por fold (ex.: soja HGB em vez de MLP): é a regra registrada, não um ajuste posterior.
O experimento de irrigação terminou depois do congelamento e não o alterou.

### 7.6 Execução

`predict` (18:52–18:54 UTC): 158–411 arquivos de entrada verificados por escopo (hash + ausência de rendimento ≥2025); previsões
gravadas e hasheadas no `run_log.jsonl`. `labels` (18:54–18:55 UTC): PAM 2025 baixada para `data/raw/v8_eval_2025` só depois dos
checkpoints. `score`: um arquivo `score_<escopo>.json` por escopo, gravado uma vez.

Fora da faixa de treino (contagem de linhas por feature, sem truncamento; `harvest_year` é sempre extrapolado, 2025 > 2023):
soja `yield_lag2to4_mean` 90/732, `elevation_m` 12; milho nacional `heat_days` 16, `t_min_mean_c` 5; arábica `rh_mean_pct` 29,
`prev_heat_days` 12; café total `rh_mean_pct` 12; conilon `yield_lag6` 21, `max_dry_spell` 14.

### 7.7 Resultados 2025 — retrospectivos, com exposição prévia documentada

MAE por safra nos municípios de treino; ganho = (MAE_ref − MAE_v8)/MAE_ref; Δ = MAE_v8 − MAE_ref [IC95, bootstrap de municípios].
"Regra congelada" = comparação com a referência primária fixada no desenvolvimento (≥5% de ganho, IC < 0, nenhuma UF >5% pior).

| Escopo (safras, cobertura) | v8 | history | ref_lag_mean / melhor ref. simples 2025 | v7 servido | v8 vs ref. primária | v8 vs melhor ref. simples 2025 | v8 vs v7 | Regra congelada |
|---|---|---|---|---|---|---|---|---|
| Soja MG/MT (91 + 31 fora; 122/122) | 198,5 | 353,6 | 298,1 (ref_lag_mean) | MLP regional 237,0; Ridge site 198,8 | history: +43,9%, −155,1 [−189,6; −116,9] | ref_lag_mean: +33,4%, −99,6 [−132,5; −66,2] | MLP reg.: +16,3%, −38,5 [−57,2; −18,9]; Ridge site: +0,2%, −0,3 [−14,1; +13,5] | passa |
| Milho MG/MT (59 + 22; 81/81) | 885,4 | 1.179,9 | 1.164,8 (ref_lag_mean) | Ridge site 1.082,8 | history: +25,0%, −294,5 [−454,7; −147,4] | ref_lag_mean: +24,0%, −279,4 [−534,6; −74,6] | +18,2%, −197,4 [−286,3; −107,3] | passa |
| Milho nacional (285 + 87; 372/375) | 1.367,3 | 1.430,3 | 1.489,3 (ref_lag_mean) | Ridge site 1.326,6 | history: +4,4%, −63,0 [−110,7; −13,2] | history (melhor): idem | −3,1%, +40,6 [+28,5; +53,0] | não passa (<5%; MG +62,6%, BA +17,3% piores) |
| Arábica MG (248 + 59; 307/311) | 223,5 | 277,1 | ref_cycle_mean 227,9 | site HGB 224,8 | history: +19,3%, −53,6 [−80,6; −27,2] | ref_cycle_mean: +1,9%, −4,4 [−18,7; +10,6] | +0,6%, −1,3 [−19,6; +17,2] | passa contra history; **sem ganho** contra a melhor referência de 2025 |
| Café total MG (253 + 61; 314/317) | 246,4 | **231,3** | history 231,3 | hydro HGB 250,4 | history: −6,5%, +15,1 [−2,1; +31,5] | idem | +1,6%, −4,1 [−18,1; +8,9] | não passa |
| Conilon ES (52 + 8; 60/60) | 358,4 | 474,9 | ref_cycle_mean 382,2 | ref_last_available 451,1 | ref_last_available: +20,6%, −92,7 [−159,9; −24,2] | ref_cycle_mean: +6,2%, −23,8 [−82,4; +36,4] | = ref. primária | passa contra a primária; **sem ganho** contra ref_cycle_mean |

Vieses 2025 (kg/ha): soja history −336, ref_lag_mean −294, v8 −97, Ridge v7 −35 (safra 2025 acima da tendência); milho MG/MT
v8 −1.103, history −1.516; conilon history +344, v8 −67.

Fora do treino (Δ v8 − ref. primária): soja −190,3 [−241,7; −130,6]; milho MG/MT −248,9 [−440,2; −10,1]; milho nacional −72,5
[−137,6; −5,5]; arábica −17,8 [−72,6; +37,2]; café total +33,4 [+1,5; +63,2]; conilon −70,9 [−245,1; +194,1] (8 safras).

Leitura (sem tratar um ano como prova):
- **Soja MG/MT:** v8 melhor que as referências simples e que a v7 MLP regional servida em MG/MT, mas fica próximo do Ridge site
  v7 (Δ −0,3 [−14,1; +13,5]). Em 2025 o ganho veio sobretudo do viés das referências numa safra acima da tendência.
- **Milho MG/MT:** v8 melhor que todas as referências e que o Ridge v7 em 2025, o **oposto** do desenvolvimento (v8 −14,7% a
  −19,2% contra history em 2019–2021). Um ano com resultado contrário ao desenvolvimento não sustenta superioridade.
- **Milho nacional:** v8 pior que o Ridge v7; ganho pequeno contra history não passa na regra.
- **Arábica:** passa contra history, a referência fixada, mas a diferença contra ref_cycle_mean e o HGB v7 em 2025 não foi demonstrada.
- **Café total MG:** v8 não supera history (history foi o melhor em 2025).
- **Conilon:** passa contra `ref_last_available`, mas a diferença contra `ref_cycle_mean` não foi demonstrada; no desenvolvimento foi muito pior (−57,7%) e há
  só 52 safras.
- Um ano, choques climáticos comuns: o IC por município subestima a incerteza entre anos.

## 8. Artefatos exportados e validação em CPU

Comando: `python -m data.ingest.export_v8` → `dist/gaiapus-models-v8/` e `dist/release/gaiapus-models-v8-experimental.zip`
(sha256 `2190ffbb3206b0cbb04284c81aa38170082b72cd32d31df43ba04e144eac8fa5`, em `.sha256.json`). Cópia dos artefatos em
`data/models/research_v8/`. Treino: as mesmas escolhas congeladas, safras ≤2023 (o ajuste da avaliação 2025).

Conteúdo: `models/{soy_mgmt,coffee_arabica,coffee_total_mg}.json` (HGB exportado como árvores JSON),
`models/{maize2_mgmt,maize2_national,coffee_conilon}.json + .pt` (MLP: pré-processamento JSON + state_dict, 5 sementes),
`yield_model.py` (carregador CPU, sem pickles), `serve_v8.py` (status `ready`, `invalid_scope`, `municipality_not_covered`,
`invalid_cutoff`, `inputs_incomplete`, avisos de faixa), `cases.json` (60 casos reais: Sorriso, Uberlândia, Patrocínio,
Três Pontas, Linhares, São Mateus), `validate_cpu.py`, `manifest.json` (escolhas, paridade, hashes), `README.md`.

Paridade CPU × CUDA (linhas da última safra de treino de cada escopo): HGB 0 kg/ha; MLP 1,3e-4 a 2,3e-4 kg/ha (tolerância 0,05).

Validação isolada (ambiente novo `uv venv`, Python 3.11.15, só numpy 2.4.6 e torch 2.15.0+cpu, `CUDA_VISIBLE_DEVICES=`,
`python -I`, pacote descompactado fora do projeto): `passed: true` — 0 divergências de hash; 60 casos com diferença máxima
0,0498 kg/ha (arredondamento a 0,1); entradas inválidas (escopo, município, corte, ausente, fora da faixa) com o status correto;
ordem das features irrelevante nos 6 escopos; valor ausente = mediana de treino nos 6; fallback de referência por UF nos 4 escopos
com base de referência. Varredura de credenciais no conteúdo descompactado: nada encontrado.

**O pacote não é operacionalmente validado** e não substitui o catálogo v7 do job ao vivo (nenhum arquivo de `data/serving`
usado pelo job foi trocado; o carregador ganhou o formato `gaiapus-v8-v1` sem alterar os existentes).

## 9. Limitações

- 2019–2021 foram inspecionados em rodadas anteriores; a validação de desenvolvimento não é virgem e o bootstrap após a seleção não
  remove viés de seleção.
- 2022–2024 não é mais teste independente; nesta rodada esses anos entram só como treino da avaliação 2025.
- A avaliação 2025 é retrospectiva (clima final, não o disponível na data); um ano só, com choques climáticos comuns a todos os
  municípios, então o IC por município subestima a incerteza entre anos.
- Calendários de semeadura por UF, Kc/p/CAD do balanço hídrico e o ponto representativo do município (não a lavoura) são hipóteses.
- Rendimento municipal PAM não valida rendimento de talhão nem efeito causal de manejo.
- Milho safrinha MG tem só 6 municípios no treino; o peso de 50% de MG deixa a métrica MG/MT instável.
- O atributo de irrigação do Censo 2017 descreve equipamento em 2017, não a irrigação de cada safra.
- O erro do serviço ao vivo (IMERG Late + ERA5) não é medido pelas avaliações; só a troca de fontes foi quantificada (§2).
