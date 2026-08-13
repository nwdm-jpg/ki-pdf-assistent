"""Formatgerechte Quellenangaben für Chunks aus verschiedenen Dokumenttypen.

Gegenstück zu den entsprechenden Funktionen in `pdf_logik.py`, das
bewusst unverändert und ausschließlich von `app.py` (PDF-only, ein
Dokument, Seiten-Dicts, 2-elementige Quellen-Tupel) genutzt wird. Dieses
Modul ist die EINE Stelle für Quellenformatierung im Mehrformat-Web-App-
Pfad (Chat in `web_app.py`, Analyse & Vergleich sowie Dokument prüfen
über `ki_analyse.py`) - keine der drei Stellen dupliziert eigene
Formatierungslogik, sie importieren alle von hier.

Ein Chunk-Eintrag trägt hier zusätzlich zu "dateiname"/"seitennummer"/
"text" optional "einheit_typ" (einer von EINHEIT_WOERTER) und
"einheit_anzeige" (Anzeigewert, z. B. ein Tabellenblattname) - fehlen
diese Felder, wird "seite" bzw. str(seitennummer) angenommen, sodass
reine PDF-Chunks ohne Änderung weiterhin funktionieren.

Quellen (aus `verwendete_quellen`) sind Dicts, aber `formatiere_quellenhinweis`
akzeptiert zusätzlich alte, bereits in `nachrichten.quellen` gespeicherte
2-elementige [dateiname, seitennummer]-Paare (aus der Zeit vor diesem
Modul) unverändert - so ist keine Datenmigration bestehender Chats nötig.
"""

EINHEIT_WOERTER = {
    "seite": ("Seite", "Seiten"),
    "folie": ("Folie", "Folien"),
    "tabellenblatt": ("Tabellenblatt", "Tabellenblätter"),
    "abschnitt": ("Abschnitt", "Abschnitte"),
}

_STANDARD_EINHEIT_TYP = "seite"


def _einheit_anzeige(eintrag):
    return eintrag.get("einheit_anzeige") or str(eintrag["seitennummer"])


def relevanten_text_zusammenstellen(chunks):
    """Baut aus den besten Chunks den Text für den KI-Prompt zusammen.

    Formatgerecht: "--- dateiname, Folie 7 ---" statt immer "Seite".
    """
    teile = []

    for eintrag in chunks:
        label = EINHEIT_WOERTER.get(
            eintrag.get("einheit_typ", _STANDARD_EINHEIT_TYP),
            EINHEIT_WOERTER[_STANDARD_EINHEIT_TYP],
        )[0]
        teile.append(
            f"--- {eintrag['dateiname']}, {label} {_einheit_anzeige(eintrag)} ---\n"
            f"{eintrag['text']}"
        )

    return "\n\n".join(teile)


def verwendete_quellen(chunks):
    """Extrahiert Quellen-Dicts aus den besten Chunks.

    Jede Quelle: {"dateiname", "seitennummer", "einheit_typ", "einheit_anzeige"}.
    """
    return [
        {
            "dateiname": eintrag["dateiname"],
            "seitennummer": eintrag["seitennummer"],
            "einheit_typ": eintrag.get("einheit_typ", _STANDARD_EINHEIT_TYP),
            "einheit_anzeige": _einheit_anzeige(eintrag),
        }
        for eintrag in chunks
    ]


def _quelle_normalisieren(quelle):
    """Vereinheitlicht neue Quellen-Dicts und alte [dateiname, seitennummer]-Paare."""
    if isinstance(quelle, dict):
        return {
            "dateiname": quelle["dateiname"],
            "einheit_typ": quelle.get("einheit_typ", _STANDARD_EINHEIT_TYP),
            "einheit_anzeige": quelle.get("einheit_anzeige")
            or str(quelle["seitennummer"]),
        }

    dateiname, seitennummer = quelle[0], quelle[1]
    return {
        "dateiname": dateiname,
        "einheit_typ": _STANDARD_EINHEIT_TYP,
        "einheit_anzeige": str(seitennummer),
    }


def _ist_numerisch(anzeigewerte):
    return all(wert.lstrip("-").isdigit() for wert in anzeigewerte)


def _anzeigewerte_sortieren(anzeigewerte):
    if _ist_numerisch(anzeigewerte):
        return sorted(anzeigewerte, key=int)
    return sorted(anzeigewerte)


def formatiere_quellenhinweis(quellen):
    """Formatiert Quellen als lesbaren, formatgerechten Quellenhinweis.

    Gruppiert nach Dateiname (und je Datei nach Einheitentyp, falls
    gemischt), entfernt Duplikate und sortiert. Numerische Anzeigewerte
    (Seiten-/Folien-/Abschnittsnummern) werden aufsteigend ohne
    Anführungszeichen aufgelistet ("Seiten 3, 7"), nicht-numerische
    (z. B. Tabellenblattnamen) alphabetisch und in Anführungszeichen
    ('Tabellenblätter "Kosten", "Umsatz"'). Gibt "" zurück, wenn keine
    Quellen übergeben wurden.
    """
    if not quellen:
        return ""

    gruppiert = {}

    for quelle in quellen:
        normalisiert = _quelle_normalisieren(quelle)
        schluessel = (normalisiert["dateiname"], normalisiert["einheit_typ"])
        gruppiert.setdefault(schluessel, set()).add(normalisiert["einheit_anzeige"])

    je_datei = {}

    for (dateiname, einheit_typ), anzeigewerte in gruppiert.items():
        je_datei.setdefault(dateiname, []).append((einheit_typ, anzeigewerte))

    teile = []

    for dateiname in sorted(je_datei):
        unterteile = []

        for einheit_typ, anzeigewerte in je_datei[dateiname]:
            singular, plural = EINHEIT_WOERTER.get(
                einheit_typ, EINHEIT_WOERTER[_STANDARD_EINHEIT_TYP]
            )
            sortiert = _anzeigewerte_sortieren(anzeigewerte)
            wort = singular if len(sortiert) == 1 else plural

            if _ist_numerisch(sortiert):
                unterteile.append(f"{wort} " + ", ".join(sortiert))
            else:
                unterteile.append(
                    f"{wort} " + ", ".join(f'"{wert}"' for wert in sortiert)
                )

        teile.append(f"{dateiname} (" + "; ".join(unterteile) + ")")

    return "Quellen: " + "; ".join(teile)
