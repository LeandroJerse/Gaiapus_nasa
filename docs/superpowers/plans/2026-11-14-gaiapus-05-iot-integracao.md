# Gaiapus 05 — telemetria, integração e aceite

**Entrega:** leitura simulada recebida pela API, exibida com qualidade e profundidade, e validação da primeira versão do produto. Hardware não bloqueia o histórico de satélite.

## IoT em duas etapas

- [ ] **Simulador:** escrever `hardware/simulator/` com envio de uma leitura válida e uma repetida. Payload: `device_id`, `boot_id`, `sequence`, `observed_at` UTC, `depth_cm`, `moisture_m3_m3`, `soil_temp_c`, `battery_mv`, `quality`, `calibration_id`, `firmware_version`. Se não calibrado, umidade convertida nula. O servidor carimba `received_at` e vincula o dispositivo ao recorte. Deduplicar por dispositivo, boot e sequência.
- [ ] **Bancada:** ESP32 em Rust com `esp-hal`/Embassy; começar com umidade/temperatura e bateria. Documentar substrato, profundidade, repetibilidade e procedimento de calibração. Marcar ausência de horário confiável; guardar leitura na fila em falha de rede. Testar reenvio, reinício e fila cheia. Medir cobertura antes de escolher gateway.
- [ ] **Piloto:** instalar um kit em ponto consentido e registrar a zona de manejo. Planejar medição de chuva local separada se o objetivo for avaliar a precisão da estimativa de satélite.

## Testes de integração para marcar em ordem

1. **Dados → API:** disponibilizar Uberlândia com arquivos validados; tentar carregar fixture sem um dia de chuva. Confirmar que a primeira aparece, a segunda é bloqueada e os logs indicam a razão.
2. **API → frontend:** caminhar pelo globo usando zoom e repetir com controles de teclado. Compare data da imagem, três pontos de NDVI e três dias de chuva com a API. Confirmar atribuição e período histórico.
3. **Condições adversas:** testar UF sem pacote e país estrangeiro. Trocar de recorte durante download, desligar WebGL e simular erro 503. Sem reutilizar gráfico da área anterior, o usuário ainda consegue navegar e entende o estado.
4. **Expansão brasileira:** repetir nos recortes publicados de Rio Verde e Barreiras. O catálogo brasileiro pode crescer sem mudar a hierarquia do mapa; o globo fora do Brasil continua em exploração.
5. **P1 · Recomendação:** demonstrar uma janela ZARC com fonte e vigência; omitir classe do solo e conferir que a interface lista a informação pendente.
6. **P1 · IoT:** simular duas leituras válidas e reenvio de uma; duas únicas persistem. Conferir dispositivo não autorizado, relógio inválido e dado desatualizado.

**Registros da entrega:** versão do conjunto de dados, comandos para subir aplicação nova, lista de testes e evidência de cada etapa concluída. O roteiro segue a [sequência do projeto](2026-11-14-gaiapus-00-roteiro.md).
