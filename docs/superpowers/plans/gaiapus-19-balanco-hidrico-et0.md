# Gaiapus — ET0, balanço hídrico e seca por fase

Entrega de 14/11/2026. Motor físico **experimental**, executado sobre dados disponíveis de oito municípios de MG/MT. Não é rede neural, previsão meteorológica, recomendação de irrigação nem previsão validada de produtividade. O que foi validado nesta entrega é a implementação numérica e sua coerência física. Ainda faltam observações de solo e lavoura para validação agronômica.

## 1. O que está implementado

- [Motor Python](../../../data/models/water_balance.py): ET0 diária, curva Kc, reservatório de água disponível, estresse e cenários de redução de chuva.
- [Executor reproduzível](../../../data/ingest/water_balance_run.py): lê entradas locais com hashes conferidos contra o manifesto v1, verifica unidades, recusa datas duplicadas/incompletas e gera os resultados abaixo.
- [Testes](../../../tests/test_water_balance.py): exemplos publicados, limites, chuva/irrigação/escoamento, fases e comparação entre cenários.

Municípios: Uberlândia, Uberaba, Unaí e Paracatu (MG); Sorriso, Sinop, Lucas do Rio Verde e Campo Novo do Parecis (MT). As culturas são soja e milho de segunda safra (`soy`, `maize2`). Não representa todos os municípios desses estados.

```powershell
python -m unittest discover -s tests
python -m data.ingest.water_balance_run
```

Executar na raiz do projeto. Usa apenas a biblioteca padrão Python, sem instalação adicional ou novo download. Em ambiente gerenciado com uv, o mesmo módulo pode ser executado com `uv run python -m data.ingest.water_balance_run`. O executor sobrescreve somente as saídas desta versão; não altera os dados v1/v2 nem os modelos treinados. Antes de mudar parâmetros para outro experimento, copie/versione os resultados e gere novo manifesto.

## 2. De onde vêm as entradas

Entrada: `data/processed/training_v1/weather_daily.csv`, 70.128 registros (8 × 8.766 dias), de 01/01/2001 a 31/12/2024. Originais: `data/raw/training_v1/power_<IBGE>.json`. Alguns arquivos têm conteúdo gzip apesar da extensão JSON; o leitor reconhece a assinatura. O CSV possui BOM UTF-8, tratado na importação.

| Variável NASA POWER | Campo local | Unidade e uso |
|---|---|---|
| IMERG_PRECTOT | rain_mm | mm/dia; entrada de chuva no reservatório |
| T2M_MIN / T2M_MAX | t_min_c / t_max_c | °C; pressão de saturação e temperatura média |
| ALLSKY_SFC_SW_DWN | radiation_mj_m2 | MJ/m²/dia; balanço radiativo |
| WS2M | ws2m_m_s | m/s a 2 m; componente aerodinâmico |
| T2MDEW | dewpoint_c | °C; pressão real de vapor |
| PS | pressure_kpa | kPa; constante psicrométrica |

Latitudes e elevações são as declaradas na geometria do JSON POWER; os valores exatos ficam em `provenance.json`. A umidade relativa média está disponível na base, mas não é necessária quando se usa ponto de orvalho. O produto POWER é acessado pela [API temporal diária](https://power.larc.nasa.gov/docs/services/api/temporal/daily/); esta execução reaproveita os arquivos previamente coletados, sem consultar uma API em tempo real.

**Escala espacial:** ponto representativo municipal, não talhão nem média agrícola. A base ponderada v2 possui seis variáveis e não inclui pressão e ponto de orvalho ponderados. Misturar essas duas variáveis pontuais com as demais ponderadas produziria uma combinação espacial não avaliada; por isso esta entrega usa o conjunto pontual completo. Também não usa o ponto alternativo de solo de Campo Novo como localização meteorológica. O estudo INMET v2 continua válido nos seus próprios limites, mas não constitui validação local da ET0 ou do armazenamento calculado aqui.

**Escala temporal:** dia UTC, preservando a convenção da fonte. Não converter datas para dia civil brasileiro sem reprocessar as fontes subdiárias. Dados faltantes não viram zero; o executor exige todos os dias e valores finitos. Nenhum preenchimento ou correção de viés de chuva foi aplicado.

## 3. Equações e parâmetros

A ET0 usa Penman–Monteith diário, com fluxo de calor no solo G=0, radiação solar medida pelo produto, albedo de referência 0,23 e vapor calculado pelo ponto de orvalho. A implementação reproduz o exemplo 18 da [FAO-56, capítulo 4](https://www.fao.org/4/x0490e/x0490e08.htm): aproximadamente 3,9 mm/dia. Longitude não entra no cálculo diário de radiação extraterrestre; entram latitude e dia do ano.

```text
ET0 = [0,408 Δ (Rn − G) + γ 900/(T+273) u2 (es−ea)]
      / [Δ + γ (1 + 0,34 u2)]
ETc = Kc × ET0
```

Para estabilidade radiativa, Rs/Rso é limitado a [0,3; 1]. Valores originais e indicador de ajuste são exportados, sem ocultar a transformação. VPD e ET0 negativos seriam limitados a zero e sinalizados. Nesta rodada houve 1.045 ajustes de Rs/Rso; nenhum de VPD ou ET0. Esses dias continuam disponíveis, com qualidade sinalizada; comparar com estações antes de tratá-los como referência agronômica.

Os Kc de referência e a interpolação inicial → desenvolvimento → meio → final seguem a estrutura da [FAO-56, capítulo 6](https://www.fao.org/4/x0490e/x0490e0b.htm). Não foram ajustados a cultivar, cobertura, vento local, frequência de molhamento ou umidade mínima. Milho usa Kc final 0,35, hipótese de grão seco no campo.

| Cultura | Dias das quatro fases | Kc inicial / meio / final | p base | CAD AD2 / AD4 / AD6 (mm) |
|---|---|---|---|---|
| Soja, grupo II | 15 / 35 / 45 / 20 = 115 | 0,40 / 1,15 / 0,50 | 0,50 | 32 / 55 / 95 |
| Milho 2ª, grupo II | 20 / 40 / 40 / 20 = 120 | 0,30 / 1,20 / 0,35 | 0,55 | 31,8 / 54,6 / 95,4 |

Fases no código: `establishment`, `vegetative`, `reproductive`, `maturation`. Durações e CAD vêm das referências ZARC já catalogadas no [documento 12](gaiapus-12-zarc-e-seca-por-fase.md), incluindo as tabelas de referência para soja da Embrapa. Associar essas durações às fases da curva Kc é uma **hipótese de implementação**, não equivalência fenológica comprovada. O dia de plantio tem `days_after_sowing=0`; cada fase inclui exatamente o número de dias da tabela. Desenvolvimento e maturação interpolam Kc diariamente até o valor final do segmento.

O reservatório representa água disponível acima do ponto de murcha, e não umidade volumétrica. Aplica p ajustado por demanda, limitado a [0,1; 0,8]; a fração de estresse reduz a ET. Essas relações e a aproximação com Kc único estão na [FAO-56, capítulo 8](https://www.fao.org/4/x0490e/x0490e0e.htm). Separar transpiração e evaporação com Kcb/Ke será uma evolução necessária para maior fidelidade.

```text
p = clip(p_base + 0,04 × (5 − ETc), 0,1, 0,8)
RAW = p × CAD
entrada = armazenamento_anterior + chuva − escoamento + irrigação
drenagem = max(0, entrada − CAD)
armazenamento_antes_ET = min(CAD, entrada)
Ks = min(1, armazenamento_antes_ET / [CAD × (1−p)])
ETa = min(armazenamento_antes_ET, Ks × ETc)
armazenamento_final = armazenamento_antes_ET − ETa
déficit_ET = ETc − ETa
```

CAD fixa durante todo o ciclo; referência de profundidade efetiva 0,6 m do catálogo ZARC, sem crescimento radicular. Isso não reproduz toda a zona radicular de milho adulto. Não inferimos CAD automaticamente das estimativas SoilGrids. A rodada assume irrigação zero, escoamento zero, drenagem instantânea e chuva antes da extração diária. O motor permite irrigação diária e fração de escoamento, mas seus valores precisam de dados locais. Não há ascensão capilar, lençol freático ou redistribuição entre camadas.

## 4. Desenho dos experimentos

Anos de colheita hipotética: **2002–2024**. Início da soja: 05/11 do ano anterior em MG e 05/10 em MT. Milho: 05/01 do próprio ano. São datas fixas para comparação, não plantios observados, calendários autorizados ou validação das janelas ZARC históricas. A versão 2026/27 do ZARC não é autorização retroativa.

**Os dois cultivos são simulados independentemente.** Em particular, 05/01 pode ocorrer antes da colheita da soja desses exemplos. Não oferecer essa combinação como sucessão viável: o backend deverá exigir a colheita anterior e reavaliar a janela de milho.

Cada combinação recebe nove cenários: histórico intacto e redução de 50% ou 100% da chuva em cada uma das quatro fases. O efeito sobre armazenamento continua nas fases seguintes. Temperatura, radiação, vento e vapor permanecem iguais: é uma análise contrafactual de chuva, não uma previsão meteorológica fisicamente acoplada de seca/onda de calor.

No histórico usa-se AD4 e água inicial de 70% da CAD. Em 2024 há sensibilidade com AD2/AD4/AD6 × 30%/70%/100% de água inicial. Não confundir esses valores escolhidos com medições. Totais: 4.464 cenários; 496 históricos e 3.968 contrafactuais; 17.856 resumos de fase. As 152.280 linhas diárias exportadas cobrem 2024; os demais anos têm resumos e são reproduzíveis pelo executor.

## 5. Resultados calculados — exemplo 2024

Valores abaixo são **déficit acumulado ETc−ETa no ciclo inteiro, em mm**, com AD4 e 70% de água inicial. As colunas de seca alteram somente a chuva da fase reprodutiva. Não são perdas em toneladas, porcentagem de colheita ou lâmina de irrigação recomendada.

| Município | Cultura | Histórico | Chuva −50% | Chuva −100% |
|---|---|---:|---:|---:|
| Uberlândia | Soja | 70,09 | 131,28 | 305,12 |
| Uberlândia | Milho 2ª | 52,26 | 83,05 | 251,10 |
| Uberaba | Soja | 39,64 | 97,79 | 260,42 |
| Uberaba | Milho 2ª | 54,15 | 99,35 | 231,54 |
| Unaí | Soja | 70,90 | 107,87 | 263,14 |
| Unaí | Milho 2ª | 103,53 | 149,82 | 275,53 |
| Paracatu | Soja | 62,90 | 83,95 | 237,89 |
| Paracatu | Milho 2ª | 119,85 | 148,79 | 264,08 |
| Sorriso | Soja | 55,74 | 120,48 | 216,66 |
| Sorriso | Milho 2ª | 0,57 | 39,59 | 144,25 |
| Sinop | Soja | 3,85 | 45,81 | 196,82 |
| Sinop | Milho 2ª | 11,07 | 36,25 | 155,31 |
| Lucas do Rio Verde | Soja | 66,82 | 139,82 | 250,85 |
| Lucas do Rio Verde | Milho 2ª | 15,38 | 33,57 | 166,99 |
| Campo Novo do Parecis | Soja | 107,76 | 175,98 | 279,81 |
| Campo Novo do Parecis | Milho 2ª | 6,24 | 41,88 | 172,53 |

Não concluir que uma cidade ou cultura é mais rentável com essa tabela. As datas, reservas de solo, demandas e ciclos diferem. Mesmo chuvas abundantes no ciclo podem gerar déficit quando se concentram em poucos dias e o excesso drena.

## 6. Arquivos para dados, backend e frontend

Diretório: [`data/processed/water_balance_v1/`](../../../data/processed/water_balance_v1/).

| Arquivo | Uso |
|---|---|
| et0_daily.csv | 70.128 dias; ET0, chuva, radiação calculada, VPD e indicadores de ajuste |
| scenario_summary.csv | Total por cenário, reserva inicial/final, ETc/ETa, déficit e diferença para o histórico pareado |
| phase_summary.csv | Mesmas métricas por fase, com início/fim inclusivos |
| daily_2024.csv | Chuva histórica/alterada, ET0, Kc, p, Ks, reserva, drenagem e balanço em cada dia |
| api_example.json | Soja Uberlândia 2024, AD4/70%, seca reprodutiva de 50%; inclui série histórica pareada e metadados |
| validation.json | Contagens, extremos, ajustes e verificações numéricas |
| provenance.json | Hashes das entradas, coordenadas, parâmetros e hipóteses |
| delivery_manifest.json | Hashes da entrega gerados após verificação final |

Unidades: fluxos e reservas em mm, ET0/ETc/ETa diários em mm/dia nos registros diários e acumulados em mm nos resumos. Kc/Ks/p e `eta_etc_ratio` adimensionais. `stress_days` conta Ks < 1−10⁻⁹; `min_ks` não é probabilidade de perda. `extra_et_deficit_vs_baseline_mm` compara o ciclo completo ao histórico da mesma cidade/cultura/ano/CAD/água inicial. Cenário e histórico não podem ser pareados apenas por município.

### Contrato recomendado para a futura API

O exemplo JSON é um artefato de dados; nenhum endpoint HTTP foi implantado nesta entrega. Implementar `POST /v1/water-balance/scenarios` com município ou talhão, cultura/cultivar, data de plantio, CAD, água inicial, fase e multiplicador de chuva. Validar unidades e intervalos no servidor; recusar dias incompletos e combinações fora da cobertura. Os identificadores atuais representam cenários pré-computados, não previsões em tempo real.

Retornar versão do motor, versão/hash do clima, escala espacial, datas, hipóteses, flags, histórico pareado, séries e resumos. Reservar campos de produtividade como indisponíveis até haver modelo calibrado; não derivar rendimento linearmente de `eta_etc_ratio`. Para irrigação real, exigir quantidade e data medidas. Acrescentar autenticação, limites de requisição e rastreamento do cenário na aplicação futura.

Frontend: comparar chuva em barras e reserva/ET em gráficos com unidades; destacar faixa da fase perturbada; oferecer CAD e água inicial como parâmetros de hipótese. O globo mantém seleção Brasil → estado → município; talhão só quando houver dados próprios. A animação 3D deve ser conduzida pelos estados diários do cenário, com rótulo de simulação. Ks baixo pode alterar a aparência ilustrativa da planta, mas não sustenta afirmar dano visual observado, doença ou rendimento. Mostrar a procedência pontual e a ausência de calibração de campo junto do resultado.

## 7. Validação realizada e critérios para evolução

Toda a suíte disponível passou: **25 testes** nesta rodada, dos quais seis específicos do balanço. ET0 confere com FAO exemplo 18; a sequência de depleção do exemplo 37 verifica o algoritmo geral de estresse (o exemplo original é tomate, não calibração de soja/milho). Também foram testados reservatório vazio, drenagem, escoamento/irrigação, dados inválidos, sequência diária e perturbação restrita à fase.

O executor verificou **524.520 dias simulados**: conservação de massa, armazenamento entre zero e CAD, ETa entre zero e ETc, ausência de mudança na demanda entre os pares e chuva alterada somente na fase selecionada. Conferiu 3.968 pares de seca: armazenamento nunca maior que o histórico e déficit total nunca menor, dentro da tolerância. Resíduo máximo: **1,4211 × 10⁻¹⁴ mm**, abaixo do limite de 10⁻⁸. ET0 variou de **0,6602 a 10,1028 mm/dia**; essa amplitude não equivale a validação contra estação.

Para a próxima etapa:

1. Obter pressão e ponto de orvalho com suporte espacial compatível com a malha agrícola v2. Recalcular e comparar ponto versus ponderação, sem substituir silenciosamente a série atual.
2. Calcular ET0 de estações com radiação, temperatura, vapor e vento confiáveis; conferir altura do anemômetro, horários e cobertura. Comparar viés/MAE/RMSE por município, mês e estação sem usar ausências como zero.
3. Medir CAD, profundidade de raízes, água inicial, chuva, irrigação e umidade ao longo do perfil em talhões consentidos. Comparar o armazenamento simulado com água disponível derivada de sensores calibrados. Um único sensor de umidade não mede sozinho toda a reserva da raiz.
4. Ajustar fenologia e Kc com cultivar/data/observações; substituir raiz fixa por dinâmica e considerar Kcb/Ke, infiltração/escoamento e camadas quando houver dados suficientes.
5. Para aprendizado supervisionado, usar déficits e ETa/ETc por fase como atributos derivados; rótulo de rendimento deve ser medido independentemente. Não usar saída deste simulador como verdade de produtividade.
6. Manter separação temporal/espacial do treinamento. Para previsão antes da colheita, usar apenas clima já observado e conjuntos de previsões/cenários disponíveis na data da decisão; o replay com clima futuro conhecido serve a análise retrospectiva e vazaria informação numa previsão operacional.
7. Só converter seca em rendimento ou margem depois de avaliar calibração por cultura/região, incerteza e comparação com baselines. Os modelos `research_v1/v2` não foram retreinados nem substituídos por esta entrega.

Até esses critérios serem atendidos, o estado de publicação deve continuar `research_uncalibrated`; indisponibilidade local deve aparecer como tal, inclusive nas lacunas INMET de Campo Novo.
