"""Zentrales, datengetriebenes Produktregister der Clevoriq-Plattform.

Ein Clevoriq-Konto ist künftig NICHT an ein einzelnes Produkt gebunden
(siehe CLAUDE.md "Clevoriq Account & Hub") - welche Produkte ein
Benutzer nutzen darf, steht in der Datenbank (`speicher.produkt_zugriffe`),
nicht im Code. Dieses Modul hält nur das REGISTER der technisch
existierenden Produkte (Name/Icon/Beschreibung je stabilem
`product_key`) - bewusst getrennt von der Frage "hat DIESER Benutzer
Zugriff", die ausschließlich `speicher.py` beantwortet.

Aktuell existiert real nur EIN Produkt (`PRODUKT_DOCUMENTS`). Ein
künftiges zweites Produkt wird ausschließlich durch einen neuen Eintrag
in `PRODUKTE` + entsprechende `speicher.produkt_zugriff_gewaehren`-Aufrufe
ergänzt - nie durch eine neue if/else-Sonderbehandlung an anderer
Stelle im Code (siehe CLAUDE.md "kein hartverdrahtetes Produkt-if").
"""

PRODUKT_DOCUMENTS = "documents"

# product_key -> Anzeige-Metadaten. Rein informativ (Name/Icon/
# Beschreibung fürs Hub) - KEINE Zugriffs-/Berechtigungsinformation
# (die liegt ausschließlich in `speicher.produkt_zugriffe`).
PRODUKTE = {
    PRODUKT_DOCUMENTS: {
        "name": "Clevoriq Documents",
        "icon": "📄",
        "beschreibung": "Dokumente verstehen, analysieren und prüfen.",
    },
}

STANDARD_PLAN = "standard"
