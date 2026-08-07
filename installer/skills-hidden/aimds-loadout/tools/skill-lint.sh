#!/usr/bin/env bash
# skill-lint.sh — Konsistenz-Linter für das AIMDS-Loadout
#
# Prüft skills/*/SKILL.md auf die mechanisch prüfbaren Verstöße, die beim Portieren
# am häufigsten passieren: fehlendes Frontmatter, Personenbezug (Entpersonalisierung),
# fehlender Verweis auf die gemeinsame Ausgabe-Konvention, tote Verweise auf
# Guardrails/Skills und Cron-Angaben, die nicht zu blueprints/README.md passen.
#
# Bewusst reines Bash + grep — keine python3-/jq-Abhängigkeit auf dem Zielrechner.
# Was dieses Script NICHT kann: inhaltliche Widersprüche oder falsche Fachlogik.
#
# VERWENDUNG
#   bash tools/skill-lint.sh                 # das Loadout, in dem das Script liegt
#   bash tools/skill-lint.sh --root <pfad>   # anderes Loadout-Verzeichnis
#   bash tools/skill-lint.sh --quiet         # nur Funde, kein ✅
#
# EXIT-CODES   0 = sauber · 1 = 🔴 kritische Funde · 2 = 🟡 nur Warnungen
#
# FALSE POSITIVE UNTERDRÜCKEN
#   Zeile mit `<!-- lint-ok: <regel-id> -->` markieren.
#   Regeln loadout-weit ab-/abstufen über tools/lint-profile.json (Muster wie im
#   Brain-Linter): { "disable": [...], "exempt_output_format": [...] }.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"   # Loadout-Wurzel = Elternordner von tools/
QUIET=0
while [ $# -gt 0 ]; do
  case "$1" in
    --root)  ROOT="$2"; shift 2 ;;
    --quiet) QUIET=1; shift ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "Unbekannte Option: $1"; exit 64 ;;
  esac
done

SKILLS_DIR="$ROOT/skills"
[ -d "$SKILLS_DIR" ] || { echo "FEHLER: $SKILLS_DIR nicht gefunden"; exit 64; }
PROFILE="$SCRIPT_DIR/lint-profile.json"
BLUEPRINTS="$ROOT/blueprints/README.md"

# ── Profil einlesen (flache JSON-Arrays per grep, ohne jq) ───────────────────
json_array() {   # $1 = key, $2 = datei  →  ein Wert je Zeile
  [ -f "$2" ] || return 0
  tr '\n' ' ' < "$2" \
    | grep -oE "\"$1\"[[:space:]]*:[[:space:]]*\[[^]]*\]" \
    | grep -oE '"[^"]+"' | sed '1d' | tr -d '"'
}
DISABLED="$(json_array disable "$PROFILE" | tr '\n' ' ')"
EXEMPT_OF="$(json_array exempt_output_format "$PROFILE" | tr '\n' ' ')"
PROF_NOTE=""
[ -f "$PROFILE" ] && PROF_NOTE="$(tr '\n' ' ' < "$PROFILE" | grep -oE '"note"[[:space:]]*:[[:space:]]*"[^"]*"' | sed -E 's/.*:[[:space:]]*"([^"]*)"/\1/')"

is_disabled() { case " $DISABLED " in *" $1 "*) return 0 ;; *) return 1 ;; esac; }
in_list()     { case " $2 " in *" $1 "*) return 0 ;; *) return 1 ;; esac; }

# Blockliste der aus der Brain-Quelle bekannten Personen/Kunden — die einzige Stelle im
# Loadout, an der diese Namen absichtlich stehen (Regressions-Schutz beim Nach-Portieren).
# Kunden erweitern die Liste um ihre eigenen Namen, die nicht in Standard-Skills gehören.
PERSON_RE='patrick|fischi|dominik|gabriel|riley|lbbw|evn|cruxlab|bechtle|roechling|aok|iamds-intern'
# Report-/Briefing-Heuristik: nur solche Skills brauchen den output-format-Verweis.
REPORT_RE='briefing|digest|report|triage|review|brief|monitor'

crit=0; warn=0; BUF=""

# add sev(3/2) rule line msg  → hängt einen Fund an den Skill-Puffer
add() {
  local sev="$1" rule="$2" line="$3" msg="$4" mark loc
  is_disabled "$rule" && return 0
  if [ "$sev" = 3 ]; then mark="🔴"; crit=$((crit+1)); else mark="🟡"; warn=$((warn+1)); fi
  loc="—"; [ "$line" -gt 0 ] 2>/dev/null && loc="Z$line"
  BUF+="$(printf '     %s %6s  [%s] %s' "$mark" "$loc" "$rule" "$msg")"$'\n'
}

echo "🧩 skill-lint — $(basename "$ROOT")"
[ -n "$PROF_NOTE" ] && echo "   Profil: $PROF_NOTE"
[ -n "$(echo "$DISABLED" | tr -d ' ')" ] && echo "   deaktiviert: $DISABLED"
echo

lint_one() {
  local f="$1" name body fm pl ln ref sk bpname
  name="$(basename "$(dirname "$f")")"
  BUF=""
  body="$(cat "$f")"

  # ── Frontmatter: --- am Dateianfang, name:, description: ──────────────────
  if ! head -1 "$f" | grep -q '^---'; then
    add 3 frontmatter 1 "kein YAML-Frontmatter am Dateianfang"
  else
    fm="$(awk 'NR>1 && /^---/{exit} NR>1{print}' "$f")"
    echo "$fm" | grep -qE '^name:[[:space:]]*\S'        || add 3 frontmatter 0 "Frontmatter ohne name:"
    echo "$fm" | grep -qE '^description:[[:space:]]*\S' || add 3 frontmatter 0 "Frontmatter ohne description:"
  fi

  # ── Personenbezug ────────────────────────────────────────────────────────
  pl="$(grep -inE "$PERSON_RE" "$f" | grep -v 'lint-ok: personenbezug' | head -3)"
  if [ -n "$pl" ]; then
    while IFS= read -r hit; do
      [ -z "$hit" ] && continue
      add 3 personenbezug "${hit%%:*}" "Personenbezug: $(echo "${hit#*:}" | cut -c1-60)"
    done <<< "$pl"
  fi

  # ── output-format-Verweis (nur Report-Skills, exemptierbar) ───────────────
  if ! in_list "$name" "$EXEMPT_OF"; then
    if echo "$name $body" | grep -qiE "$REPORT_RE"; then
      grep -q 'output-format.md' "$f" || add 2 output-format-ref 0 "Report-Skill ohne Verweis auf guardrails/output-format.md"
    fi
  fi

  # ── tote guardrails-Verweise ──────────────────────────────────────────────
  while IFS= read -r ref; do
    [ -z "$ref" ] && continue
    if [ ! -f "$ROOT/$ref" ]; then
      ln="$(grep -nF "$ref" "$f" | head -1 | cut -d: -f1)"
      add 3 dead-ref "${ln:-0}" "toter Verweis: $ref existiert nicht im Loadout"
    fi
  done < <(grep -oE 'guardrails/[A-Za-z0-9._-]+\.md' "$f" | sort -u)

  # ── tote Skill-Verweise (`name`-Skill) ────────────────────────────────────
  while IFS= read -r sk; do
    [ -z "$sk" ] && continue
    [ "$sk" = "$name" ] && continue
    if [ ! -d "$SKILLS_DIR/$sk" ]; then
      ln="$(grep -nE "\`$sk\`-[Ss]kill" "$f" | head -1 | cut -d: -f1)"
      add 3 dead-ref "${ln:-0}" "toter Skill-Verweis: '$sk' existiert nicht"
    fi
  done < <(grep -oE '`[a-z0-9-]+`-[Ss]kill' "$f" | grep -oE '`[a-z0-9-]+`' | tr -d '`' | sort -u)

  # ── Cron-Konsistenz gegen blueprints/README.md ────────────────────────────
  bpname="$(grep -A2 'blueprint:' "$f" | grep -E '^[[:space:]]*name:' | head -1 | sed -E 's/.*name:[[:space:]]*//; s/["'"'"']//g' | tr -d ' ')"
  if [ -n "$bpname" ]; then
    if [ ! -f "$BLUEPRINTS" ]; then
      add 2 cron-consistency 0 "Blueprint '$bpname' deklariert, aber blueprints/README.md fehlt"
    elif ! grep -q "$bpname" "$BLUEPRINTS"; then
      add 2 cron-consistency 0 "Blueprint '$bpname' nicht in blueprints/README.md gelistet"
    fi
  fi

  if [ -z "$BUF" ]; then
    [ "$QUIET" -eq 1 ] || echo "  ✅ $name"
  else
    echo "  $name"
    printf '%s' "$BUF"
    echo
  fi
}

for f in "$SKILLS_DIR"/*/SKILL.md; do
  [ -f "$f" ] || continue
  lint_one "$f"
done

echo "────────────────────────────────────────────────────────────"
echo "Ergebnis: 🔴 $crit kritisch · 🟡 $warn Warnung/en"
[ "$crit" -gt 0 ] && { echo "🔴 Kritische Funde — vor dem Ausrollen beheben."; exit 1; }
[ "$warn" -gt 0 ] && { echo "🟡 Warnungen — prüfen, aber nicht blockierend."; exit 2; }
echo "✅ Sauber."
exit 0
