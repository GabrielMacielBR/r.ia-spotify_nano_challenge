# Limpeza de dados do dataset Spotify

## Parâmetros de limpeza

A etapa de limpeza foi aplicada para remover registros considerados ruídos, pouco relevantes ou incompatíveis com a análise pretendida. Os critérios utilizados foram:

- Faixas com duração inferior a 1 minuto: 873 faixas encontradas
- Faixas sem BPM (0 BPM): 157 faixas encontradas
- Faixas com speechiness maior que 0,5: 1180 faixas encontradas
- Faixas dos gêneros: comedy, children, opera, gospel, piano, romance, classical, show-tunes, kids, ambient e demais gêneros semelhantes
- Faixas com baixa popularidade (popularity <= 10)
- Faixas com baixa loudness (loudness <= -16)
- Faixas cujo nome contém termos como "rain sounds", "white noise" e outros ruídos sonoros semelhantes
- Remoção de faixas duplicadas, mantendo a versão com maior popularidade para reduzir a influência de compilações genéricas

![Exemplo visual da limpeza aplicada](./assets/image.png)

## Resultado da filtragem

A tabela abaixo resume os resultados obtidos após a aplicação dos filtros:


- `duration_ms < 60000`: 742 faixas removidas
- `tempo == 0`: 145 faixas removidas
- `speechiness > 0.5`: 1117 faixas removidas
- `track_genre in blacklisted_genres`: 9318 faixas removidas
- `popularity <= 10`: 8899 faixas removidas
- `loudness <= -16`: 6762 faixas removidas
- `track_name contains noise keyword`: 369 faixas removidas

### Totais finais

- Faixas duplicadas removidas: 32793
- Total de faixas removidas: 53077
- Total restante: 61103

## Observação

A limpeza reduziu significativamente o volume de registros pouco úteis para a análise, especialmente os relacionados a ruídos acústicos, compilações genéricas e faixas de baixa qualidade ou baixa popularidade.