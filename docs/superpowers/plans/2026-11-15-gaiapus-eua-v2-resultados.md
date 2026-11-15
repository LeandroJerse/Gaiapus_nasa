# Gaiapus EUA v2 — resultados executados em 15/11/2026

**Conclusão:** a v2 foi registrada, treinada, congelada e confirmada uma única vez na safra 2025. **Só o trigo de inverno do Kansas superou todas as referências** no corte agrícola e também nas emissões pré-safra. Em Iowa e Nebraska os modelos superaram as referências simples, mas não a regressão de histórico. Nenhuma fonte nova (gridMET, ponderação por área de cultura, irrigação do Nebraska, calendário do trigo) confirmou ganho pelas regras registradas. Nenhum modelo foi promovido. Os cenários de 2026, 2027 e 2028 foram calculados e publicados como pesquisa.

Protocolo: `data/frozen/us_training_v2_protocol.json`, `us_training_v2_selection_addendum.json`, `us_v2_availability.json`. Plano e auditoria da v1: [plano v2](2026-11-15-gaiapus-eua-v2-plano.md). Branch local `feat/us-v2`, sem push.

## 1. O que foi executado

- **v1 preservada:** código, protocolo, testes e relatórios no Git, byte a byte iguais aos hashes dos freezes; selo SHA-256 de 633 arquivos (v1 + modelos e protocolos brasileiros), sem divergência ao final; backup zip verificado em `C:\Users\leand\Backups\gaiapus\` (mesmo disco, fora do OneDrive).
- **Rótulos USDA:** mesmo extrato da v1 (hash igual). Desenvolvimento 1990–2024; 2025 lacrado na extração; 0 registros de 2026 no extrato. Rótulos 1990–2020 idênticos aos da v1.
- **Clima:** NASA POWER para os 297 condados de IA/NE/KS, 2000-01-01 a 2026-11-13, sem lacunas internas nem violações físicas. A radiação só chega a 2026-08-31 (latência de 75 dias) e ficou fora das features. gridMET 2000–2025 (5,3 GB baixados) e máscaras CDL de 7 anos (4,8 GB) para o E2.
- **Condados:** 245 elegíveis (IA 93, NE 62, KS 90), escolhidos só com dados de desenvolvimento; 20% reservados por estado para avaliação espacial.
- **Disponibilidade:** regra `available_at <= issued_at`. O último rótulo é resolvido por condado e cultura: nos cenários emitidos hoje, 2025 é o último rótulo em 72% dos casos; nos demais é 2021–2024 (supressão, ou a safra 2024 de trigo do KS, que não foi publicada por condado).
- **Desenvolvimento:** validação temporal 2015–2024, grades de modelo idênticas às da v1, três tipos de emissão (no corte, 6 e 18 meses antes da janela). O modelo único com horizonte como feature (política B) venceu os modelos por horizonte nos 5 escopos.
- **Testes:** 192 passando (v1, selo, v2 e gerador de cenários).

## 2. Confirmação na safra 2025 (aberta uma vez)

MAE em kg/ha, média dos erros por condado, condados usados no treino com rótulo publicado. "Confirmado" exige ganho ≥5%, limite superior ajustado para 53 comparações abaixo de zero, ganho não negativo na reserva espacial de desenvolvimento e ≥30 pares.

### 2.1 Modelo escolhido no corte agrícola contra cada referência

| Escopo | Escolhido | MAE | `last` | `mean3` | `trend` | `history_ridge` | Pares | Status do escopo |
|---|---|---:|---:|---:|---:|---:|---:|---|
| Iowa — soja | MLP 32×16 | 120 | 240 | 303 | 299 | **118** | 58 | ganho não confirmado |
| Iowa — milho | HGB 7 | 617 | 708 | 695 | 728 | **445** | 63 | ganho não confirmado |
| Nebraska — soja | HGB 7 | 701 | 686 | 830 | 807 | **463** | 28 | sem poder (<30 pares) |
| Nebraska — milho | MLP 32×16 | 508 | 1.011 | 1.414 | 1.272 | 529 | 30 | ganho não confirmado |
| Kansas — trigo de inverno | MLP 32×16 | **563** | 1.080 | 746 | 814 | 634 | 50 | **confirmado** |

Em IA soja e NE milho o escolhido foi confirmado contra `last`, `mean3` e `trend`, mas empatou com `history_ridge`. Em IA milho e NE soja o `history_ridge` foi melhor. Na reserva espacial de 2025 (6 a 15 pares por escopo, só descritivo) o padrão é o mesmo.

### 2.2 Cadeia pré-safra com clima simulado contra a referência de histórico

| Escopo | 6 meses antes: cadeia / referência | 18 meses antes: cadeia / referência | Status |
|---|---|---|---|
| Iowa — soja | 186 / 115 | 272 / 212 | não confirmado (cadeia pior) |
| Iowa — milho | 465 / 907 | 515 / 587 | confirmado a 6 meses; não a 18 |
| Nebraska — soja | 583 / 430 | 564 / 439 | sem poder (cadeia pior) |
| Nebraska — milho | 493 / 489 | 549 / 544 | não confirmado (empate) |
| Kansas — trigo | 541 / 746 | 563 / 675 | confirmado nos dois |

### 2.3 Ablações

| Pergunta | Resultado em 2025 |
|---|---|
| Disponibilidade real × defasagem fixa de 2 anos (população v1) | Não confirmado. IA milho 513 × 647 (ganho 21%, limite ajustado acima de zero); NE milho piorou (1.347 × 589). Demais com menos de 30 pares. |
| Ampliar condados × população v1 | Não confirmado; 20 a 33 pares por escopo. |
| gridMET ponto × POWER ponto | Não confirmado em IA e KS. NE milho 615 × 918: **confirmado só depois da correção da seção 3, tratar como provisório**. |
| gridMET ponderado por CDL × gridMET ponto | Não confirmado em nenhum escopo (IA milho 568 × 639 foi o maior ganho, limite ajustado em zero). |
| Irrigação NE | Não testada de fato: no desenvolvimento a seleção do braço ficou numa referência (NE milho) ou igual à base (NE soja). O recorte por prática do USDA termina em 2018. |
| Calendário do trigo KS (Crop Progress) | 454 × 602 (ganho 24,5%), **não confirmado**: limite ajustado acima de zero e ganho negativo na reserva espacial de desenvolvimento. |

### 2.4 Diagnóstico do descompasso de idade do histórico (só desenvolvimento)

Na população v1, a regra ao estilo v1 (ajuste com defasagem 2, avaliação com teto fixo de 2 a 5 anos) teve MAE de validação pior que a defasagem consistente em 3 dos 5 escopos: IA soja 322 × 254, IA milho 1.192 × 977, NE milho 922 × 885, NE soja 351 × 354 (empate), KS 616 × 660 (melhor). É compatível com a hipótese, mas não a comprova: não é confirmação independente e o KS vai no sentido contrário.

## 3. Correção feita depois da abertura

A saída da abertura mostrou o mesmo ganho espacial de desenvolvimento em escopos diferentes: o cálculo desse critério não filtrava por escopo nas famílias F1–F6. O critério usa apenas previsões de desenvolvimento congeladas antes da abertura, nunca rótulos de 2025, então foi recalculado mecanicamente por escopo.

- `c1_score.json` ficou intacto; a correção está em `c1_score_correction.json` (script `scripts/us_v2_c1_correction.py`).
- As 13 confirmações originais se mantiveram.
- Quatro comparações cujo braço selecionou uma referência passaram a `reference_selected`.
- Uma comparação subiu para confirmada (gridMET ponto × POWER, NE milho). Por ter mudado depois da abertura, deve ser lida como provisória e replicada em 2026.

Contagem final: 14 confirmadas, 21 não confirmadas, 14 sem poder, 4 com referência selecionada.

Antes da abertura houve duas correções técnicas, sem leitura de rótulos: o E2 ponderado foi refeito porque a tabela foi regerada depois do primeiro treino, e o manifesto final foi regenerado depois de um ensaio com rótulos sintéticos achar um defeito na tabela de cobertura.

## 4. Cenários 2026, 2027 e 2028

Emitidos em 2026-11-15T15:27:55Z, com os modelos refeitos usando os rótulos publicados até 2025. São 1.200 casos (245 condados × culturas × 3 safras), sem erro.

| Safra | Situação na emissão | Casos | Elegível para avaliação operacional |
|---|---|---:|---|
| 2026 | pós-colheita, antes da divulgação do rótulo | 400 | não — não prova habilidade pré-colheita |
| 2027 (IA/NE) | pré-safra, ~6 meses | 310 | sim, como emissão pré-safra |
| 2027 (KS trigo) | em safra, 43 dias observados + cenários | 90 | sim, como emissão parcial |
| 2028 | pré-safra | 400 | sim |

- **Clima futuro:** blocos contínuos de 26 anos históricos (2000–2025) do gerador `data/forecasting/weather_ensemble.py`. É cenário climatológico, não previsão do tempo.
- **Faixa de cenários (P10–P90 entre membros):** mediana de 409–413 kg/ha em IA soja, 605 em NE soja, 957 em KS trigo, ~1.700–1.800 em milho. No desenvolvimento essa faixa cobriu só 37–52% dos rendimentos observados, contra 80% nominais. Não é intervalo preditivo.
- **Faixa preditiva:** P50 mais os quantis 10% e 90% dos resíduos de desenvolvimento do mesmo horizonte, publicada em campo separado.
- **Balanço hídrico por membro:** motor FAO-56 do repositório com os parâmetros `*_us`, capacidade de 55 mm e armazenamento inicial de 50% (hipóteses), só sequeiro. O balanço fechou em todos os membros. O evento "20 ou mais dias de estresse na fase reprodutiva" ocorreu em 100% dos membros nos exemplos; com essa capacidade de solo ele não discrimina cenários e precisa de outra definição ou de dado de solo.
- **Sem balanço diário:** safra 2026 (janela já encerrada) e trigo KS 2027 (o trecho observado não tem radiação para o ET0). O catálogo registra o motivo.

## 5. Entrega para a aplicação

| Item | Caminho |
|---|---|
| Modelos de produto e pré-processadores | `data/models/research_us_v2/product/<escopo>_<tipo>.joblib` |
| Modelos congelados da confirmação | `data/models/research_us_v2/<braço>/` |
| Contrato de resultados | `data/frozen/us_yield_scenarios_contract_v1.schema.json` |
| Exemplos 2026–2028 (Story IA, York NE, Sumner KS) | `data/processed/us_training_v2/product/examples/` (15 arquivos, validados contra o contrato) |
| Resultados por membro e resumos | `data/processed/us_training_v2/product/us_scenarios_{members,summary}_2026-11-15.csv.gz` |
| Manifesto (hashes, semente, anos análogos, tempo, RAM) | `.../product/us_scenarios_manifest_2026-11-15.json` |
| Publicação no layout do APP | `data/processed/us_training_v2/publish/` — 710 execuções, 245 lugares, checksums conferidos |
| Registro prospectivo | `data/frozen/us_v2_prospective_registry/batch_2026-11-15.json` (1.200 entradas) |
| Domínio | `data/processed/us_training_v2/counties_v2.json` |

- **Features:** ordem em `features` de cada pacote; história (`hist_*`, `year`), coordenadas, 27 colunas de clima em três segmentos e `lead_days`. Ausência vira mediana do treino mais indicador; nunca zero.
- **Inferência em CPU:** as previsões congeladas e os cenários saem da implementação NumPy; diferença máxima para CUDA de 0,0001 kg/ha.
- **Medições:** produto completo em 571 s, pico de 1.439 MB de RAM. Armazenamento: brutos v2 6,0 GB (quase tudo gridMET e CDL), processados 18 MB antes do produto, produto 140 MB, publicação 148 MB, modelos 9 MB.

## 6. Limites

- **Um único ano de confirmação.** Os intervalos não medem a variação entre safras; 2026 e 2027 precisam replicar.
- **Avaliação retrospectiva com disponibilidade estimada.** Antes de 2023 as datas de divulgação são limites conservadores, não datas confirmadas. Clima e rótulos são versões atuais revisadas.
- **Iowa 2025** teve dois valores vistos em snippets antes da v1; os resultados de Iowa não são plenamente cegos.
- **Cobertura de 2025:** faltou rótulo em 15–22% dos pares previstos em IA, 40–44% em NE e 31% em KS.
- **Estimativa por condado**, não por talhão. Semeadura é hipótese regional fixa; Crop Progress é estadual.
- **Datas de 2027** das estimativas por condado ainda não foram publicadas pelo USDA; o registro as marca como desconhecidas.
- **Backup** da v1 está no mesmo disco físico.

## 7. Como continuar

1. Pontuar o registro prospectivo quando o USDA divulgar: trigo KS 2026 previsto para 18/12/2026; milho e soja 2026 sem data oficial.
2. Não reutilizar 2025 como teste independente; ele passa a ser desenvolvimento.
3. Reemitir o trigo KS 2027 em cada corte futuro com novo lote no registro.
4. Se quiser insistir no calendário do trigo e no gridMET do NE, registrar novo protocolo e confirmar em 2026.
5. Integração no APP: a outra sessão do Claude mescla `publish/` no pin; nenhum status vira aprovação.
