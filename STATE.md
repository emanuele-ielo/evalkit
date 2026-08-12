# evalkit — stato al 2026-08-12 (scritto prima di un compact)

Kit di eval **nostro** per gli agenti Wonderful. `wful` è il motore (runna scenari,
legge tracce, fetcha result e activity); tutto il resto è nostro: raccolta,
rubrica a voti 1-5, metriche, dashboard locale.

Ultimo lavoro: **revamp UX/UI della dashboard** sul design system Wonderful (§6).
Aperto: la vista **compare** fra campagne (§8) e i giudizi opzionali (§4).

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
| `baseline-fase5d` | gpt-5.5 (snapshot `42968f53`) | **v1** | 129 verdetti pass/fail su disco, illeggibili sulla scala v2: il report li conta come **non giudicati** e la UI li mostra come legacy. Da rigiudicare (~43 $ interi, ~14 $ solo round 1) |
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

## 6. UX/UI — rifatta sul design system Wonderful (2026-08-12)

Base: il mockup `Eval dashboard UI redesign.zip` fornito da Emanuele. Design
system = quello aziendale approvato (`--w-*`), riportato a scala app in
`dashboard/src/styles.css`. **Dark è la versione canonica** (la palette è bianco
in alpha su nero) ed è il default; light rimappa gli stessi ruoli a nero in alpha
su bianco, `system` segue l'OS. Font ABC Favorit Light + Inter variable
bundlati in `dashboard/public/fonts/` (nessuna richiesta esterna).

**Cosa risponde adesso la dashboard**: *dove perde punti l'agente*. I criteri e
la tassonomia sono la spina dorsale; il compare fra campagne resta l'unico pezzo
del brief non costruito (§8).

Nuovi moduli:

- `components/json.tsx` — rende **qualsiasi** payload di tool come lettura, non
  come dump JSON. Non sa nulla del corpus Vera: deduce dai valori (stringa corta
  → badge, prima stringa media → titolo, stringhe lunghe → prosa, array di
  primitivi → chip, array di oggetti → card, mappe di booleani → flag, catene a
  chiave singola tipo `source › version` → una riga). Cap a 5 righe con
  espansione, che si apre da sola se l'evidenza cercata sta in una riga nascosta.
- `components/md.tsx` — markdown minimo (grassetto, `code`, «», liste). Prima le
  risposte mostravano i `**` grezzi. I `**` devono essere esattamente due, o le
  maschere tipo `**334**********` mandano in grassetto mezza frase.
- `lib/lede.ts` — da un verdetto ricava **una diagnosi in una riga** ("HALF AN
  ANSWER — The payload answered it, the answer did not"). Ogni titolo è vincolato
  alla condizione che descrive; la prosa sotto è sempre la spiegazione del
  giudice. Nessun testo inventato.

Le sette debolezze di partenza: risolte 1 (tabella ordinabile, round come pill
colorate), 2 (lede + claim cliccabili con hint visibile, riga del payload
cerchiata e marcata "judge quoted this row"), 4 (barre per criterio con tono
colore), 5 (feed dentro "Run internals" richiudibile), 6 (sotto 980px impila,
nessun overflow orizzontale), 7 (tassonomia cliccabile che filtra la tabella).
Resta aperta la 3 (compare).

**Un bug di onestà trovato durante il revamp**: le campagne giudicate con rubrica
v1 mostravano `0.00 / 5`, perché `mean_score` è un default pydantic e non una
misura. Ora c'è `hasScores()` in `components/bits.tsx` — una media su scala 1-5
non può valere 0 — e quelle campagne mostrano "—" più la pass-rate reale e il
comando per rigiudicare.

## 7. Guardrail

Profilo e workspace **espliciti** su ogni comando; solo agente `vera-interforze`;
MAI workspace General. Piattaforma read-only tranne `eval run` (+`agents
snapshot`), imposto dall'allowlist in `wful.py`. Chiavi lette a runtime, mai
stampate né salvate. `data/` gitignored. Repo evalkit: git **locale**, nessun
remote. Niente merge su main dell'agente senza decisione esplicita.

## 8. Cosa manca

- **Vista compare**: stesso scenario su più campagne affiancate. È ciò che serve
  per chiudere Luna-vs-gpt-5.5 sulla scala 1-5. L'API `/api/compare?base=&other=`
  esiste già lato server e non è ancora usata dalla UI; la sezione "Where we
  disagree with the platform judge" è l'unico confronto presente.
- **Giudizi non ancora fatti** (nessuno lanciato senza ok esplicito): baseline
  round 1 in v2 (~14 $) per confrontare Luna e gpt-5.5 a voti; Luna round 2-3
  (~28 $) per la flakiness; baseline intera in v2 (~43 $).
- **Rubrica**: catturare le citazioni di *nomi di tool interni* come fonte (Luna
  ha citato "lookup canale Interforze"; il giudice di piattaforma lo prende, noi
  no). Non implementato per non rompere la comparabilità a metà misura.
