# Qubo Live

Dashboard locale per il rullo Elite Qubo (BLE name `QD`): potenza, velocità e
cadenza in tempo reale, grafico, statistiche di sessione, salvataggio CSV/TCX,
**allenamenti strutturati con target di potenza** e **controllo della
resistenza / target power (ERG)** via FTMS.

Il sensore trasmette sia in **ANT+** sia in **Bluetooth Smart (BLE)**.

> **ANT+ non è Bluetooth.** Su macOS il protocollo ANT+ richiede una chiavetta
> USB ANT+ (Garmin/Dynastream) e il driver `libusb`. Senza chiavetta il Mac non
> ha il radio ANT+, quindi la dashboard usa il Bluetooth. Lo stesso sensore è
> dual-mode: i dati sono gli stessi.

## Setup

Il progetto è autonomo: questa cartella contiene codice, dashboard e dati.

```bash
cd ~/Desktop/Lavoro/code/qubo-live
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Su macOS, la prima volta concedi il permesso Bluetooth al terminale:
**Impostazioni di Sistema → Privacy e Sicurezza → Bluetooth**.

## 1. Verifica la connessione

```bash
# scansione BLE + connessione automatica al sensore Elite
.venv/bin/python probe.py --mode ble --listen 30

# forza un dispositivo per nome o indirizzo
.venv/bin/python probe.py --mode ble --connect "QD"

# solo elenco dispositivi in JSON
.venv/bin/python probe.py --mode ble --json
```

Il probe stampa:

- dispositivi vicini con RSSI e servizi BLE;
- servizi e caratteristiche del sensore connesso;
- dati decodificati (`potenza`, `velocità`, `cadenza`) in tempo reale;
- frame grezzi dei servizi proprietari, se il sensore non usa i profili standard.

### ANT+ (solo con chiavetta USB)

```bash
brew install libusb
.venv/bin/pip install openant
.venv/bin/python probe.py --mode ant --id 11029 --listen 30
```

Cerca il device ANT+ `11029`, ne stampa tipo e seriale e apre i profili
supportati (Cycling Power, Speed & Cadence). Su questo Mac, senza chiavetta,
il comando termina con un messaggio esplicativo.

## 2. Dashboard

```bash
# per provare l'interfaccia senza sensore
.venv/bin/python server.py --simulate

# uso reale: apri il browser e premi "Cerca sensore"
.venv/bin/python server.py

# connessione automatica al miglior candidato all'avvio
.venv/bin/python server.py --auto

# indirizzo BLE noto (lo stampa il probe)
.venv/bin/python server.py --address "XXXXXXXX-XXXX-..."
```

Apri <http://127.0.0.1:8000>.

Il pulsante in alto a destra alterna tema chiaro e scuro: la scelta viene
salvata nel browser e, alla prima apertura, segue la preferenza di sistema.

La dashboard mostra:

- potenza, velocità e cadenza in tempo reale;
- zona di intensità calcolata sull'FTP impostato;
- **target card** con potenza dell'intervallo corrente, tempo rimanente e
  intervallo successivo;
- grafico con finestra dinamica: 60 s, 2 minuti, poi 4 minuti (potenza e velocità);
- durante un allenamento, grafico del **profilo target completo** con la potenza
  reale recente e il marker della posizione corrente;
- durata, distanza, media e massimo di potenza e velocità;
- **controllo rullo**: slider resistenza `0-20` (passo `0.1`), preset
  `0 / 5 / 10 / 15 / 20` e target power ERG;
- pannello **Allenamento** con libreria, avvio, pausa/ripresa e conclusione;
- editor per creare, modificare ed eliminare allenamenti;
- elenco delle attività salvate, scaricabili in **TCX** (pronto per Strava)
  oppure CSV;
- pannello **Debug BLE** con gli ultimi frame ricevuti e la tabella di
  confronto fra le fonti (FTMS, CPS, CSC): valore grezzo di potenza, velocità
  e cadenza per ciascun profilo, fonte attiva in grassetto e valori scartati
  barrati.
- **Mini player** dal pulsante in alto: apre una finestra Picture-in-Picture
  (Chrome/Edge) con quattro pagine scorrevoli: potenza, grafico, cadenza e
  velocità, sessione. Resta visibile mentre navighi su altre schede; su browser
  senza Document Picture-in-Picture si apre una finestra separata. Il mini
  player si collega direttamente a `/ws` e ha il pulsante di chiusura.

### Mini player

Il pulsante **mini player** nella barra in alto apre una finestra
Picture-in-Picture (Chrome/Edge) che resta visibile mentre navighi su altre
schede. Ha quattro pagine scorrevoli:

- **Potenza**: watt, W/kg, zona FTP, tempo trascorso, target e velocità/cadenza.
- **Grafico**: andamento recente di potenza e velocità.
- **Cadenza e velocità**: i due valori grandi, tempo trascorso e FTP.
- **Sessione**: tempo, distanza, medie, massimi, calorie, W/kg e stato del workout.

Si cambia pagina con swipe, trackpad orizzontale, frecce, tastiera o i pallini
in basso. Il mini player ha una connessione WebSocket indipendente, quindi
continua ad aggiornarsi anche con la dashboard in secondo piano. Su browser
senza Document Picture-in-Picture si apre una finestra separata.

Le sessioni vengono scritte in `data/trainer_sessions/` (CSV + TCX + riepilogo
JSON) quando premi **Concludi** o **Disconnetti**. Concludere salva la sessione
e ferma la registrazione: non viene aperta automaticamente una nuova attività.
Avviare un allenamento finalizza la sessione precedente e ne apre una nuova,
così le statistiche partono da zero. Il CSV e il TCX della sessione in corso
sono scaricabili in qualsiasi momento.

Il **TCX** contiene sempre tutti i campioni registrati, dall'inizio alla fine
della seduta, con un punto al secondo e tempo, distanza, velocità, potenza e
cadenza: è il formato da caricare su Strava per vedere i grafici e le statistiche
complete. Se la sessione è stata avviata da un workout, il TCX contiene anche un
**lap per ogni intervallo**: i lap sono solo riepiloghi per leggere la media dei
20 minuti del test FTP e di ogni ripetuta VO2max/soglia, non sostituiscono il
flusso completo dei campioni. Per le sessioni già salvate il TCX
viene generato al volo dal CSV, quindi il link funziona anche per le attività
vecchie. Gli orari sono scritti come ora locale della sessione perché Strava,
per i file senza GPS, interpreta i timestamp come ora locale.

La modalità `--simulate` usa invece `data/sim_sessions/`: i dati demo non
compaiono fra le attività reali e non inquinano le statistiche.

## 3. Allenamenti strutturati

La libreria è persistita in `data/workouts.json`. Se il file manca, il server
crea la libreria del piano invernale: **Test FTP 20 min**, **Climb 3x12**,
**Climb 2x20**, **Climb 1x30**, **Over-under 3x9**, **VO2max 4x3**,
**VO2max 5x3**, **VO2max 4x4**, **VO2max 3x3 scarico**, **Soglia 3x8**,
**Soglia 3x10**, **Soglia 2x20**, **Soglia 2x10 scarico**, **Sweet spot 3x12**,
**Endurance 60**, **Endurance 60 + sprint**, **Endurance 75** e **Recupero 45**.
Il piano completo di 12 settimane e la nutrizione sono in
`PIANO_ALLENAMENTO.md`. Sono modificabili e cancellabili dalla dashboard; il
seed viene rifatto solo se il file non esiste.

Un allenamento è una sequenza di intervalli con durata in secondi, potenza
target assoluta in watt e, opzionalmente, cadenza target:

```json
{
  "workouts": [
    {
      "id": "vo2max-5x3",
      "name": "VO2max 5x3",
      "description": "Cinque ripetute da 3 minuti a 208 W.",
      "notes": "OBIETTIVO: consolidare il VO2max...\nCADENZA: 95-105 rpm...",
      "builtin": true,
      "intervals": [
        { "label": "Riscaldamento", "duration_s": 600, "power_w": 117, "cadence_min": 85, "cadence_max": 95 },
        { "label": "VO2max 1", "duration_s": 180, "power_w": 208, "cadence_min": 95, "cadence_max": 105 },
        { "label": "Recupero 1", "duration_s": 180, "power_w": 100, "cadence_min": 80, "cadence_max": 90 }
      ]
    }
  ]
}
```

Il server valida i dati: da 1 a 48 intervalli, durata tra 5 secondi e 3 ore,
potenza tra 0 e 4000 W, cadenza tra 20 e 250 rpm, nome fino a 80 caratteri,
descrizione fino a 280 e note fino a 6000.

La libreria inclusa è calibrata sulla **FTP 179 W**: VO2max circa 112-119%
FTP, soglia 95-102%, sweet spot 91%, endurance 70%. Se cambi FTP, riscala i
target con `nuova_FTP / 179` oppure modifica gli intervalli dalla dashboard.

### Come si usa

1. Scegli un allenamento nella lista e premi **Avvia allenamento**.
2. La **target card** mostra l'intervallo corrente, la potenza target, la
   **cadenza target**, il tempo rimanente e il prossimo intervallo.
3. Il grafico passa al **profilo target completo**; la linea arancione è la
   potenza reale recente e il marker verticale indica il punto attuale.
4. **Pausa** ferma il cronometro e la registrazione; **Riprendi** riparte dallo
   stesso punto senza contare il tempo trascorso in pausa.
5. **Concludi** salva subito la sessione (CSV, TCX e JSON), chiude l'allenamento
   e lascia la registrazione ferma. Non parte una nuova attività.
6. Per registrare un giro libero senza allenamento usa **Avvia sessione** nella
   card **Sessione**; **Concludi** nella stessa card salva e chiude.

Sotto la lista, **Note e istruzioni** mostra la guida dell'allenamento scelto:
cosa fare, cadenza, resistenza di partenza, come sentirti e cosa mangiare
durante. Le note si modificano dall'editor dell'allenamento (campo **Note e
istruzioni**, fino a 6000 caratteri) e sono incluse in `GET /api/workouts`.

Il pulsante **Piano** apre `PIANO_ALLENAMENTO.md` renderizzato su `/piano`, con
tabelle e link per scaricare il file originale da `/piano.md`.

Il target è una **guida visuale**, non un ERG automatico: il rullo non cambia
resistenza da solo per inseguire i watt dell'intervallo. Per una guida ERG usa
il controllo dedicato nel pannello **Controllo rullo** e imposta i watt a mano.
La target card e il grafico restano comunque attivi.

Avviare un workout finalizza la sessione precedente e ne apre una nuova, così
durata, media e distanza del workout non si mescolano con il riscaldamento
precedente. L'allenamento attivo vive in memoria: un riavvio del server lo
chiude, mentre la libreria resta su disco. Il grafico usa il buffer live per la
potenza reale (circa gli ultimi 4 minuti); il profilo target copre invece tutto
l'allenamento.

### API workout

```bash
# libreria
curl http://127.0.0.1:8000/api/workouts

# crea un allenamento
curl -X POST http://127.0.0.1:8000/api/workouts \
  -H 'Content-Type: application/json' \
  -d '{"name":"VO2max 10-7","intervals":[
        {"label":"VO2max","duration_s":600,"power_w":300},
        {"label":"Recupero","duration_s":420,"power_w":200}
      ]}'

# modifica: stesso payload con "id"
curl -X POST http://127.0.0.1:8000/api/workouts \
  -H 'Content-Type: application/json' \
  -d '{"id":"vo2max-10-7","name":"VO2max 10-7","intervals":[
        {"label":"VO2max","duration_s":600,"power_w":310},
        {"label":"Recupero","duration_s":420,"power_w":200}
      ]}'

# avvia, pausa, riprendi, concludi e salva
curl -X POST http://127.0.0.1:8000/api/workout/start \
  -H 'Content-Type: application/json' -d '{"id":"vo2max-5x3"}'
curl -X POST http://127.0.0.1:8000/api/workout/pause
curl -X POST http://127.0.0.1:8000/api/workout/resume
curl -X POST http://127.0.0.1:8000/api/workout/stop

# sessione libera: avvia e concludi senza allenamento
curl -X POST http://127.0.0.1:8000/api/session/start
curl -X POST http://127.0.0.1:8000/api/session/stop

# elimina
curl -X DELETE http://127.0.0.1:8000/api/workouts/vo2max-10-7
```

`POST /api/workouts` senza `id` crea un nuovo allenamento; con `id` aggiorna
quello esistente. Gli endpoint di controllo restituiscono lo snapshot completo
della dashboard, con il campo `workout` aggiornato.

## 4. Controllo resistenza e ERG

Il rullo espone il **FTMS Control Point** (`0x2AD9`) con:

- **Supported Resistance Level Range** (`0x2AD6`): `0.0-20.0`, passo `0.1`;
- **Supported Power Range** (`0x2AD8`): `0-4000 W`, passo `1 W`;
- **Fitness Machine Feature** (`0x2ACC`): su questo esemplare
  `fitness=20614` (`0x5086`), `target=8204` (`0x200C`), quindi il firmware
  dichiara il target di resistenza (bit 2), il target di potenza (bit 3) e i
  parametri di simulazione strada (bit 13).

La dashboard legge i bit di `0x2ACC` e abilita il controllo ERG solo se il
target power è dichiarato; se la caratteristica manca usa la presenza di
`0x2AD8` come fallback.

La dashboard invia i comandi FTMS sulla connessione BLE già aperta:

| Comando | Payload | Risposta |
| --- | --- | --- |
| Request Control | `00` | `80 00 01` (success) |
| Start/Resume | `07` | `80 07 01` (success) / `80 07 04` se già avviato |
| Set Target Resistance 5.0 | `04 32 00` | `80 04 01` (success) |
| Set Target Resistance 0.0 | `04 00 00` | `80 04 01` (success) |
| Set Target Power 0 W | `05 00 00` | `80 05 01` (success) |
| Set Simulation flat road | `11 00 00 00 00 32 23` | `80 11 01` (success) |

**Start/Resume è obbligatorio.** Senza `07` il rullo risponde `success` al
target resistance ma il freno resta fermo a `0`. La dashboard lo invia
automaticamente prima di ogni comando di resistenza o ERG; se il firmware
risponde `80 07 04` (già avviato) il comando viene considerato valido.

La resistenza **attuale** viene letta dal campo resistance di FTMS Indoor Bike
Data (`0x2AD2`, unità `0.1`). La UI mostra:

- il numero grande: il **target** inviato dall'app;
- la riga «attuale»: la posizione del freno riportata dal rullo.

Il freno si muove gradualmente: dopo un comando possono volerci alcuni secondi
prima che l'attuale raggiunga il target. In ERG il target è la potenza e la
resistenza attuale varia di conseguenza.

### Simulazione strada

La modalità **Strada** usa il comando FTMS `0x11` (Set Indoor Bike Simulation
Parameters) con:

- pendenza `0%`;
- resistenza al rotolamento `Crr = 0,005`;
- resistenza aerodinamica `CdA = 0,35 kg/m`;
- vento `0 m/s`.

Il rullo riceve questi parametri e adatta la resistenza in base alla velocità,
come su una strada piatta. La UI mostra `strada` nel controllo e disabilita
slider ed ERG finché la modalità è attiva. Per tornare al controllo manuale si
preme **Disattiva strada**: l'app invia di nuovo il target di resistenza
attuale.

Questa modalità invia comandi al rullo, quindi non è compatibile con la
richiesta precedente di lasciare il freno completamente manuale. Il Qubo
risponde al comando ma il motore del freno è lento: la resistenza attuale può
impiegare alcuni secondi a seguire la velocità. Il modello interno del rullo
può inoltre differire dalla stima usata dalla dashboard; la tabella della
sezione 5 resta un riferimento per la resistenza manuale.

### FTP di riferimento

Il test del **24/10/2026** ha dato:

- media dei 20 minuti: **188,7 W**;
- FTP = 188,7 × 0,95 = **179 W**;
- **2,18 W/kg** con 82 kg;
- blocchi da 5 minuti: 178 / 183 / 191 / 203 W, quindi pacing conservativo.

La libreria è calibrata su **179 W**. Se i target sembrano troppo facili, il
blocco finale a 203 W suggerisce che con un pacing più aggressivo l'FTP possa
essere 185-190 W: rifai il test tra 4-6 settimane. Per ora il numero ufficiale
resta 179 W.

Le calorie sono stimate dal lavoro meccanico: `kJ ≈ kcal` a circa il 24% di
efficienza. La dashboard mostra calorie e W/kg nella sessione, nel live e nel
mini player; il TCX contiene le calorie per lap.

### Modello e verifica fisica

Il dispositivo si chiama `QD`, espone target resistance **e** target power FTMS
e il freno si muove dopo Start/Resume: è il comportamento di un **Qubo Digital
Smart B+**. Il **Power Mag Smart B+** è invece un rullo magnetico con **8
livelli manuali** sulla leva, senza motore: su quello il controllo software non
può funzionare. Controlla l'etichetta e la presenza dell'alimentatore: il
Digital ha bisogno della corrente di rete per muovere il motore passo-passo.

Se dopo Start/Resume la durezza non cambia:

1. verifica che l'alimentatore del rullo sia collegato e che il LED sia acceso;
2. ascolta il rullo mentre muovi lo slider: dovresti sentire il motorino per
   alcuni secondi;
3. se il motorino non gira, segui la procedura Elite **Stepping motor
   unlocking** (rullo spento e scollegato): togli il coperchio e ruota il
   motorino giallo di 3/4 di giro in senso orario se è bloccato al minimo,
   antiorario se è bloccato al massimo. Non forzare;
4. se il motorino gira ma i magneti non si muovono, il filetto del carrello può
   essere rotto: contatta Elite per il ricambio.

Procedure Elite:
<https://elitesrl.zendesk.com/hc/en-us/articles/4408396697490-Stepping-motor-unlocking-Qubo-Digital-Qubo-Digital-Smart-B-Novo-Smart-Realtour-Evo-Intera>

## 5. Impostazioni

- **Circonferenza / sviluppo (mm)**: serve per i profili CPS/CSC di riserva e
  per la diagnostica. La velocità live usa il dato **FTMS** del rullo, quindi
  non dipende da questo valore. Valori tipici:
  - Qubo Power Mag: **94 mm**;
  - Qubo Digital: **47 mm**;
  - ruota reale 700x23c: 2096, 700x25c: 2105, 700x28c: 2136.
- **FTP**: usato per le zone di intensità, per il W/kg e per i target degli
  allenamenti.
- **Peso (kg)**: usato per calcolare W/kg e calorie bruciate.
- **Calibrazione potenza (W)**: offset additivo applicato alla potenza del
  rullo quando il valore è maggiore di zero. Il Qubo Digital stima la potenza
  dal carico e dalla velocità del rullo e tende a leggere **10-50 W in meno**;
  il valore predefinito è **+30 W**, il punto medio di quell'intervallo; su
  questo esemplare è stato portato a **+50 W** dopo la prova in pedalata.
- **Calibrazione velocità (km/h)**: offset additivo applicato alla velocità
  quando il valore è maggiore di zero. Il valore predefinito è **+2,5 km/h**
  (circa il 10% a 25 km/h), coerente con la correzione di potenza su strada
  piana. Non è una misura indipendente: serve a compensare la sottostima del
  rullo e va raffinato se si ha un riferimento.
- **Debug BLE**: attiva l'ascolto dei servizi proprietari e mostra i frame raw.

Gli offset si applicano **solo ai valori maggiori di zero**, quindi a riposo la
dashboard resta a 0 W e 0 km/h invece di mostrare l'offset. Sotto i **20 W** e
i **10 km/h** l'offset entra gradualmente, così i valori di noise durante la
coast-down non diventano potenza o velocità fantasma. Vengono applicati prima
di `SessionRecorder.add`, quindi le metriche corrette finiscono in:

- display live e storico del grafico;
- statistiche di sessione (media, massimo, distanza);
- CSV della sessione e `/api/export.csv`;
- TCX della sessione e `/api/export.tcx`;
- riepilogo JSON della sessione, che include anche il blocco `calibration` con
  gli offset in uso.

Le sessioni già salvate non vengono riscritte. La calibrazione vale subito per
le metriche live e per i campioni successivi al salvataggio delle impostazioni;
il riepilogo JSON della sessione in corso viene aggiornato con gli offset in
uso. Per disattivarla basta impostare i due offset a `0`.

## 6. Dispositivo riconosciuto

Il probe ha rilevato il sensore come `QD` (indirizzo macOS
`D29B9AD1-3001-C7B6-8DD6-7F88F0603F41`) con:

- manufacturer `Elite`, serial `11029` (lo stesso numero del device ANT+);
- firmware `153`, hardware `007`;
- profili `0x1816` CSC, `0x1818` Cycling Power, `0x1826` FTMS;
- FTMS Control Point `0x2AD9` (write + indicate);
- FTMS Feature `0x2ACC`, Resistance Range `0x2AD6`, Power Range `0x2AD8`;
- servizio proprietario `347b0001-7635-408b-8918-8ff3949ce592` con
  caratteristiche notify `347b0011`, `347b0014`, `347b0017`.

Il servizio proprietario viene mostrato nel pannello debug; i dati di guida
arrivano dai profili standard.

## 7. Profili decodificati e fonti

| Profilo | UUID | Dati |
| --- | --- | --- |
| Cycling Speed & Cadence | `0x1816` / `0x2A5B` | velocità, cadenza |
| Cycling Power | `0x1818` / `0x2A63` | potenza, velocità, cadenza |
| FTMS Indoor Bike | `0x1826` / `0x2AD2` | potenza, velocità, cadenza |
| FTMS Control Point | `0x1826` / `0x2AD9` | resistenza, target power |

La dashboard **unisce i campi** invece di scegliere un solo profilo:

- **potenza**: FTMS (`0x2AD2`), con Cycling Power come riserva. Il campo
  potenza di `0x2A63` su questo firmware non è affidabile: le sessioni
  registrate mostrano picchi irrealistici (circa 2800 W a 6 km/h) e tratti
  quasi a zero durante la pedalata. Il campo FTMS è invece la potenza
  calcolata dal rullo, quella che dipende dal carico che i magneti oppongono
  al moto;
- **velocità**: FTMS (`0x2AD2`, km/h calcolati dal rullo), con Cycling Power
  come prima riserva e CSC come seconda. Sul firmware del Qubo l'event time
  CSC può far raddoppiare la velocità, quindi CPS è preferibile come fallback;
- **cadenza**: Cycling Power / CSC, perché la cadenza FTMS di questo firmware
  non è affidabile.

La sigla accanto a **Debug BLE** mostra le fonti effettivamente in uso, ad
esempio `FTMS + CPS`.

### Frame FTMS reali

Il firmware Elite usa il bit `0x0800` (heart rate) ma al posto del battito
appende un contatore di tempo trascorso a 16 bit. Il frame tipico ha flags
`0x0874` e 15 byte:

| Byte | Campo | Scala |
| --- | --- | --- |
| 0-1 | flags (`0x0874`) | - |
| 2-3 | velocità istantanea | `0.01 km/h` |
| 4-5 | cadenza istantanea | `0.5 rpm` |
| 6-8 | distanza totale | `uint24`, metri |
| 9-10 | resistenza attuale | `0.1` |
| 11-12 | potenza istantanea | watt |
| 13-14 | tempo trascorso (non standard) | secondi |

Esempio a riposo:
`74 08 00 00 00 00 00 13 00 00 00 00 00 0f 0c` = 0 km/h, 0 rpm, 4864 m,
resistenza 0, potenza 0 W, 3087 s. La potenza è quindi nei byte 11-12 e non
viene spostata dal campo finale. Il decoder gestisce anche frame troncati:
restituisce i campi già letti e ignora il resto.

### Perché CPS non è la fonte principale

Il frame Cycling Power `2a63` di questo firmware ha flags `0x0030` (dati ruota
e pedivella) e potenza istantanea nei byte 2-3. Nelle sessioni registrate
prima della correzione il campo potenza mostrava picchi a multipli di 256
(fino a 2816 W, cioè `0x0B00`) e lunghi tratti a zero: è un artefatto di
allineamento del firmware, non una misura. La dashboard usa quindi FTMS per
potenza e velocità. CPS e CSC restano attivi per la cadenza (rivoluzioni
pedivella) e come riserva se FTMS non arriva.

### Valori fuori scala

Prima di applicare un valore la dashboard verifica che sia plausibile:

| Campo | Intervallo |
| --- | --- |
| potenza | `0-1200 W` |
| velocità | `0-120 km/h` |
| cadenza | `0-220 rpm` |

I valori fuori intervallo non entrano nelle statistiche: finiscono nella lista
`rejected` dello stato e il pannello Debug BLE li mostra barrati. Una fonte
già attiva mantiene la priorità per 3 secondi, così un frame CPS sporadico non
sostituisce il dato FTMS.

## 8. API locale

La dashboard espone anche un'API JSON minimale:

```bash
curl http://127.0.0.1:8000/api/state

curl -X POST http://127.0.0.1:8000/api/resistance \
  -H 'Content-Type: application/json' -d '{"level": 5.0}'

curl -X POST http://127.0.0.1:8000/api/erg \
  -H 'Content-Type: application/json' -d '{"watts": 150}'

curl -X POST http://127.0.0.1:8000/api/config \
  -H 'Content-Type: application/json' \
  -d '{"power_offset_w": 30, "speed_offset_kmh": 2.5}'

# Scarica il TCX della sessione in corso o di una sessione salvata
curl -OJ http://127.0.0.1:8000/api/export.tcx
curl -OJ http://127.0.0.1:8000/api/sessions/2026-10-23_17-56-39/tcx

# Piano di allenamento: pagina HTML e file Markdown originale
curl http://127.0.0.1:8000/piano
curl -OJ http://127.0.0.1:8000/piano.md
```

`/api/resistance` accetta `0-20`; `/api/erg` accetta `0-4000` W. Entrambi
restituiscono lo snapshot completo, con lo stato `control` aggiornato. Gli
endpoint per la libreria workout e per avvio/pausa/ripresa/conclusione sono
documentati nella sezione 3. `/api/session/start` e `/api/session/stop`
gestiscono una sessione libera senza workout.

Lo snapshot di `/api/state` include anche i campi di diagnostica usati dal
pannello Debug BLE:

- `source_values`: per ogni campo (`power_w`, `speed_kmh`, `cadence_rpm`) il
  valore grezzo e la validità per ciascuna fonte (`2ad2`, `2a63`, `2a5b`);
- `rejected`: gli ultimi valori scartati, con orario, fonte, campo e valore;
- `sources`: la fonte attualmente in uso per ogni campo;
- `erg_supported` e `features`: supporto ERG e valori letti da `0x2ACC`;
- `control.actual_resistance`: posizione reale del freno riportata da FTMS.

## 9. Problemi comuni

- **Nessun dispositivo trovato**: il sensore va svegliato pedalando due giri;
  dopo un periodo di inattività va in sleep e smette di fare advertising,
  quindi se il server è appena stato riavviato pedala qualche secondo e premi
  di nuovo **Cerca sensore**. Deve essere scollegato da Zwift / My E-Training /
  altri telefoni.
- **Bluetooth non autorizzato**: concedi il permesso al terminale e riavvia
  lo script.
- **Connessione occupata**: un solo central BLE può essere connesso alla volta.
- **Controlli disabilitati**: la UI li abilita solo con il rullo connesso e il
  Control Point FTMS disponibile.
- **La resistenza non cambia**: la dashboard invia già Start/Resume; se il
  freno resta fermo controlla alimentazione, motorino passo-passo e carrello
  (vedi «Modello e verifica fisica»). Su un Power Mag manuale usa la leva.
- **ANT+ non disponibile**: serve la chiavetta USB, il Bluetooth del Mac non
  parla ANT+.
