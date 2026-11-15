# Gaiapus 03 — orientação de frontend

**Entrega:** aplicação web nova, priorizando celular e uso por teclado. O artefato anterior orienta o visual e o fluxo. A implementação proposta é React + TypeScript com MapLibre, começando pelos dados de exemplo do [contrato](2026-11-14-gaiapus-01-produto-contrato.md) e integrando a API depois. O globo usa representação 3D, sem pixel art.

## Telas e estados

| Tela | O que deve ter | Quando fica pronta |
|---|---|---|
| P0 · exploração | Globo 3D, controle de zoom, breadcrumb clicável, seletor para navegação sem mapa. Brasil em destaque; fora do país, exploração. | Os dois modos de navegação chegam ao mesmo estado; voltar limpa o nível inferior e reenquadra. |
| P0 · município e recortes | Nome e UF, mensagem de disponibilidade, lista de recortes publicados, zoom no município quando houver malha. A lista deve funcionar se WebGL não abrir. | Um município sem recorte exibe “Ainda não há análise para esta área”, sem herdar dados da seleção anterior. |
| P0 · análise histórica | Imagem HLS RGB real datada, contorno do recorte, gráfico NDVI e chuva em dois painéis com mesma escala temporal, tabela, seleção de janela e fontes. | Pontos NDVI não são interpolados; chuva em mm/dia tem indicação de escala regional, período histórico e fonte. Não exibir widget “agora” com série histórica. |
| P1 · planejamento | Formulário de cultura/solo/manejo, cartão de opções e motivos, avisos sobre entradas pendentes ou ausência de regra. | A fonte e a safra aparecem ao lado da sugestão; área sem regra nunca herda janela de outro município. |
| P1 · dispositivo | Última leitura de umidade/temperatura com unidade, profundidade, data, qualidade e indicação de calibração. | Sem leitura, relógio inválido e leituras antigas têm mensagens específicas; não mostrar zero por ausência. |

## Sequência de implementação futura

- [ ] Criar `apps/web/src/features/explore/` com seleção hierárquica em IDs e função separada para enquadramento da câmera. Testar o caminho Brasil → MG → Uberlândia → recorte, volta ao país e opção estrangeira. Se malha municipal não carregar, manter lista por nome/ID disponível.
- [ ] Criar `apps/web/src/features/observations/` com imagem, gráfico e tabela para as amostras. Testar NDVI nulo, 0 mm, janela incompleta, indisponibilidade e leitura da data UTC sem deslocar o dia.
- [ ] Criar `apps/web/src/lib/api/` para trocar o adaptador de amostras pelo HTTP. Cancelar requisição obsoleta, descartar respostas de seleção antiga e cachear por recorte, janela e versão. Ao trocar de recorte, ocultar os dados imediatamente. Mostrar carregando, sem análise publicada e erro recuperável separadamente.
- [ ] Validar desktop e viewport estreita, sem rolagem horizontal da página. Componentes interativos devem ter rótulos, foco visível e alternativa textual; respeitar movimento reduzido e evitar depender só da cor.
- [ ] Só depois integrar recomendação e sensor, cada um em um painel próprio. Submeter entradas explicitamente; evitar confundir atualização do sensor com atualização de imagem.

**Aceite visual:** usando só teclado, uma pessoa localiza Uberlândia, encontra a data da imagem e explica a diferença entre NDVI do recorte e chuva da célula. Em GO/BA, onde houver pacote, mudam imagem e séries; em seleção sem pacote, nenhuma delas é exibida.
