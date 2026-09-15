# Marketplace Order & Sales Importer

Desktop-Anwendung zur automatisierten Verarbeitung, Bereinigung und Verrechnung von Bestell- und Verkaufsberichten von **Amazon**, **eBay** und **BrickLink** mit Bestands- und Einkaufspreisabgleich über Brickmerge-Depot CSV-Exporte.

---

## Funktionen

### Einkaufe Importieren**: ###
* **Amazon Buisness**: Verarbeitet Bestellberichte (`Orders report`), filtert Duplikate und exportiert Brickmerge-kompatible CSV-Dateien.

### Verkäufe Importieren**: ###
* Import von **Amazon**, **eBay** und **BrickLink** Verkäufen über die Verkaufsberichte
* Zuordnung über SKUs und Setnummern im Titel bzw. BrickLink Item No., sowie manuelle Zuweisungen
* Berechnung der Verkaufsgebühren und Schätzung der Versandkosten
* Aggregation ähnlicher Verkäufe eines Sets im selben Monat.

### Depot-Verrechnung & Margen-Kalkulation**: ###
* **FIFO-Abbau**: Bestandsabzug und Ermittlung des tatsächlichen Einkaufspreises (EK) chronologisch nach Kaufdatum.
* **Reiner Berechnungsmodus**: Ermittlung des Durchschnitts-EKs aus einer Depot-Export CSV für nachträglich dokumentierte Verkäufe.


### Tracking & Duplikatschutz**: ###
* Lokale SQLite-Datenbank (`import_history.db`) zur Filterung bereits importierter Bestell- und Zahlungs-IDs.

---

## Download & Schnellstart (Windows)

Die lauffähige Anwendung kann direkt ohne lokale Python-Installation heruntergeladen werden:

1. Lade die aktuelle Version unter **Releases** herunter.
2. Starte die `brickmerge_depot_importer.exe`.
3. Beim ersten Start wird automatisch eine lokale `import_history.db` im selben Verzeichnis angelegt.

---

## Installation aus dem Quellcode

### Voraussetzungen

* Python $\ge$ 3.10
* Empfohlen: Virtuelle Umgebung (`venv`)

### Setup

```bash
# Repository klonen
git clone https://github.com/Jens252/brickmerge_import.git
cd <REPO-NAME>

# Virtuelle Umgebung erstellen und aktivieren
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# Abhängigkeiten installieren
pip install -r requirements.txt

```

### `requirements.txt`

```text
pandas
numpy

```

### Anwendung starten

```bash
python gui.py

```
---

## Build mit PyInstaller

Um eine eigenständige `.exe` zu bauen:

```bash
pip install pyinstaller
pyinstaller brickmerge_depot_importer.spec

```

Die fertige Binärdatei befindet sich anschließend im Ordner `dist/`
