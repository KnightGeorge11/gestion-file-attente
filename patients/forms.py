from django import forms

from .models import Patient, Entreprise


class PatientForm(forms.ModelForm):
    class Meta:
        model = Patient
        fields = [
            "nom",
            "prenom",
            "date_naissance",
            "sexe",
            "telephone",
            "adresse",
            "profession",
            "entreprise",
            "matricule_entreprise",
        ]

        widgets = {
            "nom": forms.TextInput(attrs={
                "placeholder": "Nom du patient",
            }),
            "prenom": forms.TextInput(attrs={
                "placeholder": "Prénom du patient",
            }),
            "date_naissance": forms.DateInput(attrs={
                "type": "date",
            }),
            "sexe": forms.TextInput(attrs={
                "placeholder": "Masculin, Féminin...",
            }),
            "telephone": forms.TextInput(attrs={
                "placeholder": "Téléphone",
            }),
            "adresse": forms.Textarea(attrs={
                "rows": 2,
            }),
            "profession": forms.TextInput(),
            "matricule_entreprise": forms.TextInput(),
        }

        labels = {
            "nom": "Nom *",
            "prenom": "Prénom",
            "date_naissance": "Date de naissance",
            "sexe": "Sexe",
            "telephone": "Téléphone",
            "adresse": "Adresse",
            "profession": "Profession",
            "entreprise": "Entreprise",
            "matricule_entreprise": "Matricule dans l'entreprise",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["entreprise"].queryset = Entreprise.objects.filter(
            actif=True
        )

        self.fields["entreprise"].required = False
        self.fields["nom"].required = True

    def clean_nom(self):
        return self.cleaned_data["nom"].strip()

    def clean_prenom(self):
        return self.cleaned_data["prenom"].strip()