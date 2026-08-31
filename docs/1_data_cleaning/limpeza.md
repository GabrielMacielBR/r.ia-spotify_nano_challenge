Parâmetros de limpeza:

- Faixas com menos de 1 min - 873 faixas encontradas
- Faixas sem BPM (0 BPM) - 157 faixas encontradas
- Faixas com speechiness maior que 0.5 - 1180 faixas encontradas
- Faixas do gênero comedy, children, opera, gospel, piano, romance, classical, show-tunes, kids, ambient...
- Faixas com baixa popularidade (<= 10)
- Faixas com baixa loudness (<= -16)
- Faixas com nome "%rain sounds%" "%white noise%", etc...
- Resolver faixas duplicadas mantendo a faixa com maior popularidade para tentar excluir álbuns de compilações genéricas

![alt text](./assets/image.png)

Faixas duplicadas removidas: 32793
duration_ms < 60000: 742 faixas removidas
tempo == 0: 145 faixas removidas
speechiness > 0.5: 1117 faixas removidas
track_genre in blacklisted_genres: 9318 faixas removidas
popularity <= 10: 8899 faixas removidas
loudness <= -16: 6762 faixas removidas
track_name contains noise keyword: 369 faixas removidas

Total de faixas removidas: 20104
Total restante: 61103