# Gaiapus 02 — mineração, publicação e recomendação

**Entrada:** [contrato](2026-11-14-gaiapus-01-produto-contrato.md), arquivos do [pipeline e auditoria](../../../data/README.md#recortes-demonstrativos-de-2024). **Saída:** pacote com cenas, chuva, metadados, versão e evidências de QA aceito pelo backend. Proposta de saída no novo repositório: `datasets/published/<dataset_version>/` com manifesto e ativos. Os brutos ficam separados.

| Fila | Ação | Como comprovar |
|---|---|---|
| P0 · geometria | Fixar polígono e código IBGE. Registrar fonte, caixa, centro e sistema de coordenadas. | Centro e AOI conferidos contra malha; se cruzar células IMERG, registrar que a referência é a célula do centro. |
| P0 · HLS | No Planetary Computer, consultar S30/L30 por AOI e data. Baixar janelas das bandas, Fmask e metadados. Arquivar IDs sem URLs assinadas. Calcular mediana NDVI e compor RGB. | NIR B8A em S30 e B05 em L30, vermelho B04 em ambos. Registrar proporção válida, pelo menos 65% e três datas distintas para o demo; não preencher lacunas. Verificar que o PNG é da cena declarada. |
| P0 · chuva | Normalizar POWER UTC, preservar zeros e lacunas. Cruzar com IMERG Final V07B diário por célula e data; publicar a referência completa. | Guardar valores pareados, lacunas, viés, MAE e divergência acima de 0,02 mm. Rio Verde tem uma divergência documentada; não mascarar o POWER nem usar o histórico de outra célula. |
| P0 · manifesto | Associar ao `field_id` e congelar versão, checksums, janela e nível de validação. | Reproduzir a validação a partir do cache e entregar uma amostra aprovada e uma rejeitada. Dados rejeitados ficam fora do catálogo. |
| P1 · agronomia | Consultar portaria/tabela ZARC de uma cultura em Uberlândia; normalizar safra, código IBGE, ciclo, condição hídrica, classe de solo/AD, risco e decêndio conforme a fonte. | Arquivar PDF/planilha e URL, vigência e filtro aplicado. Testar entrada incompleta, janela incompatível e janela respaldada. Revisar o texto com especialista. |
| P2 · experimento | Após juntar leituras calibradas, testar previsão de umidade volumétrica em 24 h. | Usar persistência e regressão como referências. Testar em período posterior e área não vista, sem usar sinais publicados após o horário da previsão. Avaliar MAE e viés por sítio. |

- [ ] Entregar primeiro Uberlândia; testar a mesma interface em Rio Verde e Barreiras depois. Não estender regra de plantio sem fonte local.
- [ ] As sugestões de rotação são condicionadas ao manejo, histórico e solo informado. NDVI não revela cultura, nutrientes, acidez ou produtividade por si só.
- [ ] Treino experimental precisa de rótulo futuro real e dados de irrigação/manejo; os recortes de 2024 servem para o pipeline. Se faltar target, entregar relatório de suficiência em vez de publicar modelo.
- [ ] Registrar a diferença entre fonte histórica revisada e fonte que estaria disponível em tempo real. Não usar o Final como entrada instantânea em avaliação operacional.

As fontes oficiais, fórmulas, limpeza e recortes estão detalhados no [plano técnico](2026-11-14-gaiapus-execucao-mvp.md#trilha-a--mineração-qualidade-e-ia).
