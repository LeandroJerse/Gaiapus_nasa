# Gaiapus v9 — plano de avaliação com fontes disponíveis na decisão e registro prospectivo

## Andamento (2026-11-14)

- §4 iniciado. Protocolo `data/frozen/prospective_v9_protocol.json` publicado (commit `aa52a73`) antes do primeiro registro;
  avaliador `data/serving/score_prospective.py` implementado e testado antes de qualquer resultado (`f52167a`).
- Primeiro dia: 1.917 registros (8 culturas), cadeia de hashes verificada, cópia selada `sealed/2026-11-14.jsonl.gz` + manifesto
  publicados (`abb7381`, 2026-11-14 16:47 −03).
- **Correção (auditoria):** o relatório anterior dizia "todos tardios" sem aplicar a regra. Com a regra `on_time` (emenda A5):
  **244 no prazo** (soja 84 e milho 1ª 160, corte 2026-11-03, prazo 2026-11-16) e **1.673 tardios** (milho 1ª com corte 2026-10-29,
  trigo, feijão 1ª, arroz e cafés). Reproduzido por `tests/test_score_prospective.py::FirstBatchTests`. Nenhum registro foi
  reescrito e a tolerância não mudou.
- Modelos **presentes no lote 1** (38): 8 `history_ref`, 13 referências publicadas, 9 do catálogo v7 (inclui soja MG+MT) e
  4 candidatos v8 (soja MG/MT e cafés). **Nenhum v9**: os 36 candidatos v9 foram inscritos depois (`c9feba5`, 20:01 UTC) e só
  recebem chaves com prazo planejado a partir do dia da inscrição.
- Modelos inscritos no total: 11 do catálogo v7, 6 candidatos v8, 29 referências simples e 36 candidatos v9
  (`data/frozen/training_v9_protocol.json`). Artefatos congelados: release `prospective-v9-models` (ZIP sha256 `7b0edab1…988c2e`).
- Emendas versionadas (`data/frozen/prospective_v9_protocol_versions.jsonl`, hash de cada versão e impacto na elegibilidade):
  A1 registros tardios à parte; A2 cobertura sobre chaves esperadas (ausências, atrasos, falhas contam contra); A3 UF prioritária
  sem pares torna o escopo inconclusivo; A4 proveniência obrigatória (cadeia íntegra, versão do protocolo anterior à emissão,
  POWER/IMERG Late/PAM presentes, obtenção antes da emissão, disponibilidade comprovada por hora registrada ou selagem até o dia
  seguinte); A5 datas em horário de Brasília (corte, prazo planejado = corte + 6, prazo-limite = + 7, emissão = dia de `issued_at`);
  A6 calendário.
- Lote 1 sob A4: horas de obtenção de POWER e IMERG vêm do horário do arquivo de cache (antes dos registros laterais), mas o lote foi
  selado no mesmo dia, então a disponibilidade fica comprovada pela selagem; os 244 no prazo passam nas checagens.
- Job diário (03:30): registra, verifica a cadeia, sela e envia só `data/prospective_registry` ao repositório.
- Primeira pontuação: esperada após a divulgação oficial da PAM 2027 pelo IBGE (historicamente em setembro do ano seguinte, ou
  seja, por volta de setembro de 2028); a data depende dessa divulgação. Antes disso nenhum modelo pode ser aprovado.
- §1 concluído: `data/frozen/source_availability_v9.json` (verificado em 2026-11-14 contra a documentação de GPM/IMERG, NASA POWER e
  ECMWF/ERA5). Pontos-chave: Early ~4 h, Late ~14 h, Final ~3,5 meses; POWER usa GEOS (dias) e FLASHFlux (5–7 dias) perto do tempo
  real, depois substituídos por MERRA-2 e CERES SYN1deg (3–4 meses; atraso do SYN1deg desde ago/2024) — por isso a disponibilidade
  histórica da meteorologia e da radiação do POWER é **desconhecida**; ERA5T ~5 dias, trocado pelo ERA5 final ~2–3 meses depois
  (**estimada**). Só a PAM tem disponibilidade histórica **comprovada**.
- §2 concluído (retrospectivo com fontes de tipo operacional; Late/Early/ERA5 arquivados hoje, não as versões de 2021):
  `data/ingest/source_shift_v9.py`, modelos v7 servidos (ajuste ≤ 2019), safra 2021, sem treino, 23.871 chaves pareadas, cobertura
  1,0 em todas as variantes. Saídas em `data/processed/training_v9/source_shift/` (summary/feature_drift/coverage_2021.csv).
  - Deriva das entradas na janela: chuva Late +34 a +60 mm de viés em soja, milho 1ª, arroz e feijão 1ª (Early maior: até +85 mm);
    trigo e milho 2ª com viés negativo pequeno; ET0 com ERA5 até +10 mm.
  - Efeito no MAE (variante − final, IC95% bootstrap): todas as mudanças ≤ 2,6% do MAE. Maiores: milho 2ª Early +26,7 kg/ha
    [19,8; 33,6] (+1,6%); café arábica ERA5 +6,5 [2,9; 10,3] (+2,6%); soja nacional Late +3,9 [0,5; 7,2] (+1,3%); soja MG+MT Late
    +2,6 [−1,7; 6,8]. Late+ERA5 (configuração do serviço ao vivo) não piorou de forma demonstrável em nenhum escopo.
  - Feijão 1ª, milho 1ª e conilon: delta exatamente 0 — os modelos servidos dessas culturas não respondem às variáveis de clima
    trocadas. O efeito pequeno nas demais reflete também a baixa sensibilidade dos modelos v7 ao clima; **não** demonstra robustez de
    um modelo fortemente dependente do clima (os candidatos v9 MLP precisam da mesma checagem antes de qualquer aprovação).
- Nenhum candidato novo está sendo treinado.

Status original: plano. Data: 2026-11-14.

Ponto de partida (resultado v8, `gaiapus-v8-resultados.md`): nenhum candidato demonstrou superioridade consistente suficiente para
promoção; a v8 fica experimental e o catálogo servido (v7) não muda. A avaliação 2025 é retrospectiva com exposição prévia
documentada e **não será reaberta** para ajustar modelos.

Regras globais
- Nenhum modelo servido muda automaticamente; promoção só após confirmação prospectiva (§4) e decisão explícita.
- Toda correção de viés, recalibração ou adaptação às fontes operacionais usa **apenas dados de desenvolvimento**, com protocolo novo
  registrado antes de qualquer score.
- 2022–2024 e 2025 não servem mais como confirmação independente.
- Métricas são de rendimento **municipal** (IBGE PAM); não são precisão de talhão.

## 1. Inventário de fontes por disponibilidade na data de decisão

Princípio: um arquivo histórico baixado hoje mostra o valor **atual** daquela data, não prova que aquela versão existia na data
passada. Classes:
- **comprovada**: há registro datado de quando o valor ficou disponível (calendário oficial, metadado de produção do arquivo
  confirmando data ≤ decisão, ou registro nosso feito na própria data);
- **estimada**: só há latência típica documentada pelo produtor;
- **desconhecida**: sem registro de versão nem latência utilizável.

| Fonte | Uso | Latência típica (documentação do produtor) | Disponibilidade histórica (2016–2025) | Disponibilidade prospectiva (a partir do registro) | Ação |
|---|---|---|---|---|---|
| IBGE PAM (lags) | rendimentos publicados | calendário anual (9117) | **comprovada** (calendário) | comprovada | manter verificação data a data |
| IMERG Early V07 | chuva | ~4 h | estimada; versões V07 foram reprocessadas depois — o valor de hoje não é o que existia na época | comprovada se gravarmos grânulo, versão e hora de obtenção | registrar `ProductionDateTime`/versão do grânulo (CMR) |
| IMERG Late V07 | chuva (serviço atual) | ~14 h | estimada; mesmo problema de reprocessamento (ex.: V07B → V07C) | comprovada com registro | idem |
| IMERG Final V07 | chuva (treino) | ~3,5 meses | estimada; viola `available_at ≤ decision_at` em todos os cortes anuais | indisponível na decisão | usar só como referência retrospectiva |
| ERA5 (Open-Meteo archive) | radiação nos dias sem CERES | ERA5T ~5 dias; ERA5 final substitui o ERA5T depois de ~2–3 meses | estimada; o arquivo atual é o ERA5 final, não o ERA5T da época | comprovada com registro da resposta e data | registrar hora da consulta e se o dia é ERA5T |
| NASA POWER radiação (CERES) | radiação | a verificar na documentação POWER | **desconhecida** | comprovada com registro | levantar latência/versão oficial antes do protocolo |
| NASA POWER meteorologia (T, UR, vento, orvalho, pressão) | features | a verificar | **desconhecida** | comprovada com registro | idem |
| INMET (estações automáticas) | verdade de solo da chuva | horária, quase em tempo real; arquivo anual consolidado depois | estimada (dados podem ser revisados) | comprovada com registro | só para avaliar fontes, não como feature |
| Open-Meteo previsão | projeção hídrica | emissão diária | depende de arquivo de rodadas anteriores | comprovada com `issued_at`/`available_at` | já registrado no contrato da projeção hídrica |

Entregável: `data/frozen/source_availability_v9.json` com produtor, documento citado, latência, classe e data de verificação de cada
fonte, revisado antes do protocolo.

## 2. Comparação controlada: entradas finais × entradas operacionais (modelos e chaves fixos)

Objetivo: medir quanto o erro de rendimento muda **só** pela troca das entradas, sem treinar nada.

1. Modelos: os servidos (v7) e os candidatos v8 congelados, sem reajuste.
2. Chaves: as mesmas município × safra × corte do desenvolvimento (2019–2021) e dos municípios cobertos; mesmas referências.
3. Variantes de entrada, uma por vez e combinadas:
   a. chuva IMERG Final → Late; b. Final → Early; c. radiação CERES → ERA5; d. combinação usada pelo job ao vivo.
4. Métricas: MAE e viés de rendimento por cultura, UF e corte; diferença pareada operacional − final com bootstrap por município
   estratificado por UF; deriva das features (chuva acumulada, ET0, déficit) por corte.
5. Classificação: esta comparação usa arquivos atuais de Late/Early/ERA5; é **retrospectiva com fontes operacionais**, não
   reprodução em tempo real (o valor histórico disponível na época é desconhecido ou estimado, §1).
6. Custo: estender o subconjunto IMERG Late/Early (hoje só 8 células MVP 2016–2021) às coortes de soja, milho e café; ERA5 para
   todos os municípios. Reaproveitar `imerg_subset.py`, `source_swap_study.py` e caches existentes.

Fase posterior (opcional, protocolo novo): correção de viés da chuva Late ou ajuste dos modelos às entradas operacionais,
ajustados **só em anos de desenvolvimento** (≤2021), selecionados por validação interna e avaliados apenas no registro prospectivo.

## 3. O que não fazer

- Não escolher janelas, fontes ou correções olhando 2022–2024 ou 2025 e depois apresentá-los como confirmação.
- Não tratar a comparação de fontes (chuva × INMET, Late × Final) como validação do rendimento previsto.
- Não chamar de "validado" um modelo apenas treinado, publicado ou servido.

## 4. Registro prospectivo

Elegibilidade (definida por datas, não por ano fixo): unidade cultura × município × safra × corte cuja `decision_at` seja posterior
ao commit do registro dos modelos congelados, e cuja PAM ainda não exista. Exige **as duas** condições: previsão gravada até
`decision_at` **e** todas as entradas com `available_at ≤ decision_at` registradas.

Registro por previsão (JSON Lines, um arquivo por dia):

| Campo | Conteúdo |
|---|---|
| `prediction_id` | hash de (cultura, município, safra, corte, modelo) |
| `crop`, `municipality_code`, `harvest_year`, `cutoff_days` / `milestone` | chave |
| `decision_at` | dia seguinte ao corte (UTC) |
| `issued_at` | hora em que a previsão foi gravada |
| `target` | definido antes: rendimento municipal PAM (kg/ha) da **primeira** divulgação da safra |
| `model` | versão, nome, escopo, sha256 do artefato, sha256 do catálogo |
| `references` | history e referências simples calculadas na mesma hora, com as mesmas entradas |
| `inputs[]` | para cada fonte: produto, versão/grânulo, período coberto, `retrieved_at`, `available_at` e classe (§1), sha256 da resposta bruta |
| `coverage` | dias esperados × presentes, municípios cobertos |
| `estimate_kg_ha`, `status`, `warnings` | saída |
| `record_sha256`, `previous_record_sha256` | encadeamento para detectar alteração |

Selagem: manifesto diário com o sha256 do arquivo do dia, enviado ao repositório (commit datado) no mesmo dia; nenhum registro é
recalculado depois da divulgação da PAM.

Pontuação: uma vez, após a primeira divulgação da PAM da safra, contra o alvo definido, com as mesmas referências e critérios
(≥5% de ganho sobre a referência simples mais forte, IC95 < 0, nenhuma UF prioritária >5% pior), por cultura e região; resultado
gravado uma vez, sem sobrescrever.

Lacuna atual: os snapshots do job ao vivo não guardam versão/grânulo e hora de obtenção de cada fonte nem hash encadeado; o primeiro
passo de implementação é acrescentar isso ao `daily_job` sem alterar modelos.

## 5. Ordem de execução proposta

1. Levantar e registrar a disponibilidade documentada das fontes (§1).
2. Implementar o registro prospectivo no job diário (§4) e começar a acumular previsões elegíveis.
3. Executar a comparação controlada final × operacional (§2) nos anos de desenvolvimento, sem treinar.
4. Só então decidir, com protocolo novo, se vale testar correções ajustadas em desenvolvimento.
5. Pontuar o registro prospectivo quando a PAM das safras registradas for divulgada.
