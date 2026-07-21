---
name: inbox
description: Verarbeitet eingehende Diktate/Nachrichten zuverlässig als Inbox-Workflow: klassifizieren, Duplikat prüfen, bestehenden Eintrag erweitern oder neu anlegen, relevante Links setzen und klar bestätigen.
---

# Inbox Workflow (Diktate & Nachrichten)

## Ziel
Eingehende Diktate/Nachrichten strukturiert und reproduzierbar in den Workspace
überführen, ohne stille Fehler oder doppelte Einträge.

## Ablauf (verpflichtende Reihenfolge)
1. **Klassifizieren:** Typ, Thema, Priorität und gewünschte Aktion bestimmen.
2. **Bestehend prüfen:** Im Workspace nach bestehendem passendem Eintrag suchen.
3. **Erweitern oder neu anlegen:** Duplikat/Fortsetzung erweitern; sonst neu erstellen.
4. **Auto-Linking:** Mindestens einen relevanten bestehenden Link ergänzen, falls Kandidaten existieren.
5. **Bestätigen:** Klar melden, was wo abgelegt/erweitert wurde und welche Links gesetzt wurden.

## Routing-Regel
- Routing-Hinweise immer aus der **Routing-Tabelle in `AGENTS.md`** des aktiven
  Workspace lesen.
- Keine fest codierten Route-Mappings im Skilltext oder in Tool-Argumenten.

## Gates (Fehlpfade explizit behandeln)
- Wenn Klassifikation unklar ist: gezielte Rückfrage statt raten.
- Wenn Schreiben fehlschlägt: Fehler klar ausgeben, nicht als Erfolg formulieren.
- Wenn keine relevanten Link-Kandidaten existieren: explizit als Ergebnis nennen.

## Verifikation
- Zielpfad im Workspace ist benannt und existiert.
- Bei Duplikat: bestehender Eintrag wurde erweitert (kein doppelter Neueintrag).
- Mindestens ein relevanter Link wurde gesetzt, sofern Kandidaten vorhanden waren.
- Nutzer erhält eine knappe, eindeutige Abschlussbestätigung.

## Was NICHT
- Keine stillen Fallbacks ohne Rückmeldung.
- Keine erfundenen Quellen/Links.
- Kein Versand externer Nachrichten ohne explizite Freigabe.
