# Nano Challenge: Insights e Curadoria Acústica com Spotify

Este repositório reúne o projeto desenvolvido pelo **Time 6** para o Nano Challenge da Residência em Inteligência Artificial do **Instituto ELDORADO**, em parceria com a **UnB** e o **Lab Livre**.

O trabalho investiga como transformar atributos musicais em evidências acionáveis para negócios, culminando na criação do **Retail Sound**, uma plataforma inteligente de sonorização de ambientes comerciais.

---

## Como Executar a Aplicação (Quick Start)

Para executar localmente a aplicação interativa de recomendação e curadoria sonora:

### 1. Clonar e Acessar o Diretório
```bash
git clone https://github.com/GabrielMacielBR/r.ia-spotify_nano_challenge.git
cd r.ia-spotify_nano_challenge
```

### 2. Configurar o Ambiente Virtual

No **Linux** ou **macOS**:
```bash
python3 -m venv .venv
source .venv/bin/activate
```

No **Windows** (PowerShell):
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 3. Instalar as Dependências
```bash
pip install -r requirements.txt
```

### 4. Iniciar a Aplicação Streamlit
```bash
streamlit run apps/consulta_sql_dataset_streamlit.py
```
Acesse a aplicação no navegador em `http://localhost:8501`.

---

## Notebooks do Projeto

O desenvolvimento analítico e os experimentos de inteligência artificial estão organizados em três cadernos principais no diretório `notebooks/`:

### 1. [`cleaning_dataset.ipynb`](../notebooks/cleaning_dataset.ipynb) — Higienização e Sanidade dos Dados
- **Objetivo**: Tratar e refinar o catálogo bruto de 114.000 faixas para 79.178 faixas de áudio limpas e consistentes.
- **Destaques**:
  - Desduplicação inteligente com chave composta `track_name | artists`, preservando a versão de maior popularidade.
  - Eliminação de anomalias técnicas: faixas com duração inferior a 60 segundos e registros com andamento nulo (`tempo == 0`).
  - Purificação de conteúdo: expurgo de faixas faladas (`speechiness > 0.66`) e ruídos de relaxamento/sono (*rain sounds*, *white noise*, etc.).
  - Exportação do catálogo consolidado em `dataset/dataset_cleaned.csv`.

### 2. [`cluster_analysis.ipynb`](../notebooks/cluster_analysis.ipynb) — Modelagem Não Supervisionada (K-Means)
- **Objetivo**: Descobrir e mapear atmosferas sonoras previsíveis a partir de 4 dimensões acústicas essenciais (`energy`, `acousticness`, `valence`, `instrumentalness`).
- **Destaques**:
  - Padronização estatística com `StandardScaler` ($Z$-score).
  - Determinação da quantidade ótima de grupos pelo Método do Cotovelo e Coeficiente de Silhueta (pico de separação em $K = 5$, $\text{Score} \approx 0{,}351$).
  - Caracterização dos 5 clusters comerciais (Social & Ensolarado, Ambiental & Relaxante, Acústico & Intimista, Eletrônico & Moderno, Intenso & Dinâmico).
  - Projeção e validação dimensional 2D via PCA.

### 3. [`sonora.ipynb`](../notebooks/sonora.ipynb) — Jornada Experimental e Motor Sonora (CBL)
- **Objetivo**: Documentar a trajetória investigativa completa sob a metodologia *Challenge Based Learning*.
- **Destaques**:
  - Análise exploratória aprofundada das correlações e distribuições sonoras.
  - Teste e falseamento da hipótese de previsão direta de popularidade via áudio ($R^2$ próximo a 0), demonstrando que o sucesso comercial depende de variáveis externas de marketing.
  - Concepção do motor de recomendação em dois estágios: afinidade acústica exponencial vetorial, regra comportamental de andamento de Milliman (1982) e ordenação harmônica pela Roda de Camelot.

---

## Documentação do Projeto

Navegue pelos módulos completos da documentação:

1. [**Guiding Questions**](0_guiding_questions/guiding_questions.md): Questões norteadoras fundamentais (v1 e v2.0), taxonomia das variáveis, curva da festa e princípios de transição do DJ.
2. [**1. Limpeza de Dados**](1_data_cleaning/limpeza.md): Métricas do funil de higienização, justificativas técnicas e distribuição acústica.
3. [**2. Agrupamento (Clustering)**](2_clustering/clustering.md): Metodologia K-Means, centróides dos 5 perfis e projeção visual PCA.
4. [**3. Aplicação Retail Sound**](3_aplicacao/aplicacao.md): Arquitetura do motor em dois estágios, regra de Milliman, player com streaming progressivo e guia operacional.
5. [**4. Apresentação Final**](4_apresentacao/apresentacao.md): Slides da apresentação executiva em PDF incorporado, capturas de tela com zoom e síntese da proposta de valor.

---

## Estrutura do Repositório

```text
├── dataset/             # Datasets originais, limpos e com clusters gerados
├── notebooks/           # Notebooks Jupyter (limpeza, clustering e jornada Sonora)
├── apps/                # Aplicação interativa Streamlit (Retail Sound)
├── docs/                # Documentação técnica e visual para o GitHub Pages
├── requirements.txt     # Especificação de dependências do ambiente Python
└── zensical.toml        # Configuração do gerador de documentação estática
```

---

## Participantes (Time 6)

| Aluno |
| --- |
| Gabriel Maciel Araújo |
| João Vítor Carvalho Barbosa |
| Keila Alves Ferreira |
| Mahiaara Amanda Pereira Barros |
| Matheus Henrique Picone Rosa |

---

## Materiais e Referências

- **Linguagem e Ferramentas**: Python 3.10+, Jupyter, Streamlit, SQLite, Scikit-learn, Pandas, NumPy.
- **Base de Dados**: [Spotify 114k Tracks Dataset (Kaggle)](https://www.kaggle.com/datasets/maharshipandya/-spotify-tracks-dataset)
- **Fundamentação Comportamental**: Milliman, R. E. (1982). *Using Background Music to Affect the Behavior of Supermarket Shoppers*. Journal of Marketing.