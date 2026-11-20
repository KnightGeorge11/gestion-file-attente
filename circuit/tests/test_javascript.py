"""
Contrôle de syntaxe minimal des scripts (audit du 20/11/2026) : un `});` en
trop dans borne_ticket.html empêchait tout le script de la page ticket de la
borne de s'exécuter (retour à l'accueil, impression Bluetooth). Node n'étant
pas disponible partout, on vérifie l'équilibre des (), [] et {} en ignorant
chaînes, commentaires et expressions régulières.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

RACINE = Path(settings.BASE_DIR)
BALISE_DJANGO = re.compile(r"{%.*?%}|{{.*?}}", re.S)
SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)
PAIRES = {")": "(", "]": "[", "}": "{"}
AVANT_REGEX = set("(,=:[!&|?{};+-*%<>~^") | {""}


def desequilibre(code):
    """Renvoie None si le code est équilibré, sinon une description."""
    pile = []
    i, n = 0, len(code)
    precedent = ""  # dernier caractère significatif
    while i < n:
        c = code[i]
        if c in "'\"`":
            i += 1
            while i < n and code[i] != c:
                i += 2 if code[i] == "\\" else 1
            precedent = c
        elif code.startswith("//", i):
            fin = code.find("\n", i)
            i = n if fin < 0 else fin
            continue
        elif code.startswith("/*", i):
            fin = code.find("*/", i + 2)
            i = n if fin < 0 else fin + 2
            continue
        elif c == "/" and precedent in AVANT_REGEX:
            i += 1
            classe = False
            while i < n and (code[i] != "/" or classe):
                if code[i] == "\\":
                    i += 1
                elif code[i] == "[":
                    classe = True
                elif code[i] == "]":
                    classe = False
                i += 1
            precedent = "/"
        elif c in "([{":
            pile.append((c, code.count("\n", 0, i) + 1))
            precedent = c
        elif c in ")]}":
            ligne = code.count("\n", 0, i) + 1
            if not pile or pile[-1][0] != PAIRES[c]:
                return f"« {c} » inattendu ligne {ligne}"
            pile.pop()
            precedent = c
        elif not c.isspace():
            precedent = c
        i += 1
    if pile:
        return f"« {pile[-1][0]} » non fermé (ligne {pile[-1][1]})"
    return None


def scripts_a_verifier():
    for chemin in sorted(RACINE.glob("**/templates/**/*.html")):
        if ".venv" in chemin.parts:
            continue
        texte = chemin.read_text(encoding="utf-8")
        for numero, bloc in enumerate(SCRIPT.findall(texte), start=1):
            # Les balises Django sont remplacées par une valeur neutre.
            yield f"{chemin.relative_to(RACINE)} (script {numero})", BALISE_DJANGO.sub("0", bloc)
    for chemin in sorted((RACINE / "static" / "js").glob("*.js")):
        if chemin.name.startswith("htmx"):
            continue
        yield str(chemin.relative_to(RACINE)), chemin.read_text(encoding="utf-8")


class SyntaxeJavascriptTests(SimpleTestCase):
    def test_scripts_equilibres(self):
        verifies = 0
        for nom, code in scripts_a_verifier():
            with self.subTest(script=nom):
                self.assertIsNone(desequilibre(code))
            verifies += 1
        self.assertGreater(verifies, 3)

    def test_detecte_une_accolade_en_trop(self):
        self.assertIsNotNone(desequilibre("f(function () { a(); });\n});"))
        self.assertIsNone(desequilibre("var r = /[)}]/g; s = 'a)'; // )\n f(1);"))
