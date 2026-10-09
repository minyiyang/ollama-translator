# Ollama Translator

[![CI](https://github.com/minyiyang/ollama-translator/actions/workflows/ci.yml/badge.svg)](https://github.com/minyiyang/ollama-translator/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) | [Français](README.fr.md) | [Español](README.es.md) | **Deutsch** | [한국어](README.ko.md)

Eine lokale, fortsetzbare Pipeline für die Übersetzung ganzer Bücher,
ausgelegt auf literarische Prosa vom Englischen ins Chinesische und umgekehrt.
Als Eingabe dient eine EPUB-, RTF-, Text-, Markdown-, HTML-, Word- (.docx) oder
Text-PDF-Datei; die Übersetzung kommt in dem Format zurück, in dem das Buch
geliefert wurde, oder als EPUB, PDF, Word, HTML, Markdown oder Text.
Untertiteldateien (.srt, .vtt,
.ass) werden ebenfalls übersetzt, Untertitel für Untertitel und mit
unverändertem Timing. Alles läuft ausschließlich gegen lokale Ollama-Modelle:
keine Cloud-API, kein MCP-Server und kein Agenten-Framework wie LangGraph oder
AutoGen.

**Sprachpaare.** Englisch → vereinfachtes Chinesisch (`en-zh`) und zurück
(`zh-en`) sind abgestimmt: Für sie laufen alle Kontrollen, das Glossar, das
Stilblatt und die stilistische Überarbeitung. Für Französisch (`fr`),
Japanisch (`ja`), Spanisch (`es`), Deutsch (`de`) und Koreanisch (`ko`) gibt
es Sprachprofile mit Interpunktionsregeln, Anredeformen, Zahlwörtern und
Prompt-Beispielen, als Ausgangs- wie als Zielsprache und in Kombination mit
jeder anderen Sprache. Alle übrigen Sprachen werden generisch behandelt:
Kontrollen, die eine Sprache nicht unterstützt, werden übersprungen statt mit
englischen oder chinesischen Regeln ausgeführt, und die stilistische
Überarbeitung bleibt aus. Das Paar steht in der Konfiguration, etwa
`translation.direction: en>ja` (Sprachcodes nach BCP 47). Wie gut andere
Paare übersetzt werden, hängt vom Modell ab; siehe
[docs/GENERIC_LANGUAGES.md](docs/GENERIC_LANGUAGES.md) (Englisch).

## Welches Problem das Projekt löst

Ein ganzes Buch scheitert anders als ein einzelner Modellaufruf. Wer ein
Sprachmodell einfach Kapitel für Kapitel übersetzen lässt, stößt meist auf
Folgendes:

- **Schwankende Terminologie**: Derselbe Personen- oder Ortsname lautet in
  Kapitel 3 anders als in Kapitel 20.
- **Beschädigte Struktur**: Inline-Tags wie `<em>` oder `<i>` gehen beim
  Umformulieren verloren, und das EPUB ist danach falsch formatiert.
- **Verfälschte Mengenangaben**: Zahlen, Einheiten, Entfernungen und Zeiten
  werden stillschweigend geändert und fallen beim Lesen kaum auf.
- **Verschlimmbesserung**: Das Modell „repariert“ einen korrekten Satz und
  ersetzt eine gute Übersetzung durch eine schlechtere.
- **Abbruch heißt Neustart**: Ein Fehler in Kapitel 40, und alles beginnt von
  vorn.

Das Projekt behandelt jedes dieser Probleme in einer eigenen, fortsetzbaren
Stufe, statt auf einen noch längeren Prompt zu setzen.

## Ablauf

```text
decompile
  -> extract_glossary -> resolve_glossary -> approve_glossary
  -> build_story_context (optional)
  -> preprocess -> translate
  -> audit_translation -> audit_consistency -> repair_translation
  -> reprose_translation
  -> review_repaired -> repair_review -> validate_repaired
  -> translate_title -> compile -> validate_epub
```

Jede Stufe legt ihre Artefakte als Checkpoint im Arbeitsbereich
`runs/<job-id>/` ab. `resume` überspringt Stufen, die bereits abgeschlossen
und validiert sind; ein unterbrochener Lauf setzt also bei der frühesten
betroffenen Stufe wieder ein und nicht am Anfang.

## Beispiel: *Alice im Wunderland*

Das mitgelieferte Beispielbuch von Project Gutenberg durchläuft die gesamte
Pipeline vom Englischen ins vereinfachte Chinesisch. Alle folgenden Schritte
finden im lokalen Browser-Dashboard statt:

```powershell
book-agent ui          # http://127.0.0.1:8765/
```

> Screenshots fehlen vorerst, weil sich die Oberfläche des Dashboards noch
> ändert; sie werden nachgereicht, sobald sie stabil ist.

**1. Konfigurieren und starten.** Die Seite „Jobs“ listet alle Jobs auf, zehn
pro Seite. *Neuen Job anlegen* fragt nur nach dem Quellbuch (bekannte Datei
auswählen, durchsuchen oder hineinziehen), einem Namen für die
Konfigurationsdatei und einer Job-ID, die standardmäßig dem Konfigurationsnamen
entspricht. Eine vorhandene Konfiguration wie
[`configs/demo-alice.yaml`](configs/demo-alice.yaml) wird wiederverwendet, ein
neuer Name entsteht aus `config.example.yaml`. Der Job öffnet sich im Tab
**Konfiguration**: *Optionen* bündelt die Einstellungen, die man am häufigsten
ändert (Übersetzungsstil, das Modell jeder Rolle samt Installationsstatus ✓/✗,
Art der Glossarfreigabe, Qualitätskontrollen, Ausgabe); *Alle Einstellungen*
zeigt jedes Konfigurationsfeld mit Typ, Grenzen, Standardwert und einer
Schaltfläche zum Zurücksetzen; *YAML* bearbeitet die Datei direkt. Alle drei
Ansichten bleiben synchron. *Validieren* speichert und validiert die
Konfiguration, führt einen Probelauf aus und fragt Ollama, ob alle Modelle
installiert sind. *Übersetzung starten* in der Kopfzeile des Jobs ist nur
verfügbar, solange die Konfiguration seit der bestandenen Validierung
unverändert ist. Während ein Job läuft, bietet die Kopfzeile *Pausieren* (der
laufende Modellaufruf wird noch beendet) und *Stoppen* (der Lauf endet
sofort); *Fortsetzen* macht in beiden Fällen beim letzten Checkpoint weiter.
Die Beispielkonfiguration verwendet nur lokal installierte Modelle, aktiviert
die stilistische Überarbeitung und die EPUB-Bereinigung und überlässt die
Glossarfreigabe dem Modell. Im Terminal startet `.\scripts\demo-alice.ps1`
denselben Lauf (`-Resume` setzt ihn fort).

**2. Glossarfreigabe.** Mit `workflow.llm_glossary_review: true` werden
belegte, konfliktfreie Einträge mit hoher Konfidenz direkt freigegeben, der
Rest geht an das Modell. Die Seite „Glossar“ zeigt danach die freigegebenen
Begriffe, welche davon das Modell geprüft hat, warum, und was es geändert hat.
Bei manueller Prüfung pausiert der Job an dieser Stelle, und dieselbe Seite
wird zum Editor: Übersetzung, Kategorie, Anmerkung und Aliasse eines Begriffs
korrigieren, Einträge ablehnen, auf markierte Einträge filtern (Allerweltswörter,
mehrfach vergebene Übersetzungen, niedrige Konfidenz, fehlende Belege) und zu
jedem Begriff die Belegsätze aus dem Quelltext lesen. Du gibst deine Fassung
frei oder übergibst sie (oder den unveränderten Entwurf) dem Modell zur
Prüfung; danach läuft die Pipeline von selbst weiter.

**3. Übersetzung, Audit und Reparatur.** Die Seite „Fortschritt“ verfolgt den
Job live, egal ob er im Dashboard oder im Terminal gestartet wurde: jede Stufe
mit Status, geplanten Modellaufgaben, aktueller Einheit, Aufrufen,
Ausgabe-Tokens und Dauer, dazu der laufende Modellaufruf und das
Sitzungsprotokoll. Bei diesem Buch pausiert die Pipeline vor dem Kompilieren
mit drei Segmenten, die sie nicht selbst verifizieren konnte.

**4. Endprüfung.** Die Seite „Endprüfung“ (direkt zu öffnen mit
`book-agent review-ui .\runs\demo-alice-en-zh`) arbeitet diese
Prüfwarteschlange ab. Zu jedem Segment zeigt sie den Quelltext mit Kontext,
die Befunde (ein Klick hebt die zitierte Stelle hervor), die früheren
Fassungen aus der Pipeline und einen Editor mit Live-Diff, der dieselbe
deterministische Kontrolle anwendet wie `resolve-review`. Mit dem Anwenden der
Entscheidungen ist der Entwurf freigegeben; anschließend wird das EPUB
kompiliert und validiert.

**5. Das ganze Buch lesen und bearbeiten.** Der Tab **Text** zeigt jedes
Kapitel als Zeilenpaare aus Quelltext und Übersetzung. Jedes Segment lässt
sich an Ort und Stelle bearbeiten, mit Begründung, derselben deterministischen
Kontrolle und einem Diff. Jede Bearbeitung, auch jede Entscheidung aus der
Endprüfung, ist ein Eintrag in einem gemeinsamen Bearbeitungsprotokoll, das
keine Neuausführung löscht; sie lässt sich rückgängig machen, und ihr Verlauf
bleibt einsehbar. Ändert eine Neuausführung die Übersetzung eines bearbeiteten
Segments, entsteht ein Konflikt, über den du entscheidest. *Neu kompilieren*
schreibt die Bearbeitungen in wenigen Sekunden ins Buch. **⤓ XLIFF
exportieren** übergibt das Buch an ein CAT-Tool, **⤒ XLIFF importieren** holt
die Datei der Übersetzerin oder des Übersetzers über eine Vorschau zurück,
bevor etwas geschrieben wird.

## Entwurfsentscheidungen

- **Überprüfbare Struktur gehört dem Code, nicht dem Modell.** EPUB-Struktur,
  Inline-Marker und Bezeichner werden deterministisch verarbeitet und nicht
  einem Modell überlassen.
- **Lieber verzichten als unsicher reparieren.** Eine Reparatur, die sich
  nicht verifizieren lässt, wird verworfen und darf keine bessere vorhandene
  Übersetzung überschreiben; das Segment geht in die manuelle Prüfung.
- **Begrenzte Wiederholungen.** Konvergiert die Validierung nicht mehr, ruft
  die Pipeline das Modell nicht weiter auf, statt sich am selben Fehler
  festzufahren.
- **Nur generische Fehlerbehandlung.** Fehlerklassen werden allgemein
  behandelt; im Code gibt es keine Sonderfälle für einzelne Bücher oder
  Textstellen.
- **Kontext nach Bedarf.** Die konfigurierten 131K sind eine Obergrenze, keine
  feste Zuteilung. Gewöhnliche Aufrufe beginnen bei 16K, gezielte Reparatur
  und Reparaturverifikation bei 8K, und wachsen nur bei Bedarf über 16K, 32K
  und 64K bis 131K. So bleibt der GPU-Speicherbedarf proportional zur Anfrage.

## Schnellstart

### 1. Ollama und Modelle vorbereiten

Ollama muss lokal laufen, und die konfigurierten Modelle müssen installiert
sein:

```powershell
ollama pull qwen3.8:latest
ollama pull gemma4:31b
```

Vor der ersten Stufe, die ein Modell braucht, prüft die CLI die Modellnamen
und die konfigurierte Kontextgröße.

### 2. Installieren

```powershell
python -m pip install -e .
book-agent --version
```

Alternativ über die Requirements-Dateien (maßgeblich bleiben die
Abhängigkeiten in `pyproject.toml`):

```powershell
python -m pip install -r requirements.txt
python -m book_agent --version
```

Voraussetzung sind Python 3.11 oder neuer und `ollama>=0.6.2`.

### 3. Konfiguration kopieren und validieren

```powershell
copy config.example.yaml my-book.yaml
book-agent config --file .\my-book.yaml
```

`config.example.yaml` ist eine vorsichtige Ausgangskonfiguration für den
Produktivbetrieb und keine Liste der Standardwerte; einige Werte weichen
bewusst von den Standardwerten im Code ab. Jeder Schlüssel darf fehlen; dann
gilt der Standardwert aus `book_agent/config.py`.

### 4. Erst der Probelauf, dann der echte Lauf

Der Probelauf schreibt keine Dateien und kontaktiert Ollama nicht:

```powershell
book-agent run "D:\books\source.epub" --config .\my-book.yaml --dry-run
```

Wenn alles stimmt, den Lauf starten:

```powershell
book-agent run "D:\books\source.epub" --config .\my-book.yaml --job-id "my-book-en-zh"
```

Der Befehl gibt den Pfad des Arbeitsbereichs aus. Notiere ihn; alle späteren
Befehle arbeiten auf diesem Verzeichnis. Die anderen Eingabeformate und
Untertiteldateien verwenden denselben Befehl und dieselbe Pipeline.

### 5. Status abfragen und fortsetzen

```powershell
book-agent status .\runs\my-book-en-zh
book-agent resume .\runs\my-book-en-zh --plain
```

`--plain` liefert eine für Logdateien geeignete Ausgabe und ändert nichts am
Verhalten der Pipeline.

## Modellrollen

Die Standardaufteilung ist vorsichtig gewählt; jede Rolle kann ein eigenes
Modell bekommen:

| Aufgabe | Standardmodell |
|---|---|
| Extraktion von Glossarkandidaten | `qwen3.8:27b` |
| Auflösung und Prüfung der Terminologie | `qwen3.8:latest` |
| Erstübersetzung und gezielte Reparatur | `qwen3.8:latest` |
| Semantisches Audit | `gemma4:31b` |
| Mengenprüfung und Entscheidung | `gemma4:26b`, bei Bedarf Eskalation auf `gemma4:31b` |
| Reparaturvergleich und begrenzte Verifikation | `gemma4:26b` |
| Vorschlag der stilistischen Überarbeitung | `qwen3.8:latest` |
| Verifikation der stilistischen Überarbeitung | `gemma4:31b` |

Für das semantische Audit und die Reparaturverifikation, die strukturiertes
JSON liefern, ist Thinking standardmäßig abgeschaltet. Einschalten lohnt sich
nur, wenn ein schwieriger Text nachweislich davon profitiert.

## Prüfung und Fortsetzen

Mit der Standardeinstellung `require_glossary_review: true` pausiert die
Verarbeitung, sobald der Glossarentwurf vorliegt, und wartet auf die manuelle
Freigabe:

```powershell
book-agent approve "D:\runs\my-job" --glossary "D:\reviews\glossary.txt" --resume
```

Alternativ prüft ein Modell den Entwurf mit schemagebundener Ausgabe, und der
Lauf geht sofort weiter:

```powershell
book-agent approve "D:\runs\my-job" --llm-glossary --resume
```

Das prüfende Modell darf Einträge korrigieren oder entfernen, aber keine
englischen Begriffe erfinden. Protokolliert werden Prompt- und Modell-Hash,
Versuche, Prüfmodus und die freigegebenen Artefakte.

### Browser-Dashboard

`book-agent ui` startet ein Dashboard, das nur auf der Loopback-Adresse
erreichbar ist (optional `--runs`, `--configs`, `--port`, `--no-browser`). Es
deckt dieselben Freigabepunkte ab wie die Befehle oben.

Die Oberfläche gibt es auf Englisch, vereinfachtem Chinesisch, Japanisch,
Französisch, Spanisch, Deutsch und Koreanisch, also in den Sprachen, für die
die Pipeline ein Profil hat. Die Sprache wählst du im Menü rechts in der
Kopfzeile; die Auswahl wird im Browser gespeichert, Standard ist Englisch. Die
Oberflächensprache ist unabhängig von der Übersetzungsrichtung eines Jobs
(siehe [Lokalisierung des Dashboards](docs/LOCALIZATION.md), Englisch).

- **Jobs** listet die Jobs mit ihrer Übersetzungsrichtung auf (zum Beispiel
  `EN → ZH`) und legt neue an. Ein abgeschlossener Job hat in der Liste und in
  der Kopfzeile eine Schaltfläche *Herunterladen* mit einem Formatmenü
  daneben. Das Menü beginnt mit dem Format, das der Job zurückgibt
  (`output.format` in seiner Konfiguration; standardmäßig das Format der
  Quelle). Ein Buch gibt es als EPUB, PDF, Word, HTML, Markdown oder Text,
  einen Untertitel-Job als SRT, WebVTT oder ASS; bei einem anderen
  Untertitelformat als dem der Datei sagt das Dashboard, was nicht übernommen
  wird. Im Tab **Konfiguration** wird der Job bearbeitet, validiert und
  gestartet; die Kopfzeile pausiert (nach dem laufenden Modellaufruf), stoppt
  oder setzt fort. `book-agent pause <workspace>` pausiert einen im Terminal
  gestarteten Lauf auf dieselbe Weise. **Reihen** fasst Jobs zusammen, die ein
  gemeinsames, versioniertes Reihenglossar verwenden.
- **Fortschritt** verfolgt jeden Job unter `--runs` anhand von `state.sqlite3`
  und der Sitzungsprotokolle. Eine fehlgeschlagene oder pausierte Stufe bietet
  *Fortsetzen* (erledigte Arbeit bleibt erhalten) und *Neu ausführen*, eine
  abgeschlossene *Ab hier neu ausführen*. Vor einer Neuausführung
  (`retry --stage X --resume`) zeigt ein Dialog, welche Stufen wiederholt
  werden und was dabei verloren geht. Die Spalte „Letzte Änderung“ zeigt, was
  mit jeder Stufe zuletzt geschehen ist (gestartet, abgeschlossen,
  fehlgeschlagen, gestoppt, pausiert, wartet auf Prüfung, zurückgesetzt) und
  wann.
- **Glossar** bearbeitet ein pausiertes Glossar und gibt es frei oder übergibt
  es dem Modell zur Prüfung (`approve --glossary` / `--llm-glossary`).
- **Text** zeigt das Buch kapitelweise als Zeilenpaare aus Quelltext und
  Übersetzung, filterbar nach markierten, in der Prüfwarteschlange stehenden,
  bearbeiteten oder im Konflikt stehenden Segmenten. Sobald
  `validate_repaired` abgeschlossen ist, lässt sich jedes Segment direkt
  bearbeiten: Speichern verlangt eine Begründung und durchläuft die
  deterministische Kontrolle (beschädigte Struktur oder Marker, leerer,
  unübersetzter oder doppelter Text und Interpunktionsfehler blockieren;
  andere Befunde brauchen eine eigene Begründung, um sie zu übergehen).
  Bearbeitungen landen in `edits/segment-edits.jsonl`, einem
  Nur-Anhängen-Protokoll, das keiner Stufe gehört und deshalb jede
  Neuausführung übersteht. Beim Kompilieren werden sie auf den validierten
  Entwurf angewendet; sind Bearbeitungen neuer als das Buch, baut *Neu
  kompilieren* es neu. Ändert eine Neuausführung die Übersetzung eines
  bearbeiteten Segments, entsteht ein Konflikt, der das Kompilieren
  blockiert, bis du deine Bearbeitung behältst oder den neuen Text übernimmst.
  Jede Bearbeitung hat einen Verlauf und lässt sich rückgängig machen. **⤓
  XLIFF exportieren** lädt das Buch als XLIFF 2.1 herunter; **⤒ XLIFF
  importieren** zeigt eine übersetzte Datei Segment für Segment in der
  Vorschau (zugeordnet wird nach Einheiten-ID und nur bei unverändertem
  Quelltext) und schreibt erst nach Bestätigung, als Bearbeitungen mit einer
  gemeinsamen Begründung (siehe [XLIFF-Import](docs/XLIFF_IMPORT.md),
  Englisch).
- **Endprüfung** arbeitet die Prüfwarteschlange mit derselben Validierung ab
  wie `resolve-review`; wer ein Segment unverändert annimmt, muss eine
  vorgegebene oder eigene Begründung angeben. Die Entscheidungen stehen im
  selben Bearbeitungsprotokoll wie die Bearbeitungen aus dem Tab „Text“, und
  eine Bearbeitung dort erledigt auch ein Segment aus der Prüfwarteschlange.
  Sobald die Zahl der offenen Segmente
  `workflow.compile_max_unresolved_review_segments` nicht mehr überschreitet,
  bietet die Seite an, die entschiedenen Segmente anzuwenden und den
  endgültigen Entwurf freizugeben. `book-agent review-ui <workspace>` öffnet
  diese Seite direkt.

Bei einem unerwarteten Fehler zeigt das Dashboard statt einer leeren Seite
einen Hinweis mit Fehlermeldung, technischen Details und der Schaltfläche
*Neu laden*.

Bearbeitungen lassen sich auf der Kommandozeile auflisten und als XLIFF 2.1
exportieren:

```powershell
book-agent edits "D:\runs\my-job"
book-agent edits "D:\runs\my-job" --export xliff --output my-job.xlf
```

Die Stufe `audit_consistency` (standardmäßig aktiv, ohne Modellaufrufe) prüft
die Konsistenz über das ganze Buch: Sätze und Dialogzeilen, die sich
wiederholen, müssen überall gleich übersetzt sein, und die Interpunktion muss
den Konventionen des Buchs folgen. Abweichungen gehen an die Reparatur; was
sie nicht beheben kann, kommt in die Prüfwarteschlange. Optional extrahiert
`consistency.style_sheet.enabled: true` zusammen mit dem Glossar ein
**Stilblatt** für das Buch: wiederkehrende Wendungen und Hinweise zu jeder
Figur. Es wird am selben Freigabepunkt von einem Menschen geprüft (im Tab
„Glossar“ im Abschnitt „Stilblatt“ oder mit `approve --style FILE`), auch
wenn das Glossar selbst vom Modell geprüft wird. Wiederkehrende Wendungen
werden im ganzen Buch gleich übersetzt; die Hinweise zu den Figuren dienen nur
als Kontext, über Pronomen und Anredeform entscheiden der Wortlaut des
Quelltexts und die Szene. `consistency.story_context.enabled: true` aktiviert
die optionale Stufe `build_story_context`: eine kurze Zusammenfassung pro
Kapitel (etwa 6 s je Kapitel), aus der jeder Übersetzungsabschnitt die
bisherige Handlung erhält, ebenfalls nur als Kontext. Einzelheiten stehen in
[Konsistenz auf Buchebene](docs/BOOK_CONSISTENCY.md) (Englisch).

Was das Dashboard startet, läuft als gewöhnlicher CLI-Aufruf in einem
Kindprozess. Protokolle und Checkpoints entsprechen deshalb einem Lauf im
Terminal, und der Job läuft weiter, wenn das Dashboard geschlossen wird.

Das Dashboard ist eine React-TypeScript-Anwendung in `frontend/`. Ihr
Produktions-Build ist unter `book_agent/web/static/` eingecheckt, sodass die
Installation des Pakets kein Node.js braucht. Für Änderungen an der Oberfläche
(Node 24):

```powershell
cd frontend
npm ci
npm run dev      # Entwicklung mit Hot Reload; /api geht an ein laufendes `book-agent ui`
npm test         # Unit-Tests der Hilfsfunktionen und Komponententests (jsdom + Testing Library)
npm run build    # Typprüfung und Neuaufbau von book_agent/web/static; Ergebnis mit einchecken
```

## Exit-Codes

| Code | Bedeutung |
|---:|---|
| `0` | Befehl erfolgreich oder Ablauf abgeschlossen |
| `1` | Fehler bei Validierung, Konfiguration, Modell, Stufe oder Betrieb |
| `2` | Ablauf an einem Freigabepunkt pausiert |
| `130` | Abbruch durch den Benutzer oder kooperativer Abbruch der Modellgenerierung |

## Tests

Die Testsuite arbeitet offline; kein Test kontaktiert Ollama, ein lokales
Modell ist also nicht nötig:

```powershell
python -m pip install -e ".[dev]"
python -m pytest
```

## Weitere Dokumentation

Die vollständige Befehlsreferenz, die Beschreibung aller Konfigurationsfelder
und die Betriebsdetails werden derzeit auf Englisch gepflegt:

- [Englisches README](README.md)
- [Produktivbetrieb](docs/OPERATIONS.md)
- [Architektur und Entwurf](docs/DESIGN.md)
- [Konsistenz auf Buchebene](docs/BOOK_CONSISTENCY.md)
- [Lokalisierung des Dashboards](docs/LOCALIZATION.md)
- [Stufensteuerung im Dashboard: Fortsetzen, Neuausführung, vorzeitige Freigabe](docs/STAGE_CONTROL.md)
- [Volltextprüfung und nachverfolgte manuelle Bearbeitungen](docs/FULL_TEXT_REVIEW.md)
- [XLIFF-Import](docs/XLIFF_IMPORT.md)
- [Reihenglossar im Dashboard](docs/SERIES_GLOSSARY_UI.md)
- [Text-, Markdown-, HTML- und Word-Bücher](docs/FORMAT_SUPPORT.md)
- [Arbeitsplan](docs/PLAN.md)
- [Plan für einen Benchmark der Inferenz-Frameworks (noch nicht ausgeführt)](docs/FRAMEWORK_BENCHMARK_PLAN.md)

## Lizenz

Code und Dokumentation stehen unter der [MIT-Lizenz](LICENSE). Die EPUB-Dateien
in `sample/` stammen von Project Gutenberg, behalten dessen eingebettete
Bedingungen und sind von der MIT-Lizenz ausgenommen; siehe
[sample/README.md](sample/README.md). Ollama-Modelle werden von diesem
Repository nicht verteilt und unterliegen ihren jeweiligen Modelllizenzen.
