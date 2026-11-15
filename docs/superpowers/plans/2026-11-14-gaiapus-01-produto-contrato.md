# Gaiapus 01 — produto, fronteiras e contrato

**Entrega:** uma definição de fluxo que permita implementar a interface e a API separadamente. **Artefatos futuros:** `contracts/openapi.yaml`, `contracts/examples/` e um README com comandos de validação.

## Fluxo e recorte do primeiro incremento

1. A pessoa abre um globo 3D, seleciona o Brasil, uma UF e um município quando houver malha disponível. A câmera enquadra a seleção a cada passo; o caminho aparece acima do mapa.
2. O município lista recortes publicados. Na falta deles, informa que ainda não há análise. O clique em país estrangeiro aproxima o mapa e mostra “Exploração” sem oferecer análise.
3. Ao escolher um recorte publicado, vê a cena datada, a qualidade do NDVI e a chuva histórica regional. Há alternativa com seletor e tabela.
4. Em um incremento posterior, a pessoa preenche dados de cultivo e solo, consulta a explicação da janela e pode ver a última leitura de um sensor vinculado.

O limite municipal serve à navegação. Resolução e qualidade dos satélites determinam se um **recorte**, dentro ou fora do centro urbano, é publicável. Não prometer cobertura de cidades inteiras ou delinear propriedade a partir da imagem.

## Contrato que as duas pontas compartilham

| Rota futura | Entrada | Saída e comportamento |
|---|---|---|
| `GET /v1/availability` | `country` ISO alfa-2, `uf` e `municipality_code` opcionais, como strings | `supported_country` e `fields[]`, cada item com `field_id`, nome, versão, período, limites e geometria simplificada. Área consultável sem pacote: 200 e lista vazia. |
| `GET /v1/fields/{id}/observations` | `from` e `to` UTC opcionais, inclusivos | `schema_version`, `dataset_version`, `field`, `image`, `observations[]`, `rainfall[]`, `validation` e `sources`. ID não publicado: 404. Parâmetro inválido: 422. Janela sem observações: séries vazias e imagem nula, sem trocar de área. |
| `POST /v1/fields/{id}/recommendations` | Safra, cultura, solo/AD, ciclo, modo hídrico, data, histórico de plantio | `ready`, `needs_input` ou `not_available`, com `missing_fields`, `options`, evidências e `rule_version` quando aplicável. |
| `POST /v1/telemetry` | Payload autenticado do [guia IoT](2026-11-14-gaiapus-05-iot-integracao.md) | Confirmação de aceite ou de duplicata. Sensor é vinculado no servidor. |

- Coordenadas sempre longitude, latitude em WGS84. `bbox` é `[west,south,east,north]`; os IDs administrativos não são nomes.
- `observations[]`: `scene_id`, `date`, `ndvi` (número ou nulo), `valid_fraction`, `reason` opcional. `rainfall[]`: `date`, `rain_mm` em mm por dia UTC. No pacote publicado a chuva diária V07B é completa; zero é medição estimada de ausência de chuva, nulo é lacuna.
- `image`: URL, `scene_id`, data UTC, coleção, quatro cantos e atribuição. O scene ID deve corresponder a uma observação elegível da seleção; se o filtro de período excluir a cena, retornar outra cena elegível disponível ou imagem nula.
- `validation`: nível de publicação, versão do método e escopo. “Conferido com produto de referência” não significa validado com medição de campo.

## Antes de dividir o trabalho

- [ ] Criar exemplos: Uberlândia publicável, UF sem pacote e pacote rejeitado por dia de chuva faltante. O rejeitado é fixture de backend; não deve ser uma resposta de análise.
- [ ] Validar exemplos contra esquema: dia inexistente, NDVI fora de faixa, ID desconhecido e ausência de fonte devem falhar.
- [ ] Decidir um prefixo de API e apontá-lo pela configuração. Aplicação usa amostras no desenvolvimento, e deixa a origem claramente marcada.
- [ ] Anotar quais escopos entram primeiro: histórico P0, ZARC P1, telemetria P1, ML experimental P2.
