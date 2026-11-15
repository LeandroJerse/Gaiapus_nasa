# Gaiapus — janelas ZARC e clima por fase

**Atualização:** o balanço com ET0 e os cenários por fase estão [implementados e documentados no guia 19](gaiapus-19-balanco-hidrico-et0.md). A execução nova usa os oito pontos municipais v1, diferentes dos dois recortes deste guia. As datas hipotéticas não são autorização ZARC histórica; soja e milho não formam uma sucessão validada.

Safra oficial **2026/27**, para sequeiro, Uberlândia (3170206) e Sorriso (5107925). A extração usa o [CSV oficial da Tábua de Risco do MAPA](https://dados.agricultura.gov.br/pt_BR/dataset/tabua-de-risco-zoneamento-agricola-de-risco-climatico/resource/139e5a60-1f43-4cc8-aeab-a35dbbf816c0), associado às portarias 200 e 202 para soja e 324 e 326 para milho de segunda safra. O [Painel ZARC](https://mapa-indicadores.agricultura.gov.br/publico/extensions/Zarc/Zarc.html) é a referência visual. A extração foi feita pelo arquivo estruturado publicado pelo MAPA. Os códigos de ciclo, solo e regime seguem o [dicionário oficial](https://dados.agricultura.gov.br/dataset/6d3d141c-885e-41a4-ab7f-dc8ff323b96f/resource/bebb0ebb-bc75-460c-b900-a7a1ebd87bee/download/dicionario-de-dados-tabua-de-risco-2026.pdf).

| Cidade e cultura | 20% | 30% | 40% |
|---|---|---|---|
| Uberlândia · soja | 11/10/2026–10/01/2027 | 01–10/10/2026 | — |
| Sorriso · soja | 01/10/2026–10/01/2027 | — | 21–30/09/2026 |
| Uberlândia · milho 2ª safra | 21/12/2026–20/01/2027 | 21–31/01/2027 | — |
| Sorriso · milho 2ª safra | 01–31/01/2027 | 01–10/02/2027 | — |

**A tabela mostra somente o exemplo Grupo II, AD4**, com as classes registradas no CSV. Consulte e respeite também o calendário fitossanitário aplicável à soja e a data real de colheita da cultura anterior antes de usar uma janela de milho. Há 60 perfis para os dois municípios: soja grupos I–III, milho grupos I–II, ambos com AD1–AD6. O grupo III do milho consta da metodologia, mas não tem linha municipal nessas quatro extrações. O código de origem 0 indica decêndio sem indicação publicada para aquele perfil. O percentual ZARC é uma classe de risco agroclimático, sem conversão direta para percentual de perda ou para o risco calculado no replay.

| Arquivo | Conteúdo |
|---|---|
| [Recorte bruto](../../../data/raw/zarc/recorte-2026-2027.csv) | 60 linhas oficiais, inclusive colunas e classificações originais. |
| [Janelas normalizadas](../../../data/processed/zarc/windows.csv) | 2.160 linhas, 36 decêndios por perfil. São 481 indicações de 20%, 30% ou 40%. Inclui datas, CAD de referência, classe AD e portaria. |
| [Perfis de fase](../../../data/processed/zarc/phase_profiles.csv) | Dias por fase. Milho: portarias 324/326, seção 1.12. Soja: tabela mostrada pela [Embrapa no seminário ZARC Soja 06 ADs de 2023](https://www.embrapa.br/documents/1355202/83794724/4.%2BJRenato%2B-%2BZARC%2BSoja%2B06%2BADs%2B_%2B2023%2B_%2BSeminario%2BZARC.pdf/3bed6875-beb3-fbb6-fe37-c7f033a45629), como referência de replay. Validar a fenologia do cultivar em campo. |
| [Clima agregado por fase](../../../data/processed/zarc/phase_replay.csv) | 29.144 fases para 7.286 plantios históricos hipotéticos, com chuva acumulada, dias secos, maior sequência seca e médias de temperatura e umidade. |
| [Exemplos diários](../../../data/processed/zarc/daily_examples.csv) | 470 linhas e quatro casos Grupo II da safra histórica 2023/24, com chuva, temperatura, umidade do ar, radiação e vento para alimentar o simulador. |
| [Manifesto](../../../data/processed/zarc/manifest.json) | Versão do recurso, checksums, origem das fases, limiar e contagem de candidatos excluídos por falta de cobertura. |

## Etapas para amanhã

1. Escolher município, cultura, grupo, AD e data em `windows.csv`. Chave: código IBGE + cultura + grupo + AD + decêndio. Para o primeiro teste usar Grupo II/AD4. Guardar o código do perfil completo junto com o cenário.
2. Abrir `daily_examples.csv` e mostrar o replay base. Para simular seca, copiar o cenário e reduzir a chuva em um intervalo escolhido de uma fase, preservando as demais entradas. Rotular a cópia como hipótese. O histórico usou o [NASA POWER Daily](https://power.larc.nasa.gov/docs/services/api/temporal/daily/) de 2016–2024 em UTC. As datas de plantio de 2026/27 foram projetadas para os mesmos decêndios das safras históricas, e a série descartou plantios sem ciclo completo. Cada decêndio cobre dias 1–10, 11–20 ou 21–fim do mês, inclusive fevereiro bissexto. Um dia seco, aqui, tem chuva **abaixo de 1 mm**. As métricas usam somente os dias dentro da fase.
3. Implementar balanço de água no solo, usando CAD de referência ou medida, água inicial, irrigação e evapotranspiração. O resumo de chuva é compartilhado entre os solos porque ele usa a mesma série meteorológica. O efeito de cada classe AD aparece quando o solo entra no balanço. Validar com sonda, estádio observado e chuva de campo. Calibrar com colheitas medidas antes de ligar seca à produtividade ou às [margens](gaiapus-11-margens-e-seca.md).
4. Reproduzir com `python -m data.ingest.zarc_replay`. Para conferir a extração diretamente do bruto nacional, usar `python -m data.ingest.zarc_replay --source data/raw/zarc/tabua-2026-2027.csv`. A revisão do Claude Code foi usada para testar todos os dias de cada decêndio, descartar safras truncadas e manter explícito o limiar de chuva.

No caso de soja Grupo II em Uberlândia com plantio hipotético em 05/11/2023, a fase reprodutiva projetada de 25/12/2023 a 07/02/2024 teve 386,3 mm e uma sequência de sete dias abaixo de 1 mm. Uma chuva acumulada alta pode coexistir com uma sequência seca. O balanço do solo é que permitirá estimar quanto dessa sequência se traduz em déficit às raízes.
