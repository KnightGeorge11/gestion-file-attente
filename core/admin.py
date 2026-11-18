from centres.access import centres_autorises, est_administrateur_central


class CentreAdminMixin:
    """
    Isolation par centre dans l'administration Django : un compte qui n'est
    pas administrateur central ne voit et ne modifie que les données de ses
    centres autorisés. Le champ « centre » est limité à ces centres.
    """

    chemin_centre = "centre"

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if est_administrateur_central(request.user):
            return queryset
        return queryset.filter(**{f"{self.chemin_centre}__in": centres_autorises(request.user)})

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "centre" and not est_administrateur_central(request.user):
            kwargs["queryset"] = centres_autorises(request.user)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)
