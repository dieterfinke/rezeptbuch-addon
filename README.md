# Rezeptbuch für Home Assistant

Familienrezepte scannen, verwalten und per Sprache (Home Assistant Assist) abrufen.

## Installation

1. In Home Assistant **Einstellungen → Add-ons → Add-on Store** öffnen.
2. Oben rechts **⋮ → Repositories**, diese Adresse einfügen und **Hinzufügen**:
   `https://github.com/dieterfinke/rezeptbuch-addon`
3. Im Store erscheint **Rezeptbuch** → **Installieren**.
4. Reiter **Konfiguration**: den Anthropic-API-Schlüssel bei `anthropic_api_key` eintragen und speichern.
5. **Starten** und **In Seitenleiste anzeigen** einschalten. Im Reiter **Protokoll** muss `KI bereit: True` stehen.

Updates erscheinen danach automatisch im Add-on Store, sobald hier die Version in
`rezeptbuch/config.yaml` erhöht wird.

## Sprachsteuerung (Assist)

1. `homeassistant/custom_sentences/de/rezeptbuch.yaml` nach `/config/custom_sentences/de/` kopieren.
2. Den Inhalt von `homeassistant/configuration_rezeptbuch.yaml` in die `configuration.yaml` einfügen.
3. Der Hostname in `rest_command` ist schon auf `83bccaf2-rezeptbuch` eingestellt, so heißt das Add-on
   bei Installation aus diesem Repository. Nur bei einer lokalen Installation (`/addons/rezeptbuch`)
   muss dort `local-rezeptbuch` stehen.
4. Home Assistant neu starten und im Assist-Dialog testen, z. B. „Welche Backen Rezepte haben wir?“.

## Aufbau

| Ordner / Datei | Inhalt |
|---|---|
| `repository.yaml` | Macht dieses Repository zu einer Add-on-Quelle für Home Assistant |
| `rezeptbuch/` | Das Add-on (config.yaml, Dockerfile, run.sh, App) |
| `homeassistant/` | Sprachbefehle und Konfiguration für Assist |

Alle Rezepte und Bilder liegen im Add-on-Datenordner `/data` und sind Teil der Home-Assistant-Backups.
