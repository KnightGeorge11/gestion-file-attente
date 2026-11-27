"""Formulaires simples des comptes (décision du 27/11/2026)."""

from django import forms
from django.contrib.auth.password_validation import validate_password

from .gestion import ROLES_AVEC_SALLE, ROLES_AVEC_SERVICE, roles_attribuables, service_suggere, services_du_centre
from .models import Utilisateur


class _BaseCompte(forms.Form):
    role = forms.TypedChoiceField(label="Rôle", coerce=int)
    services = forms.TypedMultipleChoiceField(label="Service", coerce=int, required=False,
                                              widget=forms.CheckboxSelectMultiple)
    salle = forms.CharField(label="Salle (annoncée à l'écran)", max_length=50, required=False)

    def __init__(self, *args, utilisateur, centre, **kwargs):
        super().__init__(*args, **kwargs)
        self.centre = centre
        self.roles = {g.pk: g for g in roles_attribuables(utilisateur)}
        self.liste_services = services_du_centre(centre)
        self.fields["role"].choices = [("", "— Choisir —")] + [(pk, g.name) for pk, g in self.roles.items()]
        self.fields["services"].choices = [(s.pk, s.nom) for s in self.liste_services]

    def clean(self):
        donnees = super().clean()
        groupe = self.roles.get(donnees.get("role"))
        if groupe is None:
            return donnees
        choisis = [s for s in self.liste_services if s.pk in set(donnees.get("services") or [])]
        if groupe.name in ROLES_AVEC_SERVICE and not choisis:
            suggere = service_suggere(groupe.name, self.liste_services)
            if suggere is None:
                self.add_error("services", f"Choisissez le service où travaille ce compte ({groupe.name}).")
            else:
                choisis = [suggere]
        donnees["groupe"] = groupe
        donnees["services_choisis"] = choisis
        if groupe.name not in ROLES_AVEC_SALLE:
            donnees["salle"] = ""
        return donnees

    def roles_avec_salle(self):
        return [pk for pk, g in self.roles.items() if g.name in ROLES_AVEC_SALLE]

    def roles_et_services(self):
        """{id du rôle: id du service proposé} pour cocher le service en choisissant le rôle."""
        return {
            pk: s.pk for pk, g in self.roles.items()
            if (s := service_suggere(g.name, self.liste_services)) is not None
        }


class CompteForm(_BaseCompte):
    prenom = forms.CharField(label="Prénom", max_length=150, required=False)
    nom = forms.CharField(label="Nom", max_length=150)
    identifiant = forms.CharField(label="Identifiant de connexion", max_length=150, required=False,
                                  help_text="Laissez vide : il est créé à partir du nom (ex. jean.rakoto).")
    mot_de_passe = forms.CharField(label="Mot de passe", required=False, strip=False,
                                   widget=forms.PasswordInput(render_value=True),
                                   help_text="Laissez vide : un mot de passe provisoire est créé et affiché une fois.")

    field_order = ["prenom", "nom", "role", "services", "salle", "identifiant", "mot_de_passe"]

    def clean_identifiant(self):
        identifiant = self.cleaned_data["identifiant"].strip().lower()
        if identifiant and Utilisateur.objects.filter(username=identifiant).exists():
            raise forms.ValidationError("Cet identifiant est déjà pris.")
        if identifiant and not all(c.isalnum() or c in "._-@+" for c in identifiant):
            raise forms.ValidationError("Lettres, chiffres, point, tiret ou soulignement seulement.")
        return identifiant

    def clean_mot_de_passe(self):
        mot_de_passe = self.cleaned_data["mot_de_passe"]
        if mot_de_passe:
            validate_password(mot_de_passe)
        return mot_de_passe


class ListeComptesForm(_BaseCompte):
    noms = forms.CharField(
        label="Personnes", widget=forms.Textarea(attrs={"rows": 8}),
        help_text="Une personne par ligne : prénom puis nom (ex. Jean Rakoto).",
    )

    field_order = ["noms", "role", "services", "salle"]

    def clean_noms(self):
        lignes = [" ".join(l.split()) for l in self.cleaned_data["noms"].splitlines()]
        lignes = [l for l in lignes if l]
        if not lignes:
            raise forms.ValidationError("Écrivez au moins un nom.")
        if len(lignes) > 100:
            raise forms.ValidationError("100 personnes au plus à la fois.")
        return lignes


class ModifierCompteForm(_BaseCompte):
    prenom = forms.CharField(label="Prénom", max_length=150, required=False)
    nom = forms.CharField(label="Nom", max_length=150, required=False)

    field_order = ["prenom", "nom", "role", "services", "salle"]
