from django.conf import settings
from django.shortcuts import resolve_url

from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.contrib.messages.storage.fallback import FallbackStorage
from django.http import HttpResponse
from django.urls import reverse

from .models import Centre
from .access import (
    SESSION_CENTRE_KEY,
    centres_autorises,
    utilisateur_peut_acceder_centre,
)
from .middleware import CentreActifMiddleware
from .decorators import centre_actif_requis
from comptes.models import AffectationUtilisateur


Utilisateur = get_user_model()


@centre_actif_requis
def vue_protegee_test(request):
    return HttpResponse("Accès autorisé")


class SecuriteCentresTests(TestCase):

    def setUp(self):
        self.centre_a = Centre.objects.create(
            code="TESTA",
            nom="Centre de test A",
        )

        self.centre_b = Centre.objects.create(
            code="TESTB",
            nom="Centre de test B",
        )

        self.centre_c = Centre.objects.create(
            code="TESTC",
            nom="Centre de test C",
        )

        self.agent = Utilisateur.objects.create_user(
            username="agent_test",
            password="MotDePasseTest123!",
            type_compte="AGENT",
        )

        self.responsable = Utilisateur.objects.create_user(
            username="responsable_test",
            password="MotDePasseTest123!",
            type_compte="RESP",
        )

        self.admin_central = Utilisateur.objects.create_user(
            username="admin_test",
            password="MotDePasseTest123!",
            type_compte="ADMIN",
        )

        AffectationUtilisateur.objects.create(
            utilisateur=self.agent,
            centre=self.centre_a,
            role="AGENT",
        )

        AffectationUtilisateur.objects.create(
            utilisateur=self.responsable,
            centre=self.centre_a,
            role="RESP",
        )

        AffectationUtilisateur.objects.create(
            utilisateur=self.responsable,
            centre=self.centre_b,
            role="AGENT",
        )

    def test_agent_accede_a_son_centre(self):
        self.assertTrue(
            utilisateur_peut_acceder_centre(
                self.agent,
                self.centre_a,
            )
        )

    def test_agent_ne_peut_pas_acceder_a_un_autre_centre(self):
        self.assertFalse(
            utilisateur_peut_acceder_centre(
                self.agent,
                self.centre_b,
            )
        )

    def test_responsable_accede_a_ses_centres_affectes(self):
        centres = centres_autorises(self.responsable)

        self.assertEqual(
            set(centres),
            {self.centre_a, self.centre_b},
        )

    def test_responsable_ne_peut_pas_acceder_a_un_centre_non_affecte(self):
        self.assertFalse(
            utilisateur_peut_acceder_centre(
                self.responsable,
                self.centre_c,
            )
        )

    def test_admin_central_accede_a_tous_les_centres_actifs(self):
        centres = centres_autorises(self.admin_central)

        self.assertEqual(
            set(centres),
            {
                self.centre_a,
                self.centre_b,
                self.centre_c,
            },
        )

    def test_centre_inactif_refuse_meme_a_admin(self):
        self.centre_c.actif = False
        self.centre_c.save()

        self.assertFalse(
            utilisateur_peut_acceder_centre(
                self.admin_central,
                self.centre_c,
            )
        )

    def test_utilisateur_non_connecte_ne_peut_acceder_a_aucun_centre(self):
        self.assertFalse(
            utilisateur_peut_acceder_centre(
                AnonymousUser(),
                self.centre_a,
            )
        )


class CentreActifMiddlewareTests(TestCase):

    def setUp(self):
        self.factory = RequestFactory()

        self.centre_a = Centre.objects.create(
            code="MIDTA",
            nom="Centre middleware A",
        )

        self.centre_b = Centre.objects.create(
            code="MIDTB",
            nom="Centre middleware B",
        )

        self.centre_c = Centre.objects.create(
            code="MIDTC",
            nom="Centre middleware C",
        )

        self.agent = Utilisateur.objects.create_user(
            username="middleware_agent",
            password="MotDePasseTest123!",
            type_compte="AGENT",
        )

        self.responsable = Utilisateur.objects.create_user(
            username="middleware_responsable",
            password="MotDePasseTest123!",
            type_compte="RESP",
        )

        self.admin = Utilisateur.objects.create_user(
            username="middleware_admin",
            password="MotDePasseTest123!",
            type_compte="ADMIN",
        )

        AffectationUtilisateur.objects.create(
            utilisateur=self.agent,
            centre=self.centre_a,
            role="AGENT",
        )

        AffectationUtilisateur.objects.create(
            utilisateur=self.responsable,
            centre=self.centre_a,
            role="RESP",
        )

        AffectationUtilisateur.objects.create(
            utilisateur=self.responsable,
            centre=self.centre_b,
            role="RESP",
        )

    def executer_middleware(self, utilisateur, centre_id=None):
        request = self.factory.get("/")

        session_middleware = SessionMiddleware(
            lambda req: HttpResponse()
        )
        session_middleware.process_request(request)

        request.user = utilisateur

        if centre_id is not None:
            request.session[SESSION_CENTRE_KEY] = centre_id

        resultat = {}

        def get_response(req):
            resultat["centre_actif"] = req.centre_actif
            return HttpResponse()

        middleware = CentreActifMiddleware(get_response)
        middleware(request)

        return resultat["centre_actif"], request

    def test_un_seul_centre_est_selectionne_automatiquement(self):
        centre, request = self.executer_middleware(self.agent)

        self.assertEqual(centre, self.centre_a)
        self.assertEqual(
            request.session[SESSION_CENTRE_KEY],
            self.centre_a.id,
        )

    def test_plusieurs_centres_exigent_une_selection(self):
        centre, request = self.executer_middleware(
            self.responsable
        )

        self.assertIsNone(centre)
        self.assertNotIn(
            SESSION_CENTRE_KEY,
            request.session,
        )

    def test_centre_selectionne_est_reconnu(self):
        centre, request = self.executer_middleware(
            self.responsable,
            self.centre_b.id,
        )

        self.assertEqual(centre, self.centre_b)

    def test_changement_de_centre_est_pris_en_compte(self):
        premier_centre, _ = self.executer_middleware(
            self.responsable,
            self.centre_a.id,
        )

        nouveau_centre, _ = self.executer_middleware(
            self.responsable,
            self.centre_b.id,
        )

        self.assertEqual(premier_centre, self.centre_a)
        self.assertEqual(nouveau_centre, self.centre_b)

    def test_selection_non_autorisee_est_supprimee(self):
        centre, request = self.executer_middleware(
            self.agent,
            self.centre_b.id,
        )

        self.assertIsNone(centre)
        self.assertNotIn(
            SESSION_CENTRE_KEY,
            request.session,
        )

    def test_admin_peut_selectionner_un_centre_sans_affectation(self):
        centre, request = self.executer_middleware(
            self.admin,
            self.centre_c.id,
        )

        self.assertEqual(centre, self.centre_c)
        self.assertEqual(
            request.session[SESSION_CENTRE_KEY],
            self.centre_c.id,
        )


class CentreActifDecorateurTests(TestCase):

    def setUp(self):
        self.factory = RequestFactory()

        self.centre_a = Centre.objects.create(
            code="DECA",
            nom="Centre décorateur A",
        )

        self.centre_b = Centre.objects.create(
            code="DECB",
            nom="Centre décorateur B",
        )

        self.agent = Utilisateur.objects.create_user(
            username="decorateur_agent",
            password="MotDePasseTest123!",
            type_compte="AGENT",
        )

        self.admin = Utilisateur.objects.create_user(
            username="decorateur_admin",
            password="MotDePasseTest123!",
            type_compte="ADMIN",
        )

        AffectationUtilisateur.objects.create(
            utilisateur=self.agent,
            centre=self.centre_a,
            role="AGENT",
        )

    def executer_vue(self, utilisateur, centre=None):
        request = self.factory.get("/test-protegee/")

        session_middleware = SessionMiddleware(
            lambda req: HttpResponse()
        )
        session_middleware.process_request(request)

        request.user = utilisateur
        request.centre_actif = centre

        if centre is not None:
            request.session[SESSION_CENTRE_KEY] = centre.id

        request._messages = FallbackStorage(request)

        response = vue_protegee_test(request)

        return response, request

    def test_utilisateur_non_connecte_est_redirige_vers_connexion(self):
        response, _ = self.executer_vue(
            AnonymousUser()
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response.url.startswith(resolve_url(settings.LOGIN_URL))
        )

    def test_utilisateur_sans_centre_actif_est_redirige(self):
        response, _ = self.executer_vue(
            self.agent
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse("centres:selection"),
        )

    def test_agent_ne_peut_pas_acceder_au_centre_d_un_autre(self):
        response, request = self.executer_vue(
            self.agent,
            self.centre_b,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse("centres:selection"),
        )
        self.assertNotIn(
            SESSION_CENTRE_KEY,
            request.session,
        )

    def test_agent_autorise_peut_acceder_a_son_centre(self):
        response, _ = self.executer_vue(
            self.agent,
            self.centre_a,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.content.decode(),
            "Accès autorisé",
        )

    def test_admin_central_peut_acceder_sans_affectation(self):
        response, _ = self.executer_vue(
            self.admin,
            self.centre_b,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.content.decode(),
            "Accès autorisé",
        )
