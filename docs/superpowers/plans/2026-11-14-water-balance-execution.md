# Execução — ET0 e balanço hídrico

Escopo: motor físico experimental, soja/milho 2ª, oito municípios MG/MT; sem estimativa de rendimento, sem recomendação automática de irrigação.

- [x] Testar ET0 contra FAO-56 exemplo 18 e estresse contra exemplo 37.
- [x] Implementar ET0 FAO Penman–Monteith diária, balanço de reservatório e curvas Kc/fases.
- [x] Processar clima pontual v1 completo; não misturar pressão/orvalho pontuais com clima ponderado v2.
- [x] Rodar histórico 2002–2024 e seca 50%/100% em cada fase; sensibilidade CAD/água inicial em 2024.
- [x] Verificar balanço de massa, limites, cenários pareados e comportamento sob seca; revisão independente e documentação.

Decisões: solo é reservatório de capacidade fixa, sem crescimento de raízes; CAD de referência ZARC, não derivada automaticamente de SoilGrids. Kc e p de referência FAO; correspondência de fases ZARC com fases Kc é hipótese, não cultivar calibrado. Plantios fixos são cenários, não datas observadas nem autorização ZARC. História de milho é independente da soja: não presume que milho poderia ser plantado antes da colheita anterior.

## Registro de execução

- Testes inicialmente falharam por ausência do motor; passaram após implementação. Seis testes específicos; suíte completa 25/25.
- Primeira execução real identificou BOM no CSV v1 (`day_utc` com prefixo BOM). Confirmado nos bytes originais; leitura corrigida para utf-8-sig. Reexecução completa passou sem alteração dos dados de origem.
- 70.128 ET0, 4.464 cenários, 17.856 resumos de fase; 524.520 dias verificados. Massa máxima 1,4211e-14 mm.
- Revisão independente pelo agente `review_water_balance`: sem defeitos importantes; verificou fórmulas FAO, seis testes, contagens, IDs únicos, conservação dos totais e hashes de origem. Sem itens menores pendentes.
- Guia 19, README de dados, roteiro operacional e referência ZARC atualizados. Nenhuma publicação externa, alteração de modelos treinados ou alegação de calibração de campo.
