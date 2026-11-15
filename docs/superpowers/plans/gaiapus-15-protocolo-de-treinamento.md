# Gaiapus — protocolo reproduzível de treinamento e validação

> **Atualização v2 (14/11/2026):** consulte a [validação INMET, contexto de Campo Novo e ponderação agrícola](gaiapus-17-validacao-espacial-v2.md) e os [resultados comparativos](gaiapus-18-resultados-v2.md). Este guia preserva o contexto v1; os novos resultados e limitações estão nos guias 17–18.


## 1. Objetivo que o primeiro dataset permite

Estimar **rendimento municipal anual em kg/ha**, separadamente para soja e milho de segunda safra, com clima disponível em uma janela de calendário. É um benchmark retrospectivo de associação, com várias limitações de disponibilidade histórica. Não é causal, não é previsão meteorológica, não é estimativa certificada de rendimento de um talhão.

O alvo é `yield_kg_ha`. A linha é município × cultura × ano de colheita. Os anos de clima diários não aumentam o número independente de safras. O primeiro conjunto contém 351 linhas elegíveis; chuva/solo não acrescentam rótulos de rendimento que não existem.

## 2. Reprodução

Na raiz `ideias_nasa`, com os brutos já presentes:

```powershell
uv run --python 3.13 python -m unittest discover -s tests
uv run --python 3.13 python -m data.ingest.training_foundation
uv run --python 3.13 --with scikit-learn==1.7.2 --with numpy==2.3.3 --with pandas==2.3.3 python -m data.ingest.training_baseline
```

O segundo comando reconstrói o dataset local e os hashes sem acessar a rede. O terceiro treina somente no conjunto de treino e emite métricas de validação; não avalia o teste final. Os modelos são serializados com joblib e são de pesquisa. Carregar apenas os arquivos produzidos e verificados pela própria equipe.

Para coleta reprodutível, quando realmente necessária:

```powershell
uv run --with shapely==2.1.2 python -m data.ingest.training_foundation --download
uv run python -m data.ingest.station_reference
uv run python -m data.ingest.soil_context
```

O coletor principal usa cache e rejeita mudança no hash/URL de arquivo já armazenado. Uma nova versão de origem deve ir para outra pasta, não sobrescrever a base de um experimento. `soil_context` registra falhas por município no relatório; a existência do CSV não prova completude. Confira sempre os relatórios e a quantidade esperada.

## 3. Construção atual das entradas

| Cultura | Janela | Data de corte | Interpretação |
|---|---|---|---|
| Soja do ano Y | 1/outubro de Y−1 a 31/janeiro de Y | 31/janeiro | Proxy sazonal, sem data real de plantio |
| Milho 2ª do ano Y | 1/janeiro a 30/abril de Y | 30/abril | Proxy sazonal, mistura preparo e desenvolvimento conforme manejo |

As datas são hipóteses de engenharia para testar a infraestrutura, não recomendações de plantio. Algumas lavouras de soja já podem ter sido colhidas no corte. O produto final precisa de horizontes definidos, por exemplo previsão aos 30/60/90 dias após plantio, ou em uma data pré-safra. Esses horizontes exigem outros datasets, com chuva observada apenas até o corte e futuro vindo de previsão emitida naquela data ou de cenários explicitamente rotulados.

Entradas permitidas no baseline:

- Ano da colheita, para representar tendência histórica; deve ter ablação porque pode explicar melhora tecnológica, não clima.
- Média do rendimento Y−2 a Y−4, usando somente alvos válidos com áreas positivas. Zero numérico consistente é distinto de ausência. Imputação mediana aprendida no treino quando não existe histórico.
- Chuva acumulada em mm; número de dias abaixo de 1 mm; maior sequência consecutiva abaixo de 1 mm.
- Médias de mínima/máxima; dias acima de 35 °C; radiação acumulada; médias de umidade e vento.

**35 °C é apenas um indicador meteorológico fixo desta experiência**, não limite de dano calibrado por cultura. Graus-dia e limiares críticos por fase só entram quando houver parâmetros de cultivar e fonte agronômica documentados.

Proibidos como entradas: rendimento alvo, produção total, área colhida/plantada do próprio ano, valor bruto de produção, informações posteriores ao corte e estatísticas calculadas usando teste. `municipality_code` é usado para junção e avaliação, não para memorizar identificadores no modelo atual.

## 4. Separação treino/validação/teste

- Treino temporal: anos até 2018, excluindo integralmente Uberaba e Sinop do ajuste.
- Validação temporal: 2019–2021 nos seis municípios de treino.
- Validação espaço-temporal: 2019–2021 em Uberaba/Sinop. Seus históricos de produtividade podem ser entradas defasadas; portanto o experimento é transferência para município sem amostras no ajuste, **não** transferência para município sem histórico algum.
- Teste reservado: 2022–2024. O script não calcula métricas nem previsões nesse conjunto. A equipe deve evitar selecionar features examinando seus alvos. Os dados continuam em CSV acessível; a reserva é procedimental, não controle de acesso.

Não dividir linhas diárias aleatoriamente. Não colocar recortes do mesmo talhão/safra em treino e teste. Na expansão, agrupar também por células de clima compartilhadas e vizinhança espacial; municípios próximos não são independentes só por terem códigos diferentes.

Imputador e scaler são ajustados dentro do pipeline de treino. Na busca de hiperparâmetros, criar folds em origem temporal crescente, por exemplo ajustar até 2010/2013/2016 e validar nos dois anos seguintes, sempre mantendo os municípios de reserva fora do ajuste. A versão atual usa parâmetros fixos, sem busca ampla.

## 5. Baselines executados

| Cultura | Modelo | MAE temporal, kg/ha | MAE espaço-temporal, kg/ha |
|---|---|---:|---:|
| Soja | Média histórica defasada | 243,61 | 325,78 |
| Soja | Ridge | 189,83 | 294,99 |
| Soja | HistGradientBoosting | 208,53 | 320,20 |
| Milho 2ª | Média histórica defasada | 1.839,07 | 1.722,44 |
| Milho 2ª | Ridge | 1.262,82 | 1.179,55 |
| Milho 2ª | HistGradientBoosting | 1.582,17 | 1.417,98 |

Soja: 102 amostras no ajuste; milho: 90. Validação: 18 amostras temporais e seis espaço-temporais por cultura. São conjuntos muito pequenos, sem intervalo de confiança estabelecido. O erro de milho continua alto e não permite oferecer precisão de talhão. O melhor número desta rodada não justifica publicação do modelo.

Ridge: imputação mediana + padronização + `alpha=10`. Gradient boosting: 100 iterações, sete folhas máximas, pelo menos 15 amostras por folha, L2=10, semente 42. Métricas completas incluem MAE, RMSE e viés em `baseline_validation.csv`; previsões individuais de validação em `baseline_predictions_validation.csv`. O manifesto de modelos registra versões e hash do dataset.

## 6. Próximos experimentos, em ordem

1. **Ablação de tendência:** comparar média histórica, somente ano + histórico, somente clima, e clima + histórico. Demonstrar se o clima acrescenta informação além da tendência. Manter os splits.
2. **Suporte espacial:** substituir ponto representativo por média ponderada da área da cultura usando máscara anual validada. Manter a mesma versão de rótulos para mensurar o ganho.
3. **Validação da chuva:** usar INMET com convenção temporal conferida, sem ajustar correção no conjunto reservado. Avaliar erro por estação/mês e cobertura, não apenas correlação global.
4. **Features fisiológicas:** ET0, VPD, balanço hídrico, graus-dia e déficit por fase com parâmetros e incertezas. Recalcular sem usar o futuro para inferir a fase no corte.
5. **Sensoriamento remoto:** agregar curva HLS à mesma unidade do alvo; registrar cobertura e lacunas. Comparar desempenho com e sem HLS. Evitar que uma máscara anual produzida após a safra seja usada como informação disponível antes do plantio.
6. **MLP pequena como concorrente:** somente após as etapas anteriores, com early stopping temporal explícito; nada de validação interna aleatória que misture safras. Arquiteturas candidatas 16 ou 32 unidades, no máximo duas camadas, regularização L2/dropout, padronização pelo treino, Adam, taxa inicial 1e−3, teto de 300 épocas e paciência 20. Esses são parâmetros iniciais propostos, não execução já realizada.
7. **Estabilidade:** comparar cinco sementes para MLP, curvas de aprendizado, erro por município/UF/cultura, por faixa de rendimento e por seca. O melhor resultado isolado de uma semente não basta.
8. **Incerteza:** regressão quantílica ou calibração conformal em bloco de calibração separado, sem reutilizar os mesmos dados para escolher o modelo e calibrar o intervalo. Verificar cobertura e largura por região. Com poucos exemplos, não publicar probabilidades muito precisas.
9. **Teste final uma vez:** congelar código, parâmetros e features; gerar manifesto; abrir 2022–2024. Qualquer ajuste posterior cria nova rodada com outro período de teste ainda não usado.

Critérios propostos de avanço: redução de MAE pelo menos 10% contra baseline simples na validação temporal e melhora também espacial, sem degradação superior a 10% em qualquer UF; estabilidade entre anos e intervalos calibrados quando houver amostra suficiente. São critérios de engenharia a pactuar, não garantia estatística. A atual Ridge ainda não passa automaticamente todos esses critérios; por exemplo o ganho espacial da soja é pequeno.

## 7. Simulador de seca e relação com rendimento

Primeiro implementar um balanço explícito de água, usando a [FAO-56](https://www.fao.org/4/x0490e/x0490e00.htm) como referência. As variáveis necessárias já foram coletadas para preparar ET0, mas **ET0 e o balanço ainda não foram calculados nesta entrega**.

```text
Tmed = (Tmin + Tmax) / 2
es(T) = 0,6108 * exp(17,27*T / (T + 237,3))              [kPa]
ea = es(Torvalho)                                      [aproximação com média diária]
VPD = média(es(Tmin), es(Tmax)) - ea                     [avaliar consistência]
ET0 = FAO Penman–Monteith diária com Rn, G, Tmed, u2, es-ea, Δ e γ
ETc = Kc(fase) * ET0
TAW = 1000 * (theta_fc - theta_wp) * Zr                  [mm; Zr em m]
RAW = p * TAW
ETa = Ks * ETc                                         [modelo simplificado]
S(t+1) = S(t) + chuva_infiltrada + irrigacao - ETa - drenagem
```

Escoamento, drenagem e limites de armazenamento precisam fechar o balanço de massa. Crescimento de raízes altera a capacidade explorada; não usar CAD constante sem declarar a aproximação. Não igualar precipitação total a água infiltrada. Não usar pH ou textura diretamente como CAD sem parametrização. Em evapotranspiração, conferir albedo de referência, radiação líquida, altitude, fluxo de calor do solo e unidades; usar casos numéricos oficiais de referência como testes.

Para cenários: criar cópia imutável do histórico, reduzir chuva apenas nos dias/fases escolhidos, manter o histórico original e registrar hipótese. Comparar água às raízes, dias de estresse e déficit acumulado, com diferentes estados iniciais. Um cenário de −30% de chuva **não** implica −30% de rendimento.

A transformação déficit → rendimento precisa de cultivar, manejo e colheitas observadas ou modelo agronômico calibrado. Não treinar uma rede com perda de rendimento arbitrária fabricada e depois apresentar a saída como conhecimento aprendido do mundo. Dados sintéticos podem testar software; não demonstram precisão agrícola.

## 8. Dados que só a parceria de campo pode fornecer

Usar o cabeçalho de `data/templates/field_harvests.csv`; cada linha é talhão × safra × cultura. Campos mínimos: identificador anônimo, município, polígono, cultura/cultivar, ciclo/grupo, plantio/emergência/colheita, irrigação, solo por profundidade, produção/área/umidade e método de medição. Registrar operações e cultura anterior.

Amostras de solo: textura, densidade, capacidade de campo, ponto de murcha, profundidade efetiva, pH, matéria orgânica e análise de nutrientes com laboratório/data. IoT: água volumétrica calibrada em mais de uma profundidade, temperatura, pluviômetro ou estação de referência, integridade e posição. Sondas de baixo custo não tornam NPK medido automaticamente confiável.

Nenhuma API pública resolve o cultivar/manejo que ocorreu dentro de uma fazenda específica. Esse é o principal investimento para passar de demonstração regional a modelo de talhão, muito antes de baixar centenas de GB.
