# Prompt für Claude Code — Portierung abarbeiten

> Kopieren, in Claude Code im Repo `hermes-agent` einfügen, laufen lassen.
> Zwei Varianten: **A** arbeitet alles durch, **B** ein Ticket pro Aufruf.

---

## Variante A — kompletter Durchlauf (empfohlen für den ersten Anlauf)

```
Du arbeitest die Portierungs-Spec in diesem Repo ab:
installer/skills-hidden/aimds-loadout/PORTIERUNG-AUS-BRAIN.md

KONTEXT
Dieses Loadout ist die Standard-Auslieferung für Kunden-Installationen von Hermes.
Die Quelle der Logik ist ein persönlicher Obsidian-Vault unter
~/Documents/Claude/patrick-brain/.claude/ (agents/ und rules/). Der ist deutlich
ausgereifter als das Loadout — aber auf EINE Person zugeschnitten.

AUFTRAG
Arbeite Ticket 0 bis 5 der Reihe nach ab. Jedes Ticket vollständig abschließen,
bevor du das nächste anfängst.

ARBEITSWEISE JE TICKET
1. Ticket in der Spec lesen, inklusive der Struktur-Mapping-Tabelle oben.
2. Die genannte Brain-Quelldatei lesen (~/Documents/Claude/patrick-brain/.claude/...).
3. Die LOGIK übernehmen, den TEXT NEU SCHREIBEN. Nicht abschreiben — die
   Brain-Fassungen sind Referenz, keine Vorlage.
4. Zieldatei im Loadout anlegen/ändern.
5. Verifizieren (siehe unten). Erst dann Haken in der Spec setzen: - [ ] → - [x]
6. Kurz zusammenfassen was du geändert hast, dann weiter zum nächsten Ticket.

VERIFIKATION — nach JEDER geänderten Datei, ohne Ausnahme
a) Entpersonalisierung:
   grep -iE "patrick|fischi|dominik|gabriel|riley|lbbw|evn|cruxlab|bechtle|roechling|aok|iamds-intern" <datei>
   Muss 0 Treffer liefern. Jeder Treffer ist ein Fehler, kein Grenzfall.
b) Struktur-Mapping eingehalten? Prüfe gegen die Tabelle in der Spec —
   besonders: due statt deadline, _findings.md statt agent-queue.md,
   contacts/ statt people/, projects/ flach statt _active/_backlog/.
c) Verweist die Datei auf andere Skills oder Dateien? Prüfe mit ls/test, ob die
   wirklich existieren. Tote Verweise sind der häufigste Fehler beim Portieren.
d) Frontmatter vollständig (name, description) und valides YAML.

HARTE REGELN
- Erfinde keine Hermes-Mechanik. Wenn du nicht sicher bist, wie Hermes etwas
  aufruft (Cron, Blueprint-Metadaten, Skill-Verkettung), lies erst
  blueprints/README.md, AGENT-TOPOLOGIE.md und config/config.hermes.example.yaml.
  Findest du es dort nicht: STOPP und frag.
- Ändere nichts außerhalb von installer/skills-hidden/aimds-loadout/ und
  installer/workspace-template/. Kein Python, keine Runtime, keine Tests.
- Der Brain-Vault ist READ-ONLY. Nur lesen, niemals schreiben.
- Lösche keine bestehenden Skills. Bestehende Stubs werden erweitert, nicht ersetzt,
  außer die Spec sagt ausdrücklich etwas anderes.
- Kein git commit, kein git push — ich reviewe selbst.

STOPP UND FRAGEN — nicht raten, bei diesen Punkten:
- Ticket 0a: eigene workspace/AGENTS.md im Loadout, oder die aus
  installer/workspace-template/ erben? Das ist eine Architektur-Entscheidung.
- Ticket 0b: wohin gehören Run-Logs? (_logs/ im Workspace, Hermes-eigenes Logging,
  oder gar nicht?)
- Wenn eine Brain-Funktion sich nicht sinnvoll entpersonalisieren lässt: melden
  und überspringen, statt eine verwaschene Version zu bauen.
- Wenn ein Ticket eine Datei betrifft, die es nicht gibt und die Spec nicht sagt,
  ob sie neu angelegt werden soll.

ABSCHLUSS
Wenn alle Tickets durch sind oder du an einem Stopp-Punkt hängst:
- Liste je Ticket: erledigt / offen / gestoppt-mit-Grund
- Nenne alle neu angelegten und geänderten Dateien mit Pfad
- Führe die Entpersonalisierungs-Prüfung (a) einmal über ALLE geänderten Dateien
  zusammen aus und zeig das Ergebnis
- Sag ehrlich, was du nicht verifizieren konntest

Fang mit Ticket 0 an.
```

---

## Variante B — ein Ticket pro Aufruf (wenn A zu viel auf einmal ändert)

```
Arbeite AUSSCHLIESSLICH Ticket <N> aus
installer/skills-hidden/aimds-loadout/PORTIERUNG-AUS-BRAIN.md ab.

Regeln, Verifikation und Stopp-Punkte: identisch zum Durchlauf-Prompt in
PORTIERUNG-PROMPT.md (Variante A) — lies den Abschnitt dort und halte dich daran.

Wenn Ticket <N> fertig und verifiziert ist: Haken in der Spec setzen, zusammenfassen,
dann STOPP. Fang nicht mit dem nächsten Ticket an.
```

---

## Nachlauf-Prompt (nach der Portierung, für den Review)

```
Prüfe das Ergebnis der Portierung in
installer/skills-hidden/aimds-loadout/ — ohne etwas zu ändern.

1. Entpersonalisierung: grep -riE "patrick|fischi|dominik|gabriel|riley|lbbw|evn|
   cruxlab|bechtle|roechling|aok|iamds-intern" über skills/, guardrails/,
   workspace/, blueprints/. Jeder Treffer mit Datei + Zeile.
2. Tote Verweise: Jeder Verweis auf eine Datei oder einen anderen Skill —
   existiert das Ziel?
3. Widersprüche: Nennt ein Skill eine Cron-Zeit, die von blueprints/README.md
   abweicht? Behauptet ein Skill etwas, das AGENT-TOPOLOGIE.md anders festlegt?
4. Frontmatter: hat jede SKILL.md ein valides name + description?
5. Ausgabe-Konvention: verweisen die Skills auf guardrails/output-format.md,
   oder erfindet jeder sein eigenes Format?

Gib eine Liste: DATEI | ZEILE | BEFUND | SCHWERE (kritisch/wichtig/kosmetisch).
Keine Zusammenfassung, keine Einleitung. Nur die Liste.
Ändere nichts.
```

---

## Hinweise zum Einsatz

**Wo ausführen:** Claude Code im Repo `hermes-agent`. Der Brain-Vault muss lesbar
sein (`~/Documents/Claude/patrick-brain`) — bei getrennten Berechtigungen vorher
Zugriff geben, sonst scheitert Schritt 2 jedes Tickets.

**Warum Ticket 0 nicht autonom durchläuft:** Es enthält zwei
Architektur-Entscheidungen (AGENTS.md-Vererbung, Log-Ablage), die niemand raten
sollte. Rechne damit, dass der erste Lauf dort anhält und nachfragt. Das ist
gewollt.

**Nach dem Lauf:** Den Nachlauf-Prompt separat laufen lassen. Ein Agent, der
seine eigene Arbeit prüft, findet weniger als einer, der nur prüft — das ist
kein Misstrauen, sondern der Grund, warum Review ein eigener Schritt ist.

**Wenn etwas schiefgeht:** Alle Änderungen liegen in
`installer/skills-hidden/aimds-loadout/` und `installer/workspace-template/`.
`git diff` auf diese beiden Pfade zeigt den vollen Umfang, `git checkout --` macht
es zurück. Deshalb steht im Prompt ausdrücklich "kein commit".
