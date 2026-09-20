# Brickmerge Depot Importer für Marktplatz Verkäufe und Amazon Business Einkäufe

Desktop-Anwendung zur automatisierten Generierung von Import CSV-Dateien für brickmerge Depots. 
Zum Ausführen aus dem Quellcode wird mindestens Python **3.10** benötigt.

---

## Funktionen

### Einkäufe importieren: ###
* **Amazon Business**: Verarbeitet Orders Reports, korrigiert mehrfache Einträge und exportiert brickmerge-kompatible CSV-Dateien.

### Verkäufe Importieren: ###
* Generiert CSV-Dateien zum Import im brickmerge Depot für Verkäufe über **Amazon**, **eBay** und **BrickLink** aus den jeweiligen Verkaufsberichten
* Zuordnung über SKUs und Setnummern im Titel bzw. BrickLink Item No., sowie manuelle Zuweisungen
* Berechnung der Verkaufsgebühren und Schätzung der Versandkosten
* Aggregation ähnlicher Verkäufe eines Sets im selben Monat.

### Depot-Verrechnung & Margen-Kalkulation: ###
* Über brickmerge-Depot CSV-Export Datei.
* **FIFO-Abbau**: Bestandsabzug und Ermittlung des tatsächlichen Einkaufspreises (EK) chronologisch nach Kaufdatum. Die aktualisierte Datei kann dann genutzt werden, um den Bestand im brickmerge Depot zu aktualisieren.
* **Reiner Berechnungsmodus**: Verwendung des Durchschnitts-EKs aus einer Depot-Export CSV für nachträglich dokumentierte Verkäufe.


### Tracking & Duplikatschutz: ###
* Lokale SQLite-Datenbank zur Filterung bereits importierter Positionen.

---

## Download & Schnellstart (Windows)

Die lauffähige Anwendung kann direkt ohne lokale Python-Installation heruntergeladen werden:

1. Lade die aktuelle Version unter **Releases** herunter.
2. Starte die `brickmerge_depot_importer.exe`.
3. Beim ersten Start wird automatisch eine lokale `import_history.db` im selben Verzeichnis angelegt.

---

## Ausführen aus dem Quellcode

### Voraussetzungen

* Python >= 3.10
* Empfohlen: Virtuelle Umgebung (`venv`)

### Setup

```bash
# Repository klonen
git clone https://github.com/Jens252/brickmerge_import.git
cd brickmerge_import

# Virtuelle Umgebung erstellen und aktivieren
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# Abhängigkeiten installieren
pip install -r requirements.txt

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
