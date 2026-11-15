# Execução da base v2 — chuva, solo e suporte agrícola

Pedido: executar autonomamente validação INMET, investigar lacunas Campo Novo, ponderar clima e retreinar.

- [x] Validar precipitação no ponto da estação e intervalo UTC correto: 12 estações, 2023–2024, calendário completo.
- [x] Ampliar referência temporal de Campo Novo e investigar solo vizinho sem sobrescrever ausências: solo agrícola a 585m; referência de chuva regional, lacuna local preservada.
- [x] Obter máscara anual acessível; calcular área agrícola por célula e normalizar pesos: ESA CCI genérica, 168 recortes, 504 grupos normalizados.
- [x] Coletar clima nas células relevantes, gerar amostras com suporte agrícola e comparar modelos: 610 células, 351 amostras, 42.719 dias de safra.
- [x] Revisar implementação: revisão independente apontou calendário, checagens físicas/hash e features por artefato; corrigidos com testes.
- [x] Congelar manifestos e verificar entrega final: inventário SHA-256, dados/modelos v1 preservados, documentos v1 arquivados, teste de rendimento reservado.

Ruling: preservar `training_v1` e escrever em `training_v2`; comparação é pareada, sem avaliar o teste de rendimento 2022–2024.

Ruling: INMET informa precipitação da hora anterior. Mensagem de 00h pertence ao dia UTC anterior. Não ajustar defasagem para maximizar correlação.

Ruling: MapBiomas URLs oficiais de cobertura col11/col10 e segunda safra retornaram HTTP 403. Uma máscara alternativa de agricultura genérica poderá ser usada como proxy explícito, sem chamá-la de soja ou milho mapeados.

Ruling final: nem solo nem chuva regional substituem observação inexistente no ponto original. A aplicação deve declarar esse limite. O ganho de validação dos modelos é misto; todos continuam pesquisa.

Revisão final independente: nenhum bloqueador remanescente identificado após correções. Gates de qualidade implementados, contrato por artefato conferido, solo agrícola classe11 dentro do município. A máscara continua genérica e as referências alternativas não recuperam observações locais inexistentes.
