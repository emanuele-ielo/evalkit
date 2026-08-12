# evalkit + agent-vera — stato al 2026-08-12 sera (handover)

Kit di eval **nostro** per gli agenti Wonderful. `wful` è il motore (runna scenari,
legge tracce, fetcha result e activity); tutto il resto è nostro: raccolta,
rubrica a voti 1-5, metriche, dashboard locale.

## Aggiornamento 2026-08-12 — Terra + customer care v4

Questa sezione sostituisce le decisioni v3/Luna sotto per il lavoro futuro; il resto del file resta come
storia degli esperimenti già eseguiti.

- L'agente passa a **`gpt-5.6-terra`** sul branch `vera-model-terra`. Luna e le campagne v2/v3 restano
  baseline storiche: non confrontare numericamente i loro punteggi con v4.
- **LOB e provenienza non sono più customer-facing:** `source*` e `needs_lob_validation` non arrivano più
  dai tool; prompt e rubrica vietano fonti, versioni, documenti, KB e formule generiche di conferma TIM.
- La rubrica **v4** misura grounding, risoluzione/completezza, condizioni decisionali e tono customer care.
  I payload e i gold storici vengono proiettati senza LOB/provenienza; i verdict v2/v3 restano leggibili.
- Il bridge binario non applica più un veto a un singolo criterio da 2/5: passa con media almeno 4/5,
  nessun blocco deterministico e nessun criterio catastrofico da 1/5. Il 2/5 resta visibile nel report.
- Le richieste senza uno slot che cambia materialmente la risposta devono produrre **una domanda breve,
  senza tool**. La ricerca parte al turno successivo usando intento + oggetto + vincoli espliciti.
- Nuovo batch iniziale `v4_customer_care`: `g057` (canale scritto → chiarire lo scopo → reclamo) e `g032`
  (privatizzazione → chiarire tipo linea/stato → percorso). Nel secondo scenario il massimo di **4 SIM
  prepagate Consumer per codice fiscale** resta una condizione obbligatoria.
- Il lessicale di `search_vera` ora verifica i confini di parola, non considera confident una keyword
  ordinaria isolata e non lascia che il rumore lessicale blocchi il retry semantico; FAQ, KB e probe dei
  due corpus girano in parallelo.

I 43 scenari `gold_v2_*` e i 27 draft v3 non vanno riscritti in-place: hanno significato storico. La
migrazione completa richiede nuovi slug v4 con obblighi tipizzati (`metadata.evalkit_v4`) e traiettorie
multi-turn per le famiglie ambigue.

Oggi due filoni **paralleli** (sessioni diverse), entrambi chiusi:
1. **Revamp UX/UI della dashboard** sul design system Wonderful (§6, intatto da quella sessione).
2. **Overhaul dell'eval**: diagnosi completa di `luna-43x3` (5 agenti di analisi), fix di
   tool + skill + rubrica (v3), A/B misurato, batch eval nuovo in bozza (§0, §5, §7-§12).

---

## 0. TL;DR per chi arriva adesso

1. **Il giudice di piattaforma è ritirato.** Non vede l'output dei tool (`saw_tool_output=False`
   43/43; ~2,5k token in input contro i ~52k del nostro): 67 delle sue 72 accuse verificabili
   erano false — confrontava col gold e chiamava "inventato" ciò che stava nel payload.
   I suoi numeri non vanno mai più citati. Giudice di riferimento: il nostro, **rubrica v3**.
2. **Tool e skill riparati e shippati** (§5.2). Effetto misurato: recall righe gold 53%→61%,
   invenzioni dimezzate, zero attempt senza tool call, arm lessicale attivo 1/44→52/52 chiamate.
3. **Ma il voto non si è mosso** (3,38→3,37 in v3; senza cap disclaimer 3,60→3,63, §7).
   Diagnosi finale: il vincolo residuo è la **capacità del modello** (gpt-5.6-luna) di eseguire
   il workflow — lo adotta a metà (1,21 ricerche/attempt) e continua a scartare fatti che ha
   nel payload. Non più harness, non più dati, non più prompt.
4. **Prossimo esperimento proposto (NON ancora approvato):** stesso A/B su gpt-5.5
   (~3-4$ run + ~14$ giudice) per capire se il tetto è Luna. Budget giudice: **spesi ~29$ dei 42$**.
5. **Batch eval nuovo:** 27 bozze in `~/GIT/wonderful/Vera/fase4-batch-v3/scenarios-draft/`,
   gold `DRAFT-pending-live-derivation`. Decisione di Emanuele: **niente scenari condizionali
   ("se ricaricabile…; se abbonamento…") né boundary (dati linea) finché non ci sono le API TIM.**

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
    deterministic.py  check meccanici (assertions, tool_call_present, must_not_contain,
                      lob_disclaimer, pcv_no_contact — v3)
    judge/            rubric.py (v3, voti 1-5) · llm.py (OpenAI+Anthropic) ·
                      vote.py (mediana) · runner.py (orchestrazione)
    report.py         metriche, confronti, McNemar
    server.py         API JSON + SSE + serve dashboard/dist
  dashboard/          Vite + React 19 + TS, CSS a mano con token (design system Wonderful, §6)
  data/               GITIGNORED — dati cliente (campagne, verdetti)
  evalkit.toml        config; nessun segreto
```

Comandi utili:

```bash
cd ~/GIT/wonderful/evalkit
.venv/bin/python -m evalkit.cli doctor
.venv/bin/python -m evalkit.cli report luna2-43x3 --scenarios
.venv/bin/python -m evalkit.cli run --agent vera --rounds 3 --snapshot-id <id> \
    --campaign <id> --no-judge          # raccolta; --resume <id> riprende; concurrency 12 ok
.venv/bin/python -m evalkit.cli judge <campagna> --rounds 1 --concurrency 128 --rps 40  # --force rigiudica
nohup .venv/bin/python -m evalkit.cli serve --port 4747 &   # dashboard
cd dashboard && pnpm build                                   # dopo modifiche UI
```

Il server è su **http://127.0.0.1:4747** e resta su tra le sessioni (processo detached).
`index.html` esce `no-store`, asset hashati: dopo `pnpm build` basta un reload.

## 2. Configurazione attuale

- **Agente**: `vera-interforze` (`37b7f505-…`), profilo `tim-enterprise-sandbox`,
  workspace personale `2b85609f-…`, repo `~/GIT/wonderful/Vera/agent-vera`.
- **Modello agente**: `gpt-5.6-luna` sul branch `vera-model-luna` (ora a `09d9196` =
  merge di main coi fix tool/skill). Main resta su `gpt-5.5` (a `242137f`).
  **Mai mergiare luna su main senza decisione esplicita.**
- **Snapshot utili**: `7b1ef3e0` (main + tool fix), **`791dda16` (luna + tutti i fix —
  usato per `luna2-43x3`)**.
- **Giudice**: `claude-sonnet-5` via Anthropic (chiave in `wonderful/env_files/controller.env`,
  letta a runtime). 3 voti, structured output **nativi** (`method="json_schema"`),
  `max_output_tokens=24000`. Limiti misurati: 10M in/min, 2M out/min, 20k req/min →
  concorrenza 128 senza errori. Un judge di 43 attempt ≈ 13-15$.
- Concorrenza di **raccolta** (`eval run`): 4 nel toml; **12 testato pulito** (129 attempt,
  0 errori, ~2× più veloce).

## 3. Rubrica v3 — voti 1-5 (commit evalkit `7c6f648`)

Quattro criteri, mediana di 3 voti ciascuno, voto attempt = media dei quattro; i check
meccanici bloccanti **cappano a 2**. In v3 il bridge era `passed` = ≥4 e nessun
criterio <3; la policy corrente v4, descritta sopra, ha abbassato quel floor a 2.

- **grounding** (invariato): le affermazioni stanno nel payload.
- **completeness**: ora **proporzionale** — FULL=1, PARTIAL=0,5 sui fatti non-meta;
  4=copertura ≥80% + il fatto direttamente chiesto presente; 3=≥50%+chiesto; 2=chiesto
  MISS o <50%. Frasi di puro comportamento marcate `meta:true` ed escluse dal conteggio.
  (Il cap-a-3 della v2 schiacciava coperture dal 25% all'83% sullo stesso voto.)
- **clauses**: solo caveat di prosa, `evidence_locator` obbligatorio per ogni voce,
  divieto esplicito di doppio conteggio con completeness, scala ancorata ai conteggi.
  (In v2 era il criterio più rumoroso: unanimità 35%.)
- **provenance** (invariato).
- **deterministic v3**: `tool_call_present` e `must_not_contain` (bloccanti, invariati);
  nuovi bloccanti **`lob_disclaimer`** (righe con `needs_lob_validation` nel payload →
  variante di «da confermare/verificare con TIM» nella risposta) e **`pcv_no_contact`**
  (righe con `pending_channel_validation` → nessun numero/PEC/email; pattern validati sui
  43 reali, zero falsi positivi su prezzi/date/versioni). **`provenance_literals` RIMOSSO**
  (falso positivo su g089, muto su 6/43: quel check lo fa meglio il giudice che vede i payload).
- Chiavi JSON invariate (la dashboard le consuma). Verdetti v2 ancora leggibili.
  `meta:true`/`asked:true`/`loc:` viaggiano come prefissi del campo `note` (schemas.py era
  fuori perimetro): da promuovere a campi tipizzati alla prossima occasione.
- I verdetti **v1** (pass/fail) restano illeggibili sulla scala: `store.read_legacy_verdict()`
  li espone e la UI li mostra come legacy.

## 4. Campagne su disco

| id | agente | rubrica | stato |
|---|---|---|---|
| `baseline-fase5d` | gpt-5.5 (snapshot `42968f53`) | v1 | 129 verdetti legacy pass/fail; UI li mostra come legacy |
| `luna-43x3` | luna pre-fix (snapshot `17a4aab7`) | v2 | round 1 giudicato (15/43, 3,70) — **INTATTA, baseline v2** |
| `luna43x3-v3rejudge` | clone della precedente | **v3** | round 1 rigiudicato: **3,38, pass 11/43** |
| `luna2-43x3` | luna + tool + skill (snapshot `791dda16`) | **v3** | 129/129 raccolti, round 1 giudicato: **3,37, pass 9/43**; round 2-3 su disco non giudicati (~28$) |
| `luna-smoke` | luna | v1 | 1 attempt |

Archivio: `data/_archive/gpt5-partial/` — 62 verdetti parziali col giudice gpt-5.

## 5. La sessione di overhaul (diagnosi → fix → misura)

### 5.1 Diagnosi di luna-43x3 (5 agenti, mattina) — i verdetti che reggono

- **M2 Sintesi**: Vera usava mediana **3 righe su 20**; il 52% dei fatti recuperati non
  arrivava in risposta; drop cresce col rango (33% a 0-2 → 65% a 10-19); seconda ricerca
  in 2/43. Espandere la query >2× il messaggio dimezza il recall (≤1,3× → 65%, >2× → 50%).
- **M3 Dati**: **corpus completo** (tutte le righe gold esistono) ma 47% delle righe che
  rispondono non arrivava al modello. Arm lessicale trigram matematicamente morto sui campi
  lunghi (0 gold hit in 43/44 chiamate su `body`: la similarity simmetrica si diluisce con
  la lunghezza — un codice di 14 char in un body di 600 satura a ~0,1). `lookup_tim_info`
  `found:0` nel 72% (95% su `kind:offer`: match substring dell'intera query su `offer_name`).
  Il rango NON è il problema; payload più grandi correlano meglio (r=+0,41): mai tagliare il cap.
- **M4 Flag**: contratto flag rispettato (0 leak contatti); `clauses` v2 misurava caveat
  mai chiesti nel prompt (96% delle assenze non c'entrava coi flag).
- **M5 Gold**: 9/43 scenari con gold rotto (whitelist versioni stantie), ma il nostro
  giudice li assorbe (0,01 sul voto). **Retrieval 100% deterministico** (query identica →
  righe identiche): la varianza fra round è tutta riformulazione del modello.
- **Pattern/skill**: skill caricata correttamente (verificato 129/129). Il tipo di
  richiesta è conoscibile solo DOPO la prima ricerca (msg mediano 50 char, 21/43 ambigui)
  → una skill sola, loop data-driven, niente router per tipo.
- **Domanda reale** (2.196 msg): data/topup 19,9%, roaming 14,9%, offerte 9,4%; il 34% è
  dato-live/handoff che il KB non serve. Fatti solo-tipizzati: zone roaming non-UE 88%,
  intl calling 89%, coverage opzioni 63%, sospensioni canale 4/6 senza traccia in prosa;
  ~5,4% dei messaggi reali richiede un fatto solo-tipizzato.

### 5.2 Cosa è stato shippato

**agent-vera, main (pushato):**
- `062d9b2` — `search_vera`: arm lessicale = **probe `contains`** (max 3: frase intera se
  query ≤3 parole, poi token salienti) su colonna derivata **`search_text`** normalizzata
  (`normalizeSearchText` in `search.ts` = fonte di verità, specchiata in `load/build.py` e
  nel backfill). Score 0,35–0,65 per frazione di probe matchate; match pieno → confident.
  Trigram eliminato. Degrada a semantic-only se la colonna manca.
- `242137f` — leak `internal_ref` rimosso (i `console.log` dei tool FINISCONO nel tool
  result visibile al modello; il log `hits` con id/score è TENUTO: è la strumentazione
  recall); `FAQ_TOP_K` 5→8, `KB_TOP_K` 15→20; dedupe cross-tabella Jaccard ≥0,5 (`deduped:N`
  nel log); **`skills/vera-data/prompt.md` riscritto**: prosa-prima con 3 eccezioni
  lookup-first (paese estero / codice stato pratica / nome esatto offerta), prima query =
  parole del cliente ≤1,5×, ricerche successive SOLO col vocabolario scoperto, stop sulla
  copertura o su nessuna-riga-nuova con rete 5-6, usa tutto il payload coi nomi esatti,
  rispondi per casi, completezza-prima-della-brevità, lookup dopo la prosa con fallback a
  search_vera su `found:0`. `base.md` NON toccato (decisione esplicita).

**Piattaforma (workspace personale):** colonna `search_text` (string) su `vera_kb_units` e
`vera_faq` via `wful tables update` + backfill **705/705** righe (script idempotente con
ledger: `~/GIT/wonderful/Vera/fase1-lexical/backfill_search_text.py`). `load/build.py`
genera la colonna nei reload futuri.

**evalkit:** rubrica v3 (§3), commit locale `7c6f648`.

### 5.3 Il metodo che ha retto

Ogni fix è stato **misurato prima di essere applicato**: bench pg_trgm replicato in Python
sulle 44 query reali (ha ucciso l'ipotesi "colonna derivata per il trigram": 1/64 gold a
soglia 0,3 — la diluizione è matematica; ha promosso `contains`); ricalcolo offline della
scala completeness sui 43 verdetti esistenti (gratis); verifica della lotteria disclaimer
su tutti i 6 round (gratis, senza giudice).

## 6. UX/UI — rifatta sul design system Wonderful (2026-08-12, sessione parallela)

Base: il mockup `Eval dashboard UI redesign.zip` fornito da Emanuele. Design
system = quello aziendale approvato (`--w-*`), riportato a scala app in
`dashboard/src/styles.css`. **Dark è la versione canonica** (la palette è bianco
in alpha su nero) ed è il default; light rimappa gli stessi ruoli a nero in alpha
su bianco, `system` segue l'OS. Font ABC Favorit Light + Inter variable
bundlati in `dashboard/public/fonts/` (nessuna richiesta esterna).

**Cosa risponde adesso la dashboard**: *dove perde punti l'agente*. I criteri e
la tassonomia sono la spina dorsale; il compare fra campagne resta l'unico pezzo
del brief non costruito.

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

Nota post-merge: `lib/lede.ts` conserva la voce `provenance_literals` per rendere
i verdetti v2 su disco — va tenuta finché si mostrano campagne v2.

## 7. A/B misurato (v3, round 1, stessi 43 scenari, stesso metro)

| | prima (pre-fix) | dopo (tool + skill) |
|---|---|---|
| voto | 3,38 | **3,37** |
| voto senza cap disclaimer | 3,60 | **3,63** |
| pass | 11/43 | 9/43 |
| completeness | 2,55 | 2,65 |
| grounding | 3,74 | 3,84 |
| recall righe gold | 53% | **61%** |
| `unsupported_claim` | 10 | **5** |
| `invented_provenance` | 1 | **0** |
| attempt senza tool call | 1 | **0** |
| ricerche/attempt | 1,02 | 1,21 |

Delta appaiato: 19 su, 19 giù, 5 fermi. **Lettura**: il retrieval è riparato e le
invenzioni dimezzate, ma la sintesi resta il vincolo — e ora è attribuibile alla capacità
del modello, non a harness/dati/prompt.

**Lotteria del disclaimer** (verificata su tutti i 6 round, gratis): 4-6 attempt per round
omettono «dato da confermare con TIM» con flag presente, e sono scenari DIVERSI a ogni
round (9-11 incoerenti per campagna). Il cap a 2,0 inietta ±0,25 di rumore sul voto medio.
Se TIM lo vuole sempre → **post-processor deterministico nel runtime** (decisione di
architettura, di Emanuele), non altro prompt.

## 8. Batch eval nuovo (bozze)

`~/GIT/wonderful/Vera/fase4-batch-v3/scenarios-draft/` — **27 scenari** + INDEX.md +
README-roaming.md: 8 composti (prosa+lookup: Albania/TIM PASS, prezzo e validità
TIM4YOU200, privatizzazione quiescenza sospesa, fattura via mail, Giappone/Maldive/Kenya —
verificati NON condizionali), 14 prosa pesati sulla domanda reale, 5 roaming non
condizionali. NON stanno nel repo agente: **il pre-receive della piattaforma rifiuta path
non standard sotto `evals/`** (entreranno in `evals/scenarios/` da finalizzati).

Regole di autoring incorporate (lezioni del batch vecchio): gold derivato da run LIVE (mai
snapshot congelati); provenienza GENERATIVA («qualunque `source.version` nel payload di
questo run», mai whitelist); clausole flag condizionate alla presenza nel run;
`must_not_contain` solo token raggiungibili (`internal_ref`, `.pdf`, `800.191.101`,
`chunk_id`); niente mock; niente tool call obbligate a vuoto.
**gold_status=DRAFT: derivare live prima di ogni congelamento.**

Totale scenari a regime: 43 vecchi (regressione/A-B) + 27 nuovi = **70**.

## 9. Decisioni prese da Emanuele (vincolanti)

1. Giudice piattaforma ignorato nelle nuove eval.
2. Niente check deterministico sulla provenienza (lo fa l'LLM); `tool_call_present` e
   `must_not_contain` restano.
3. Workflow di retrieval nella SKILL, `base.md` resta base. Nessun tetto rigido alle
   ricerche; stop sulla copertura.
4. Una skill sola, niente router per tipo di richiesta.
5. Colonna derivata sì, dove migliora accuracy misurata.
6. **Niente scenari condizionali né boundary finché non ci sono le API TIM** (tagliati
   c05, c06, b01-b04). I 5 roaming non condizionali approvati.
7. Merge su main dell'agent repo ok per i tool (fatto). Modello luna resta su branch.
8. Budget giudice ~42$: spesi ~29$.
9. Eval "intera subito", niente spezzatino.

## 10. Prossimi passi (in ordine)

1. **A/B su gpt-5.5** (proposto, in attesa di ok): run 43×1 su snapshot `7b1ef3e0`,
   judge v3 (~14$ → totale ~46$, sopra il budget: chiedere). Decide se il tetto è Luna.
2. **Disclaimer post-processor** nel runtime (decisione di architettura, di Emanuele).
3. **Fix matching `lookup_tim_info`** (token-OR + lista nomi disponibili su `found:0`):
   NON ancora fatto; non sposta i 43 (gold tutto in prosa) ma serve al batch nuovo e alla
   produzione (72% chiamate a vuoto, 5,4% dei messaggi reali non rispondibili).
4. **Derivazione live del gold** per le 27 bozze → poi `evals/scenarios/` + batch slug.
5. **Vista compare** nella dashboard (API `/api/compare?base=&other=` già esistente lato
   server, mai usata dalla UI): ora ci sono due campagne v3 da affiancare.
6. Giudicare round 2-3 di `luna2-43x3` (~28$) solo se serve stringere la varianza.
7. Rubrica: catturare citazioni di *nomi di tool interni* come fonte (Luna ha citato
   "lookup canale Interforze") — non implementato per non rompere la comparabilità.
8. Promuovere `meta`/`asked`/`evidence_locator` a campi tipizzati in schemas.py.

## 11. Bug dati trovati e NON sistemati (per il giro corpus)

- 22/57 righe `pending_channel_validation` stampano un numero di telefono nel body
  (es. g001 results[3]: 40916, 800.191.101; lista nel report M4 in sessione).
- Promo TIM4YOU200: due date di fine (30/09/2026 in P1-FAQ-062 vs 30/04/2026 in P1-RAG-078)
  senza `conflict_flag`.
- Top Destination/Resto del Mondo: P1-FAQ-011/012 "per ricaricabili" vs P1-RAG-045
  "per entrambe" — senza `conflict_flag`.
- g028 results[8]: `flags.conflict` su un body che NEGA il conflitto.
- `vera_roaming_zone`: Kenya e Maldive senza riga (0/270) — buco del loader o assenza a
  monte? (FAQ P1-FAQ-008/009/010 danno default Zona 4.)
- `low_confidence` su 268/789 righe payload: non menzionato nella rubrica (la skill nuova
  lo tratta come segnale di ri-query).
- 347 `source_version` placeholder ("ULTIMO", "n/d") = 21% del corpus non citabile.

## 12. Trappole operative (leggere PRIMA di toccare qualcosa)

- **Mai `wful tables update` da schemi su disco**: il live ha colonne/righe in più
  (`vera_faq.question_search` di FIX2; +37 righe `OFFMIR-*` in `vera_kb_units`).
  Sempre `wful tables get` prima; `--column`/`--data-file` sostituiscono l'INTERA lista
  (per un pelo non abbiamo droppato `question_search`).
- **CLI ≠ runtime sui filtri**: `wful tables row-list --filters-json` vuole
  `{"filters":[{"key","value"}]}` e IGNORA silenziosamente chiavi sconosciute (sembra che
  filtri, non filtra — cinque probe diverse, stesse 10 righe). Il `contains`
  column/operator/value esiste solo nel runtime (`ctx.tables.filter`). Per verifiche CLI
  usare `--text`.
- **I `console.log` dei tool arrivano al modello** dentro il tool result. Non loggare
  interni. Il log `hits` è strumentazione nostra: non rimuoverlo senza sostituto.
- **Le tracce troncano gli attributi span a ~16KB**: misurare il recall dai log del tool
  (event `search_vera`), non dagli span (si sottostima: 37% vs 52%).
- **Pre-receive piattaforma**: sotto `evals/` solo path standard.
- **Agenti paralleli sullo stesso working tree**: un verifier con regola "nessuna modifica
  fuori dal tuo scope" può revertare il lavoro di un altro cantiere (successo: recuperato
  dal transcript del workflow). Scope per FILE o worktree isolati.
- **Retrieval deterministico**: query identica → righe identiche. Ripetere query è inutile;
  la varianza fra round è riformulazione del modello.

## 13. Guardrail

Profilo e workspace **espliciti** su ogni comando; solo agente `vera-interforze`;
MAI workspace General. Piattaforma read-only tranne `eval run` + `agents snapshot` +
i backfill esplicitamente approvati, imposto dall'allowlist in `wful.py`. Chiavi lette a
runtime, mai stampate né salvate. `data/` gitignored (dato cliente). Repo evalkit: git
**locale**, nessun remote. Niente merge del modello luna su main senza decisione esplicita.
