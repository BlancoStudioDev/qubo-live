"""Structured workouts: validation, defaults and JSON persistence.

The web app keeps workouts in one small JSON file so the library is easy to
edit by hand as well as from the dashboard.  The server remains the source of
truth and never trusts interval values coming from the browser.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_INTERVALS = 48
MIN_INTERVAL_S = 5
MAX_INTERVAL_S = 3 * 60 * 60
MAX_POWER_W = 4000
MAX_NAME = 80
MAX_DESCRIPTION = 280
MAX_NOTES = 6000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slug(value: str) -> str:
    text = unicodedata.normalize("NFKD", value)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (text or "workout")[:80]


def _iv(label: str, minutes: float, power_w: int) -> dict[str, Any]:
    return {
        "label": label,
        "duration_s": int(round(minutes * 60)),
        "power_w": int(power_w),
    }


def _workout(
    identifier: str,
    name: str,
    description: str,
    intervals: list[dict[str, Any]],
    now: str,
    notes: str = "",
) -> dict[str, Any]:
    return {
        "id": identifier,
        "name": name,
        "description": description,
        "notes": notes,
        "intervals": intervals,
        "builtin": True,
        "created_at": now,
        "updated_at": now,
    }


def default_workouts() -> list[dict[str, Any]]:
    """The FTP 179 W / VO2max / threshold starter library (absolute watts)."""
    now = _now_iso()
    return [
        _workout(
            'vo2max-5x3',
            'VO2max 5x3',
            "5 x 3' a 208 W con 3' a 100 W. Cadenza 95-105 rpm. Nutrizione: 2-3 h prima 60-80 g carbo + 20-30 g proteine; acqua durante; dopo 30-40 g proteine + 80 g carbo.",
            [
                _iv('Riscaldamento', 10, 117),
                _iv('VO2max 1', 3, 208),
                _iv('Recupero 1', 3, 100),
                _iv('VO2max 2', 3, 208),
                _iv('Recupero 2', 3, 100),
                _iv('VO2max 3', 3, 208),
                _iv('Recupero 3', 3, 100),
                _iv('VO2max 4', 3, 208),
                _iv('Recupero 4', 3, 100),
                _iv('VO2max 5', 3, 208),
                _iv('Recupero 5', 3, 100),
                _iv('Defaticamento', 10, 96),
            ],
            now,
            "OBIETTIVO: consolidare il VO2max con 5 ripetute da 3 minuti.\n\nRISCALDAMENTO: 10' a 117 W, cadenza 85-95 rpm.\n\nINTERVALLI: 5 x 3' a 208 W, recupero 3' a 100 W. Tieni il recupero facile anche se hai voglia di spingere.\n\nCADENZA: 95-105 rpm durante, 80-90 rpm nel recupero.\n\nRESISTENZA: livello 5-8 (o Strada), cambio per arrivare al target a 100 rpm. No ERG. Regola: se a 100 rpm sei sotto, sali di 1 livello; se sei sopra, scendi di 1 o cambia rapporto.\n\nCOME SENTIRTI: RPE 8. Se l'ultima ripetuta è al limite, va bene; se crolli prima, abbassa di 5 W.\n\nNUTRIZIONE DURANTE: acqua; 30 g di carboidrati all'ora se fa caldo o se allunghi la seduta. Prima: 60-80 g di carboidrati + 20-30 g di proteine 2-3 h prima. Dopo: 30-40 g di proteine + 80 g di carboidrati.\n\nDEFATICAMENTO: 10' a 96 W.\n\nNOTE: no ERG. Il TCX contiene tutta la seduta, campione per campione. I lap delle ripetute sono solo riepiloghi: su Strava puoi leggere la media dei 3', ma il flusso completo resta nel file.",
        ),
        _workout(
            'soglia-3x10',
            'Soglia 3x10',
            "3 x 10' a 179 W con 5' a 100 W. Cadenza 85-95 rpm. Nutrizione: 2-3 h prima 60-80 g carbo + 20-30 g proteine; acqua durante; dopo 30-40 g proteine + 80-100 g carbo.",
            [
                _iv('Riscaldamento', 10, 117),
                _iv('Soglia 1', 10, 179),
                _iv('Recupero 1', 5, 100),
                _iv('Soglia 2', 10, 179),
                _iv('Recupero 2', 5, 100),
                _iv('Soglia 3', 10, 179),
                _iv('Recupero 3', 5, 100),
                _iv('Defaticamento', 10, 96),
            ],
            now,
            "OBIETTIVO: consolidare la soglia con 3 blocchi da 10 minuti.\n\nRISCALDAMENTO: 10' a 117 W, cadenza 85-95 rpm.\n\nINTERVALLI: 3 x 10' a 179 W, recupero 5' a 100 W. Parti leggermente sotto e chiudi leggermente sopra: niente partenze a tutta.\n\nCADENZA: 85-95 rpm. Se scende sotto 80, scala un pignone.\n\nRESISTENZA: livello 4-7 (o Strada), cambio per stare al target. No ERG. Regola: se a 90 rpm sei sotto, sali di 1 livello; se sei sopra, scendi o cambia rapporto.\n\nCOME SENTIRTI: RPE 7-8. Il respiro è forte, ma non devi essere in apnea.\n\nNUTRIZIONE DURANTE: acqua; 30 g di carboidrati all'ora se fa caldo o se la seduta supera i 60'. Prima: 60-80 g di carboidrati + 20-30 g di proteine 2-3 h prima. Dopo: 30-40 g di proteine + 80-100 g di carboidrati.\n\nDEFATICAMENTO: 10' a 96 W.\n\nNOTE: no ERG. Se l'ultimo blocco crolla, la prossima volta abbassa di 5 W.",
        ),
        _workout(
            'endurance-60',
            'Endurance 60',
            "40' continui a 125 W, con 10' di riscaldamento e 10' di defaticamento. Cadenza 85-95 rpm. Nutrizione: 2-3 h prima 50-70 g carbo + 20 g proteine; acqua durante; dopo 25-30 g proteine + 60-80 g carbo.",
            [
                _iv('Riscaldamento', 10, 117),
                _iv('Endurance', 40, 125),
                _iv('Defaticamento', 10, 108),
            ],
            now,
            "OBIETTIVO: fondo aerobico continuo, la base del piano.\n\nRISCALDAMENTO: 10' a 117 W, cadenza 85-95 rpm.\n\nLAVORO: 40' continui a 125 W. Se con il cardio sei a 130-140 bpm e riesci a parlare, sei in Z2. Se il battito sale troppo, rallenta.\n\nCADENZA: 85-95 rpm, costante. Se scende sotto 80, scala un pignone.\n\nRESISTENZA: livello 2-4 (o Strada). Non serve un carico alto: qui conta il tempo, non la forza.\n\nCOME SENTIRTI: RPE 3-4, respirazione comoda, puoi parlare in frasi intere.\n\nNUTRIZIONE DURANTE: acqua; carboidrati non necessari in 60'. Se fa caldo, aggiungi elettroliti. Prima: 50-70 g di carboidrati + 20 g di proteine 2-3 h prima. Dopo: 25-30 g di proteine + 60-80 g di carboidrati.\n\nDEFATICAMENTO: 10' a 108 W.\n\nNOTE: se il target a 125 W è troppo facile e la FC resta bassa, puoi salire a 135-140 W restando in Z2.",
        ),
        _workout(
            'sweet-spot-3x12',
            'Sweet spot 3x12',
            "3 x 12' a 162 W con 4' a 100 W. Cadenza 85-95 rpm. Nutrizione: 2-3 h prima 50-70 g carbo + 20 g proteine; acqua durante; dopo 30 g proteine + 80 g carbo.",
            [
                _iv('Riscaldamento', 10, 117),
                _iv('Sweet spot 1', 12, 162),
                _iv('Recupero 1', 4, 100),
                _iv('Sweet spot 2', 12, 162),
                _iv('Recupero 2', 4, 100),
                _iv('Sweet spot 3', 12, 162),
                _iv('Recupero 3', 4, 100),
                _iv('Defaticamento', 10, 96),
            ],
            now,
            "OBIETTIVO: lavorare sotto soglia con 3 blocchi da 12 minuti.\n\nRISCALDAMENTO: 10' a 117 W, cadenza 85-95 rpm.\n\nINTERVALLI: 3 x 12' a 162 W, recupero 4' a 100 W. È più facile della soglia, ma non è un recupero: resta concentrato.\n\nCADENZA: 85-95 rpm.\n\nRESISTENZA: livello 4-6 (o Strada), cambio per stare al target. No ERG. Se a 90 rpm sei sotto, sali di 1 livello; se sei sopra, scendi o cambia rapporto.\n\nCOME SENTIRTI: RPE 6-7, respiro profondo ma controllato. Dovresti poter dire qualche parola.\n\nNUTRIZIONE DURANTE: acqua; 30 g di carboidrati all'ora se fa caldo o se allunghi. Prima: 50-70 g di carboidrati + 20 g di proteine 2-3 h prima. Dopo: 30 g di proteine + 80 g di carboidrati.\n\nDEFATICAMENTO: 10' a 96 W.\n\nNOTE: no ERG. È una buona seduta per la terza volta settimanale, senza il costo della soglia piena.",
        ),
        _workout(
            'test-ftp-20-min',
            'Test FTP 20 min',
            "Test FTP 20': 5' spin, 7' warmup, 3x1' fast (200 W) con 1' easy, 2' easy, 20' test a sensazione (target 185 W), 10' cooldown. No ERG, resistenza fissa. FTP = media 20' x 0,95. Nutrizione: 2-3 h prima 60-80 g carbo + 25 g proteine; dopo 30-40 g proteine + 80-100 g carbo.",
            [
                _iv('Spin iniziale', 5, 100),
                _iv('Riscaldamento', 7, 117),
                _iv('Fast 1', 1, 200),
                _iv('Easy 1', 1, 100),
                _iv('Fast 2', 1, 200),
                _iv('Easy 2', 1, 100),
                _iv('Fast 3', 1, 200),
                _iv('Easy 3', 1, 100),
                _iv('Recupero', 2, 100),
                _iv('Test FTP 20 min', 20, 185),
                _iv('Defaticamento', 10, 96),
            ],
            now,
            "OBIETTIVO: misurare l'FTP con una prova di 20 minuti. Non è un allenamento per fare un record: è un test da ripetere nelle stesse condizioni.\n\nRISCALDAMENTO: 5' spin facile, 7' a 117 W, poi 3x1' a 200 W con 1' a 100 W, 2' a 100 W. Cadenza 85-95 rpm, rapporto agile.\n\nTEST: 20' a sensazione. Parti a 180-185 W, resta regolare, negli ultimi 5' dai tutto. Obiettivo: split negativo del 2-5%, non del 14%. Se al 5' minuto sei già in apnea, rallenta; se parli comodamente, accelerà.\n\nCADENZA: 85-95 rpm, stay seated. Se scende sotto 80, scala un pignone.\n\nRESISTENZA: resistenza fissa, livello 5-7 di partenza, stesso livello e stesso rapporto del test precedente. No ERG, no Strada: il carico deve restare costante. Se a 90 rpm non arrivi al target, sali di 1 livello; se sei sopra, scendi di 1 o cambia rapporto.\n\nNUTRIZIONE DURANTE: solo acqua a piccoli sorsi. Non servono carboidrati nei 20 minuti. 2-3 h prima 60-80 g di carboidrati + 25 g di proteine; 30' prima una banana se serve; dopo 30-40 g di proteine + 80-100 g di carboidrati.\n\nDEFATICAMENTO: 10' a 96 W.\n\nNOTE: non mettere in pausa. Il TCX contiene tutti i campioni della seduta; il lap del test è solo un riepilogo per leggere la media dei 20'. FTP = media 20' x 0,95. Se scoppi, recupera 48 h e ripeti.",
        ),
        _workout(
            'vo2max-4x3',
            'VO2max 4x3',
            "4 x 3' a 200 W con 3' a 100 W. Cadenza 95-105 rpm. Riscaldamento e defaticamento inclusi. Nutrizione: 2-3 h prima 50-70 g carbo + 20 g proteine; acqua durante; dopo 30 g proteine + 60-80 g carbo.",
            [
                _iv('Riscaldamento', 10, 117),
                _iv('VO2max 1', 3, 200),
                _iv('Recupero 1', 3, 100),
                _iv('VO2max 2', 3, 200),
                _iv('Recupero 2', 3, 100),
                _iv('VO2max 3', 3, 200),
                _iv('Recupero 3', 3, 100),
                _iv('VO2max 4', 3, 200),
                _iv('Recupero 4', 3, 100),
                _iv('Defaticamento', 10, 96),
            ],
            now,
            "OBIETTIVO: introdurre il VO2max con 4 ripetute da 3 minuti. È una seduta dura ma corta.\n\nRISCALDAMENTO: 10' a 117 W, cadenza 85-95 rpm.\n\nINTERVALLI: 4 x 3' a 200 W, recupero 3' a 100 W. Il recupero è parte dell'allenamento: non andare forte.\n\nCADENZA: 95-105 rpm durante le ripetute, 80-90 rpm nel recupero.\n\nRESISTENZA: parti da livello 5-7 (o Strada) e usa il cambio per arrivare a 200 W. No ERG. Se a 100 rpm non arrivi al target, sali di 1 livello; se sei sopra, scendi o cambia rapporto.\n\nCOME SENTIRTI: RPE 8, respiro profondo, ma non deve diventare un test massimale. Se non chiudi l'ultima ripetuta, abbassa di 5 W la prossima volta.\n\nNUTRIZIONE DURANTE: acqua; 30 g di carboidrati all'ora solo se fa caldo o se aggiungi spin oltre i 60'. Prima: 50-70 g di carboidrati + 20 g di proteine 2-3 h prima. Dopo: 30 g di proteine + 60-80 g di carboidrati.\n\nDEFATICAMENTO: 10' a 96 W.\n\nNOTE: no ERG. Il TCX contiene tutta la seduta, campione per campione. I lap delle ripetute sono solo riepiloghi: su Strava puoi leggere la media dei 3', ma il flusso completo resta nel file.",
        ),
        _workout(
            'vo2max-4x4',
            'VO2max 4x4',
            "4 x 4' a 212 W con 4' a 100 W. Cadenza 95-105 rpm. Seduta avanzata. Nutrizione: 2-3 h prima 60-80 g carbo + 25 g proteine; acqua durante; dopo 35-40 g proteine + 80-100 g carbo.",
            [
                _iv('Riscaldamento', 10, 117),
                _iv('VO2max 1', 4, 212),
                _iv('Recupero 1', 4, 100),
                _iv('VO2max 2', 4, 212),
                _iv('Recupero 2', 4, 100),
                _iv('VO2max 3', 4, 212),
                _iv('Recupero 3', 4, 100),
                _iv('VO2max 4', 4, 212),
                _iv('Recupero 4', 4, 100),
                _iv('Defaticamento', 10, 96),
            ],
            now,
            "OBIETTIVO: seduta VO2max avanzata con 4 ripetute da 4 minuti.\n\nRISCALDAMENTO: 10' a 117 W, cadenza 85-95 rpm.\n\nINTERVALLI: 4 x 4' a 212 W, recupero 4' a 100 W. È la seduta più dura del blocco: non aggiungere altro.\n\nCADENZA: 95-105 rpm durante, 80-90 rpm nel recupero.\n\nRESISTENZA: livello 5-8 (o Strada), cambio per stare a 100 rpm. No ERG. Se a 100 rpm non arrivi, sali di 1 livello; se sei sopra, scendi o cambia rapporto.\n\nCOME SENTIRTI: RPE 8-9, respiro molto profondo. Se non chiudi la terza ripetuta, la prossima volta togli 5 W.\n\nNUTRIZIONE DURANTE: acqua; 30-60 g di carboidrati all'ora se fa caldo o se la seduta supera i 60'. Prima: 60-80 g di carboidrati + 25 g di proteine 2-3 h prima. Dopo: 35-40 g di proteine + 80-100 g di carboidrati.\n\nDEFATICAMENTO: 10' a 96 W.\n\nNOTE: no ERG. Non farla il giorno prima di un test o di una seduta di soglia.",
        ),
        _workout(
            'vo2max-3x3-scarico',
            'VO2max 3x3 scarico',
            "3 x 3' a 191 W con 4' a 100 W. Versione scarico. Cadenza 95-105 rpm. Nutrizione: 2 h prima 40-60 g carbo + 20 g proteine; acqua durante; dopo 25-30 g proteine + 60 g carbo.",
            [
                _iv('Riscaldamento', 10, 117),
                _iv('VO2max 1', 3, 191),
                _iv('Recupero 1', 4, 100),
                _iv('VO2max 2', 3, 191),
                _iv('Recupero 2', 4, 100),
                _iv('VO2max 3', 3, 191),
                _iv('Recupero 3', 4, 100),
                _iv('Defaticamento', 10, 96),
            ],
            now,
            "OBIETTIVO: mantenere un po' di stimolo VO2max in una settimana di scarico.\n\nRISCALDAMENTO: 10' a 117 W, cadenza 85-95 rpm.\n\nINTERVALLI: 3 x 3' a 191 W, recupero 4' a 100 W. Non cercare il record: deve sembrare controllata.\n\nCADENZA: 95-105 rpm durante, 80-90 rpm nel recupero.\n\nRESISTENZA: livello 5-7 (o Strada), cambio per stare a 95-100 rpm. No ERG.\n\nCOME SENTIRTI: RPE 7-8, non 9. Se sei distrutto, fai solo 2 ripetute.\n\nNUTRIZIONE DURANTE: acqua; carboidrati non necessari. Prima: 40-60 g di carboidrati + 20 g di proteine 2 h prima. Dopo: 25-30 g di proteine + 60 g di carboidrati.\n\nDEFATICAMENTO: 10' a 96 W.\n\nNOTE: è una seduta di scarico: meglio finire freschi che con una ripetuta in più.",
        ),
        _workout(
            'soglia-3x8',
            'Soglia 3x8',
            "3 x 8' a 171 W con 4' a 100 W. Cadenza 85-95 rpm. Nutrizione: 2-3 h prima 50-70 g carbo + 20 g proteine; acqua durante; dopo 30 g proteine + 60-80 g carbo.",
            [
                _iv('Riscaldamento', 10, 117),
                _iv('Soglia 1', 8, 171),
                _iv('Recupero 1', 4, 100),
                _iv('Soglia 2', 8, 171),
                _iv('Recupero 2', 4, 100),
                _iv('Soglia 3', 8, 171),
                _iv('Recupero 3', 4, 100),
                _iv('Defaticamento', 10, 96),
            ],
            now,
            "OBIETTIVO: introdurre il lavoro di soglia con 3 blocchi da 8 minuti.\n\nRISCALDAMENTO: 10' a 117 W, cadenza 85-95 rpm.\n\nINTERVALLI: 3 x 8' a 171 W, recupero 4' a 100 W. Cerca un ritmo regolare, non partire forte.\n\nCADENZA: 85-95 rpm. Se scende sotto 80, scala un pignone.\n\nRESISTENZA: livello 4-7 (o Strada), cambio per mantenere il target senza grattare. No ERG. Se a 90 rpm sei sotto, sali di 1 livello; se sei sopra, scendi o cambia rapporto.\n\nCOME SENTIRTI: RPE 7-8, respiro forte ma controllato. Dovresti poter dire poche parole.\n\nNUTRIZIONE DURANTE: acqua; 30 g di carboidrati all'ora se fa caldo o se allunghi. Prima: 50-70 g di carboidrati + 20 g di proteine 2-3 h prima. Dopo: 30 g di proteine + 60-80 g di carboidrati.\n\nDEFATICAMENTO: 10' a 96 W.\n\nNOTE: no ERG. Il TCX contiene tutta la seduta, campione per campione. Il lap di ogni blocco è solo un riepilogo per leggere la media; il flusso completo resta nel file.",
        ),
        _workout(
            'soglia-2x20',
            'Soglia 2x20',
            "2 x 20' a 183 W con 5' a 100 W. Cadenza 85-95 rpm. Seduta chiave del blocco. Nutrizione: 2-3 h prima 60-80 g carbo + 25 g proteine; acqua durante; dopo 35-40 g proteine + 100 g carbo.",
            [
                _iv('Riscaldamento', 10, 117),
                _iv('Soglia 1', 20, 183),
                _iv('Recupero 1', 5, 100),
                _iv('Soglia 2', 20, 183),
                _iv('Recupero 2', 5, 100),
                _iv('Defaticamento', 10, 96),
            ],
            now,
            "OBIETTIVO: seduta chiave di soglia con 2 blocchi da 20 minuti.\n\nRISCALDAMENTO: 10' a 117 W, cadenza 85-95 rpm.\n\nINTERVALLI: 2 x 20' a 183 W, recupero 5' a 100 W. È una prova di pazienza: parti controllato e resta regolare.\n\nCADENZA: 85-95 rpm. Se scende sotto 80, scala un pignone.\n\nRESISTENZA: livello 4-7 (o Strada), cambio per mantenere il target. No ERG. Se a 90 rpm sei sotto, sali di 1 livello; se sei sopra, scendi o cambia rapporto.\n\nCOME SENTIRTI: RPE 7-8, respiro forte e regolare. Se il primo blocco ti distrugge, il target è troppo alto: abbassa di 5 W.\n\nNUTRIZIONE DURANTE: acqua; 30-60 g di carboidrati all'ora se fa caldo o se la seduta supera i 60'. Prima: 60-80 g di carboidrati + 25 g di proteine 2-3 h prima. Dopo: 35-40 g di proteine + 100 g di carboidrati.\n\nDEFATICAMENTO: 10' a 96 W.\n\nNOTE: no ERG. È la seduta più importante del blocco: meglio farla riposato.",
        ),
        _workout(
            'soglia-2x10-scarico',
            'Soglia 2x10 scarico',
            "2 x 10' a 171 W con 5' a 100 W. Versione scarico. Cadenza 85-95 rpm. Nutrizione: 2 h prima 40-60 g carbo + 20 g proteine; acqua durante; dopo 25-30 g proteine + 60-80 g carbo.",
            [
                _iv('Riscaldamento', 10, 117),
                _iv('Soglia 1', 10, 171),
                _iv('Recupero 1', 5, 100),
                _iv('Soglia 2', 10, 171),
                _iv('Recupero 2', 5, 100),
                _iv('Defaticamento', 10, 96),
            ],
            now,
            "OBIETTIVO: mantenere il ritmo di soglia in una settimana di scarico.\n\nRISCALDAMENTO: 10' a 117 W, cadenza 85-95 rpm.\n\nINTERVALLI: 2 x 10' a 171 W, recupero 5' a 100 W. Deve sembrare controllata, non una gara.\n\nCADENZA: 85-95 rpm.\n\nRESISTENZA: livello 4-6 (o Strada), cambio per stare al target. No ERG.\n\nCOME SENTIRTI: RPE 7, non 9. Se sei stanco, fai solo un blocco.\n\nNUTRIZIONE DURANTE: acqua; carboidrati non necessari. Prima: 40-60 g di carboidrati + 20 g di proteine 2 h prima. Dopo: 25-30 g di proteine + 60-80 g di carboidrati.\n\nDEFATICAMENTO: 10' a 96 W.\n\nNOTE: settimana di scarico: meglio finire con margine.",
        ),
        _workout(
            'endurance-75',
            'Endurance 75',
            "55' continui a 125 W, con 10' di riscaldamento e 10' di defaticamento. Cadenza 85-95 rpm. Nutrizione: 2-3 h prima 50-70 g carbo + 20 g proteine; 30-60 g carbo/h se serve; dopo 25-30 g proteine + 60-80 g carbo.",
            [
                _iv('Riscaldamento', 10, 117),
                _iv('Endurance', 55, 125),
                _iv('Defaticamento', 10, 108),
            ],
            now,
            "OBIETTIVO: fondo aerobico più lungo, per volume e resistenza.\n\nRISCALDAMENTO: 10' a 117 W, cadenza 85-95 rpm.\n\nLAVORO: 55' continui a 125 W. Tieni la cadenza e la respirazione stabili; non inseguire la velocità.\n\nCADENZA: 85-95 rpm.\n\nRESISTENZA: livello 2-4 (o Strada). Il carico deve permetterti di restare in Z2.\n\nCOME SENTIRTI: RPE 3-4, puoi parlare. Se la FC supera la Z2, rallenta.\n\nNUTRIZIONE DURANTE: acqua + 30-60 g di carboidrati all'ora (bevanda sportiva o gel) se la seduta supera i 75' o fa caldo. Prima: 50-70 g di carboidrati + 20 g di proteine 2-3 h prima. Dopo: 25-30 g di proteine + 60-80 g di carboidrati.\n\nDEFATICAMENTO: 10' a 108 W.\n\nNOTE: è la seduta giusta per provare l'alimentazione durante la corsa.",
        ),
        _workout(
            'recupero-45',
            'Recupero 45',
            "45' facili a 96 W, cadenza 80-90 rpm. Serve a muovere le gambe senza accumulare fatica. Nutrizione: normale, con 20-30 g proteine e carbo da verdura e cereali.",
            [
                _iv('Recupero', 45, 96),
            ],
            now,
            "OBIETTIVO: recupero attivo, muovere le gambe senza accumulare fatica.\n\nLAVORO: 45' facili a 96 W. Non guardare il target come un obiettivo: se sei più basso, va bene.\n\nCADENZA: 80-90 rpm, leggera.\n\nRESISTENZA: livello 1-3 (o Strada), catena agile. Non serve carico.\n\nCOME SENTIRTI: RPE 2-3, respirazione comoda, puoi parlare senza problemi.\n\nNUTRIZIONE DURANTE: acqua; carboidrati non necessari. Se hai fame, una banana va bene. Prima/dopo: pasti normali con 20-30 g di proteine.\n\nDEFATICAMENTO: nessuno, la seduta è già facile.\n\nNOTE: se sei stanco o dormito male, questa è la seduta giusta al posto di una qualità.",
        ),
    ]


def _as_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} non valido") from exc
    if number < minimum or number > maximum:
        raise ValueError(f"{field} fuori intervallo ({minimum}-{maximum})")
    return number


def normalize_workout(
    raw: dict[str, Any],
    *,
    workout_id: str | None = None,
    builtin: bool = False,
    created_at: str | None = None,
) -> dict[str, Any]:
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("il nome dell'allenamento è obbligatorio")
    if len(name) > MAX_NAME:
        raise ValueError(f"il nome non può superare {MAX_NAME} caratteri")

    description = str(raw.get("description") or "").strip()
    if len(description) > MAX_DESCRIPTION:
        raise ValueError(
            f"la descrizione non può superare {MAX_DESCRIPTION} caratteri"
        )

    notes = str(raw.get("notes") or "").strip()
    if len(notes) > MAX_NOTES:
        raise ValueError(
            f"le note non possono superare {MAX_NOTES} caratteri"
        )

    raw_intervals = raw.get("intervals")
    if not isinstance(raw_intervals, list) or not raw_intervals:
        raise ValueError("serve almeno un intervallo")
    if len(raw_intervals) > MAX_INTERVALS:
        raise ValueError(f"massimo {MAX_INTERVALS} intervalli")

    intervals: list[dict[str, Any]] = []
    for index, item in enumerate(raw_intervals):
        if not isinstance(item, dict):
            raise ValueError(f"intervallo {index + 1} non valido")
        label = str(item.get("label") or f"Intervallo {index + 1}").strip()
        intervals.append(
            {
                "label": label[:MAX_NAME] or f"Intervallo {index + 1}",
                "duration_s": _as_int(
                    item.get("duration_s"),
                    "durata",
                    MIN_INTERVAL_S,
                    MAX_INTERVAL_S,
                ),
                "power_w": _as_int(
                    item.get("power_w", 0), "potenza", 0, MAX_POWER_W
                ),
            }
        )

    identifier = _slug(workout_id or name)
    now = _now_iso()
    return {
        "id": identifier,
        "name": name,
        "description": description,
        "notes": notes,
        "intervals": intervals,
        "builtin": bool(builtin),
        "created_at": created_at or now,
        "updated_at": now,
    }


def workout_summary(workout: dict[str, Any]) -> dict[str, Any]:
    intervals = [dict(item) for item in workout.get("intervals", [])]
    total_s = sum(int(item["duration_s"]) for item in intervals)
    weighted = sum(
        int(item["power_w"]) * int(item["duration_s"]) for item in intervals
    )
    avg_power = round(weighted / total_s) if total_s else 0
    max_power = max((int(item["power_w"]) for item in intervals), default=0)
    data = dict(workout)
    data["intervals"] = intervals
    data["total_s"] = total_s
    data["avg_power_w"] = avg_power
    data["max_power_w"] = max_power
    data["interval_count"] = len(intervals)
    return data


def _backup_broken(path: Path) -> None:
    try:
        backup = path.with_name(path.name + ".broken")
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError:
        pass


def _seed(path: Path, *, backup: bool = False) -> list[dict[str, Any]]:
    if backup:
        _backup_broken(path)
    library = default_workouts()
    save_workouts(path, library)
    return library


def load_workouts(path: Path) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return _seed(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _seed(path, backup=True)
    items = raw.get("workouts") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return _seed(path, backup=True)

    library: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            library.append(
                normalize_workout(
                    item,
                    workout_id=str(item.get("id") or ""),
                    builtin=bool(item.get("builtin", False)),
                    created_at=str(item.get("created_at") or "") or None,
                )
            )
        except (TypeError, ValueError):
            continue
    if not library:
        return _seed(path, backup=True)

    # Be forgiving with hand-edited files that accidentally repeat an id.
    seen: set[str] = set()
    for workout in library:
        base = workout["id"]
        candidate = base
        suffix = 2
        while candidate in seen:
            candidate = f"{base}-{suffix}"
            suffix += 1
        workout["id"] = candidate
        seen.add(candidate)
    return library


def save_workouts(path: Path, library: list[dict[str, Any]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"workouts": library}
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temp.replace(path)
