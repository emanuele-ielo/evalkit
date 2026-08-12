# evalkit — stato al 2026-08-12 (scritto prima di un compact)

Kit di eval **nostro** per gli agenti Wonderful. `wful` è il motore (runna scenari,
legge tracce, fetcha result e activity); tutto il resto è nostro: raccolta,
rubrica a voti 1-5, metriche, dashboard locale.

Prossimo lavoro concordato con Emanuele: **re-vamp UX/UI della dashboard** (§6).

---

## 1. Com'è fatto

```
evalkit/
  evalkit/            package Python (venv locale in .venv, Python 3.14)
    cli.py            doctor | import | run | judge | report | serve
    config.py         evalkit.toml + credenziali per provider (mai loggate)
    wful.py           wrapper allowlisted sul CLI (solo 6 comandi + activities get)
    runner.py         campagne live: eval run per scenario, concorrenza nostra
    importers.py      import di result cached → campagna
    normalize.py      result + trace + activity → AttemptView (il cuore)
    deterministic.py  check meccanici (assertions, tool, provenance letterale)
    judge/            rubric.py (v2, voti 1-5) · llm.py (OpenAI+Anthropic) ·
                      vote.py (mediana) · runner.py (orchestrazione)
    report.py         metriche, confronti, McNemar
    server.py         API JSON + SSE + serve dashboard/dist
  dashboard/          Vite + React 19 + TS, CSS a mano con token (no framework)
  data/               GITIGNORED — dati cliente (campagne, verdetti)
  evalkit.toml        config; nessun segreto
```

Comandi utili:

```bash
cd ~/GIT/wonderful/evalkit
.venv/bin/python -m evalkit.cli doctor
.venv/bin/python -m evalkit.cli report luna-43x3 --scenarios
.venv/bin/python -m evalkit.cli judge <campagna> --rounds 1 --concurrency 128 --rps 40
nohup .venv/bin/python -m evalkit.cli serve --port 4747 &   # dashboard
cd dashboard && pnpm build                                   # dopo modifiche UI
```

Il server è servito su **http://127.0.0.1:4747** e resta su tra le sessioni
(processo detached). `index.html` esce `no-store`, gli asset sono hashati: dopo
`pnpm build` basta un reload normale.

## 2. Configurazione attuale

- **Agente**: `vera-interforze` (`37b7f505-…`), profilo `tim-enterprise-sandbox`,
  workspace personale `2b85609f-…`, repo `~/GIT/wonderful/Vera/agent-vera`.
- **Modello agente**: `gpt-5.6-luna` sul branch `vera-model-luna` (commit
  `7858daa`, spinto, **non mergiato** su main). Main resta su `gpt-5.5`.
  Validazione server-side passata; la traccia conferma che Luna risponde.
- **Giudice**: `claude-sonnet-5` via Anthropic (chiave in
  `wonderful/env_files/controller.env`, letta a runtime). 3 voti, structured
  output **nativi** (`method="json_schema"`; il tool-calling di default di
  LangChain non garantisce lo schema e perde campi), `max_output_tokens=24000`.
- Limiti chiave Anthropic misurati: 10M token input/min, 2M output/min, 20k
  richieste/min → concorrenza 128 gira senza un solo errore.

## 3. Rubrica v2 — voti 1-5

Quattro criteri, ciascuno 1-5 da ogni voto: **grounding** (le affermazioni stanno
nel payload), **completeness** (ha detto i fatti di riferimento), **clauses**
(condizioni obbligatorie), **provenance** (fonti/versioni reali). Scala ancorata
alla conseguenza per l'utente: 5 nessun difetto, 3 difetto reale ma risposta
usabile, 2 inutile o fuorviante, 1 grave (contraddice, inventa, non risponde).

Aggregazione: **mediana** dei 3 voti per criterio (un giudice fuori linea non
sposta il numero), voto dell'attempt = media dei quattro criteri. I check
meccanici bloccanti **cappano** a 2 invece di vetare. Resta un `passed` derivato
(≥4 e nessun criterio <3) solo per confrontarsi col giudice binario di
piattaforma.

**I verdetti v1 (pass/fail) non sono leggibili sulla scala nuova** — un pass/fail
non si converte in voto senza inventare. `store.read_legacy_verdict()` li espone
e la UI lo dice esplicitamente invece di mostrarli come "non giudicati".

## 4. Campagne su disco

| id | agente | rubrica | stato |
|---|---|---|---|
| `baseline-fase5d` | gpt-5.5 (snapshot `42968f53`) | **v1** | 129/129 giudicati pass/fail — da rigiudicare in v2 (~43 $ interi, ~14 $ solo round 1) |
| `luna-43x3` | gpt-5.6-luna (snapshot `17a4aab7`) | **v2** | 129 raccolti, **43 giudicati (solo round 1)**; round 2-3 su disco non giudicati (~28 $) |
| `luna-smoke` | gpt-5.6-luna | v1 | 1 attempt (scenario oracle g020) |

Archivio: `data/_archive/gpt5-partial/` — 62 verdetti parziali col giudice gpt-5,
tenuti per un eventuale confronto fra giudici.

## 5. Numeri e diagnosi (il perché di tutto)

**Baseline gpt-5.5, rubrica v1, Sonnet 5, 129/129**: 7/43 majority (ufficiale
9/43), 23/129 attempt. Per criterio: grounding 74%, provenance 98%, clauses 59%,
**completeness 23%**.

**Luna round 1, rubrica v2, 43 attempt**: **voto 3,70/5**. provenance 4,56 ·
grounding 3,84 · clauses 3,77 · **completeness 2,74**. Distribuzione: 1×5★,
31×4★, 8×3★, 3×2★ — nessun 1★.

**La diagnosi centrale**: Vera non inventa, **risponde a metà**. `partial_answer`
su 40 attempt su 43, `missing_required_fact` 23, e 15 tentativi con
`hedging_without_answer` (si ritira su domande che il corpus copre). Prova
decisiva: la stessa rubrica *senza* il criterio completeness dà 18/43, cioè
esattamente il numero di fase 5e — quel giudice misurava sostanzialmente la
fondatezza. Con solo grounding sarebbe 31/43.

**Luna vs gpt-5.5, confronto gratuito (giudice ufficiale, stessi 129 attempt)**:
9/43 → **5/43** majority, 24/129 → **18/129** attempt. Token quasi identici, ma
Luna costa ~0,37 $ contro ~9,90 $ per 129 conversazioni (**27× meno**). Per dire
*quanto* e *dove* perde sulla scala 1-5 serve la baseline round 1 in v2 (~14 $).

## 6. Re-vamp UX/UI — punto di partenza

**Com'è ora.** Tre viste (`dashboard/src/views/`): `Campaigns.tsx` (griglia di
card), `Campaign.tsx` (stat + criteri + tassonomia + matrice scenari×round),
`Attempt.tsx` (tre pannelli: lista tentativi con `j`/`k` · conversazione con tool
call espandibili args↔payload · inspector a 4 tab verdict/prompt/llm/raw).
Design system in `src/styles.css`: token CSS, light di base e dark ridefinito su
`prefers-color-scheme` + toggle `data-theme`; nessun framework; pill `Score`
colorata verde ≥4 / ambra 3-4 / rosso <3.

**Cosa so già che è debole**, in ordine di quanto mi dà fastidio:

1. La **matrice** è una tabella piatta: non si ordina, non si vede la
   distribuzione dei criteri per riga, e con 43×3 celle il colpo d'occhio manca.
2. L'**inspector** è denso e obbliga a cambiare tab: le accuse dei singoli voti
   stanno dietro un vote picker, e il collegamento claim→evidenza (che funziona)
   non è scopribile.
3. **Manca la vista compare**: stesso scenario su più campagne/round affiancati.
   Era nel brief, non l'ho costruita — ed è esattamente ciò che serve per
   Luna-vs-gpt-5.5.
4. Il **voto non è mai spiegato visivamente**: c'è il numero e la tabella dei
   criteri, ma non un grafico che mostri dove si perde (radar/barre per criterio
   a livello di attempt).
5. La **live feed** è un log grezzo; durante un run non comunica progresso reale.
6. Nessun layout stretto testato; la vista Attempt sotto 1100px degrada.
7. La tassonomia è una lista di barre: non è cliccabile per filtrare la matrice.

Prima di ridisegnare va deciso **qual è la domanda principale** che la dashboard
deve rispondere in tre secondi. Candidate: "questo modello è meglio del
precedente?" (→ compare come vista di primo livello) oppure "dove perde punti
l'agente?" (→ criteri e tassonomia come spina dorsale, matrice secondaria).

## 7. Guardrail

Profilo e workspace **espliciti** su ogni comando; solo agente `vera-interforze`;
MAI workspace General. Piattaforma read-only tranne `eval run` (+`agents
snapshot`), imposto dall'allowlist in `wful.py`. Chiavi lette a runtime, mai
stampate né salvate. `data/` gitignored. Repo evalkit: git **locale**, nessun
remote. Niente merge su main dell'agente senza decisione esplicita.
