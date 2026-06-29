from django.contrib.auth import get_user_model
from django.urls import reverse
from django.test import override_settings
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from rest_framework.test import APITestCase

from .models import AnalyticsSnapshot, Branch, Courier, CourierMessage, Customer, Delivery, Notification, NotificationAttempt, Organization, OrganizationPermission, OrganizationRole, OrganizationUser, PlatformPermission, PlatformRole, PlatformUser, PublicSite, Upload
from .security import hash_password, totp_code
from .services import DeliveryStateError, aggregate_analytics_snapshot, assign_courier, create_delivery, record_tracking, release_due_scheduled_deliveries, transition_delivery


class BackendFoundationTests(APITestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Swift Couriers", slug="swift-couriers", subdomain="swift")
        self.other_org = Organization.objects.create(name="Other Logistics", slug="other-logistics", subdomain="other")
        perms = {}
        for code in ["view_orders", "manage_orders", "view_fleet", "manage_fleet", "manage_staff", "view_customers", "view_analytics", "manage_settings"]:
            perms[code] = OrganizationPermission.objects.create(organization=self.org, code=code)
        self.role = OrganizationRole.objects.create(organization=self.org, key="owner", label="Owner")
        self.role.permissions.set(perms.values())
        self.user = OrganizationUser.objects.create(
            organization=self.org,
            name="Jordan Reyes",
            email="jordan@swiftcouriers.com",
            initials="JR",
            role=self.role,
            status=OrganizationUser.Status.ACTIVE,
            password_hash=hash_password("ChangeMe123!"),
        )
        self.courier = Courier.objects.create(
            organization=self.org,
            name="Amina Yusuf",
            initials="AY",
            status=Courier.Status.AVAILABLE,
            phone="+2348010004101",
            email="amina.yusuf@swiftcouriers.com",
            password_hash=hash_password("ChangeMe123!"),
        )
        self.platform_role = PlatformRole.objects.create(key="super_admin", label="Super Admin")
        self.impersonate_permission = PlatformPermission.objects.create(code="impersonate_tenant")
        self.metrics_permission = PlatformPermission.objects.create(code="view_platform_metrics")
        self.manage_orgs_permission = PlatformPermission.objects.create(code="manage_organizations")
        self.suspend_orgs_permission = PlatformPermission.objects.create(code="suspend_organization")
        self.platform_role.permissions.set([
            self.impersonate_permission,
            self.metrics_permission,
            self.manage_orgs_permission,
            self.suspend_orgs_permission,
        ])
        self.platform_user = get_user_model().objects.create_user(
            username="admin@streak.local",
            email="admin@streak.local",
            password="ChangeMe123!",
            first_name="Platform",
            last_name="Admin",
            is_staff=True,
            is_active=True,
        )
        self.platform_profile = PlatformUser.objects.create(
            user=self.platform_user,
            email=self.platform_user.email,
            name="Platform Admin",
            role=self.platform_role,
            status=PlatformUser.Status.ACTIVE,
        )

    def test_tenant_queryset_scopes_rows_to_organization(self):
        Delivery.objects.create(
            organization=self.other_org,
            reference="DX-OTHER",
            customer_name="Other Customer",
            pickup_address="A",
            delivery_address="B",
        )
        create_delivery(
            organization=self.org,
            reference="DX-100",
            customer_name="Swift Customer",
            pickup_address="A",
            delivery_address="B",
        )

        self.assertEqual(Delivery.objects.for_organization(self.org).count(), 1)
        self.assertEqual(Delivery.objects.for_organization(self.org).first().reference, "DX-100")

    def test_delivery_assignment_and_transition_rules(self):
        delivery = create_delivery(
            organization=self.org,
            reference="DX-101",
            customer_name="Ada",
            pickup_address="Warehouse",
            delivery_address="Customer",
        )
        assigned = assign_courier(organization=self.org, delivery=delivery, courier=self.courier)
        self.assertEqual(assigned.status, Delivery.Status.ASSIGNED)
        delivered = transition_delivery(organization=self.org, delivery=assigned, status=Delivery.Status.PICKED_UP)
        delivered = transition_delivery(organization=self.org, delivery=delivered, status=Delivery.Status.IN_TRANSIT)
        delivered = transition_delivery(organization=self.org, delivery=delivered, status=Delivery.Status.DELIVERED)
        with self.assertRaises(DeliveryStateError):
            transition_delivery(organization=self.org, delivery=delivered, status=Delivery.Status.CANCELLED)

    def test_login_issues_tokens_and_authenticated_api_is_tenant_scoped(self):
        delivery = create_delivery(
            organization=self.org,
            reference="DX-102",
            customer_name="Ada",
            pickup_address="Warehouse",
            delivery_address="Customer",
        )
        login = self.client.post(
            reverse("organization-login"),
            {"organization": "swift", "email": self.user.email, "password": "ChangeMe123!"},
            format="json",
        )
        self.assertEqual(login.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
        response = self.client.get("/api/deliveries/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["id"] if isinstance(response.data, dict) and "results" in response.data else response.data[0]["id"], str(delivery.id))

    def test_business_signup_bootstraps_owner_workspace(self):
        response = self.client.post(
            reverse("business-signup"),
            {
                "owner_name": "Maya Stone",
                "owner_email": "maya@rapid.test",
                "owner_password": "ChangeMe123!",
                "company_name": "Rapid Logistics",
                "subdomain": "rapid",
                "company_size": "6-20",
                "branch_count": 3,
                "country": "Nigeria",
                "currency": "NGN",
                "location": "Lagos, Nigeria",
                "brand_color": "#2563eb",
                "enable_public_site": True,
                "site_headline": "Rapid city deliveries",
                "site_description": "Book and track Rapid deliveries online.",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertIn("access", response.data)
        organization = Organization.objects.get(subdomain="rapid")
        self.assertEqual(organization.metadata["settings"]["currency"], "NGN")
        self.assertEqual(organization.metadata["onboarding"]["branch_count"], 3)
        self.assertEqual(organization.metadata["onboarding"]["location"], "Lagos, Nigeria")
        owner = OrganizationUser.objects.get(organization=organization, email="maya@rapid.test")
        self.assertEqual(owner.role.key, "owner")
        self.assertTrue(owner.role.permissions.filter(code="manage_settings").exists())
        site = PublicSite.objects.get(organization=organization)
        self.assertTrue(site.enabled)
        self.assertEqual(site.headline, "Rapid city deliveries")
        self.assertEqual(response.data["organization"]["id"], str(organization.id))
        self.assertEqual(response.data["user"]["id"], str(owner.id))

    def test_courier_login_can_create_own_tracking_update(self):
        login = self.client.post(
            reverse("courier-login"),
            {"organization": "swift", "phone": self.courier.phone, "password": "ChangeMe123!"},
            format="json",
        )
        self.assertEqual(login.status_code, 200)
        self.assertIn("access", login.data)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")

        response = self.client.post(
            reverse("tracking-list"),
            {
                "latitude": "10.512300",
                "longitude": "7.418800",
                "accuracy": "6.40",
                "battery_level": 77,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.courier.refresh_from_db()
        self.assertEqual(self.courier.current_latitude, Decimal("10.512300"))
        self.assertEqual(self.courier.current_longitude, Decimal("7.418800"))
        self.assertEqual(self.courier.battery_level, 77)

    def test_courier_can_login_with_email_and_load_profile(self):
        login = self.client.post(
            reverse("courier-login"),
            {"organization": "swift", "email": self.courier.email, "password": "ChangeMe123!"},
            format="json",
        )
        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.data["courier"]["email"], self.courier.email)

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
        profile = self.client.get(reverse("courier-own-profile"))
        self.assertEqual(profile.status_code, 200)
        self.assertEqual(profile.data["email"], self.courier.email)

    def test_dispatch_and_courier_can_exchange_messages(self):
        staff_login = self.client.post(
            reverse("organization-login"),
            {"organization": "swift", "email": self.user.email, "password": "ChangeMe123!"},
            format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {staff_login.data['access']}")

        with patch("core.views.broadcast_chat_event") as broadcast:
            sent = self.client.post(
                reverse("courier-messages-list"),
                {"courier_id": str(self.courier.id), "body": "Head to the Kaduna depot."},
                format="json",
            )
        self.assertEqual(sent.status_code, 201)
        self.assertEqual(sent.data["sender_type"], CourierMessage.SenderType.DISPATCH)
        self.assertEqual(str(sent.data["contact_user"]), str(self.user.id))
        broadcast.assert_called_once()
        self.assertEqual(broadcast.call_args.args[1], "courier.message_created")
        self.assertEqual(broadcast.call_args.args[2]["chat_id"], f"courier:{self.courier.id}:manager:{self.user.id}")

        courier_login = self.client.post(
            reverse("courier-login"),
            {"organization": "swift", "phone": self.courier.phone, "password": "ChangeMe123!"},
            format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {courier_login.data['access']}")
        contacts = self.client.get(reverse("courier-own-message-contacts"))
        self.assertEqual(contacts.status_code, 200)
        self.assertEqual(contacts.data[0]["id"], str(self.user.id))

        inbox = self.client.get(reverse("courier-own-messages"), {"contact_user_id": str(self.user.id)})
        self.assertEqual(inbox.status_code, 200)
        self.assertEqual(inbox.data[0]["body"], "Head to the Kaduna depot.")

        with patch("core.views.broadcast_chat_event") as broadcast:
            reply = self.client.post(
                reverse("courier-own-messages"),
                {"contact_user_id": str(self.user.id), "body": "On my way."},
                format="json",
            )
        self.assertEqual(reply.status_code, 201)
        self.assertEqual(reply.data["sender_type"], CourierMessage.SenderType.COURIER)
        self.assertEqual(str(reply.data["contact_user"]), str(self.user.id))
        broadcast.assert_called_once()

    def test_courier_message_threads_are_separated_by_manager_contact(self):
        second_manager = OrganizationUser.objects.create(
            organization=self.org,
            name="Fatima Bello",
            email="fatima@swiftcouriers.com",
            initials="FB",
            role=self.role,
            status=OrganizationUser.Status.ACTIVE,
            password_hash=hash_password("ChangeMe123!"),
        )
        CourierMessage.objects.create(
            organization=self.org,
            courier=self.courier,
            contact_user=self.user,
            sender_user=self.user,
            sender_type=CourierMessage.SenderType.DISPATCH,
            body="Jordan thread",
        )
        CourierMessage.objects.create(
            organization=self.org,
            courier=self.courier,
            contact_user=second_manager,
            sender_user=second_manager,
            sender_type=CourierMessage.SenderType.DISPATCH,
            body="Fatima thread",
        )

        courier_login = self.client.post(
            reverse("courier-login"),
            {"organization": "swift", "email": self.courier.email, "password": "ChangeMe123!"},
            format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {courier_login.data['access']}")

        first_thread = self.client.get(reverse("courier-own-messages"), {"contact_user_id": str(self.user.id)})
        second_thread = self.client.get(reverse("courier-own-messages"), {"contact_user_id": str(second_manager.id)})

        self.assertEqual([message["body"] for message in first_thread.data], ["Jordan thread"])
        self.assertEqual([message["body"] for message in second_thread.data], ["Fatima thread"])

    def test_courier_can_view_profile_and_manage_assigned_tasks(self):
        delivery = create_delivery(
            organization=self.org,
            reference="DX-COURIER-1",
            customer_name="Ada",
            customer_phone="+2348090000001",
            pickup_address="Warehouse",
            delivery_address="Customer",
        )
        assign_courier(organization=self.org, delivery=delivery, courier=self.courier)
        login = self.client.post(
            reverse("courier-login"),
            {"organization": "swift", "phone": self.courier.phone, "password": "ChangeMe123!"},
            format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")

        profile = self.client.get(reverse("courier-own-profile"))
        self.assertEqual(profile.status_code, 200)
        self.assertEqual(profile.data["id"], str(self.courier.id))

        updated_profile = self.client.patch(
            reverse("courier-own-profile"),
            {"preferences": {"autoStartTracking": True, "highAccuracy": True}},
            format="json",
        )
        self.assertEqual(updated_profile.status_code, 200)
        self.courier.refresh_from_db()
        self.assertEqual(self.courier.metadata["preferences"]["autoStartTracking"], True)

        tasks = self.client.get(reverse("courier-own-tasks"))
        self.assertEqual(tasks.status_code, 200)
        self.assertEqual(tasks.data[0]["id"], str(delivery.id))

        accepted = self.client.post(
            reverse("courier-own-task-transition", kwargs={"delivery_id": delivery.id}),
            {"status": Delivery.Status.ACCEPTED},
            format="json",
        )
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.data["status"], Delivery.Status.ACCEPTED)

    def test_owner_can_manage_branches(self):
        login = self.client.post(
            reverse("organization-login"),
            {"organization": "swift", "email": self.user.email, "password": "ChangeMe123!"},
            format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")

        kaduna = self.client.post(
            reverse("branches-list"),
            {"name": "Kaduna Branch", "code": "kaduna", "state": "Kaduna", "city": "Kaduna", "is_default": True},
            format="json",
        )
        self.assertEqual(kaduna.status_code, 201)
        self.assertEqual(kaduna.data["is_default"], True)

        abuja = self.client.post(
            reverse("branches-list"),
            {"name": "Abuja Branch", "code": "abuja", "state": "FCT", "city": "Abuja", "is_default": True},
            format="json",
        )
        self.assertEqual(abuja.status_code, 201)
        self.assertEqual(Branch.objects.get(id=kaduna.data["id"]).is_default, False)

        updated = self.client.patch(
            reverse("branches-detail", kwargs={"pk": abuja.data["id"]}),
            {"address": "Central Business District"},
            format="json",
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.data["address"], "Central Business District")

        default_delete = self.client.delete(reverse("branches-detail", kwargs={"pk": abuja.data["id"]}))
        self.assertEqual(default_delete.status_code, 400)

        deleted = self.client.delete(reverse("branches-detail", kwargs={"pk": kaduna.data["id"]}))
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(Branch.objects.get(id=kaduna.data["id"]).status, Branch.Status.INACTIVE)

    @override_settings(GOOGLE_MAPS_API_KEY="test-google-key", GOOGLE_MAPS_MAP_ID="test-map-id")
    def test_google_maps_config_is_authenticated_and_fleet_scoped(self):
        denied = self.client.get(reverse("google-maps-config"))
        self.assertIn(denied.status_code, {401, 403})

        login = self.client.post(
            reverse("organization-login"),
            {"organization": "swift", "email": self.user.email, "password": "ChangeMe123!"},
            format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
        response = self.client.get(reverse("google-maps-config"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["api_key"], "test-google-key")
        self.assertEqual(response.data["map_id"], "test-map-id")
        self.assertEqual(response.data["configured"], True)

    def test_platform_login_issues_tokens(self):
        response = self.client.post(
            reverse("platform-login"),
            {"email": self.platform_user.email, "password": "ChangeMe123!"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertEqual(response.data["user"]["role"], "super_admin")

    def test_platform_organization_management(self):
        platform_login = self.client.post(
            reverse("platform-login"),
            {"email": self.platform_user.email, "password": "ChangeMe123!"},
            format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {platform_login.data['access']}")

        created = self.client.post(
            "/api/platform/organizations/",
            {"name": "North Ops", "slug": "north-ops", "subdomain": "north", "subscription_plan": "Growth"},
            format="json",
        )
        self.assertEqual(created.status_code, 201)
        suspended = self.client.post(f"/api/platform/organizations/{created.data['id']}/suspend/", {}, format="json")
        self.assertEqual(suspended.status_code, 200)
        self.assertEqual(suspended.data["status"], Organization.Status.SUSPENDED)

    def test_health_readiness_metrics_and_request_timing(self):
        health = self.client.get(reverse("health"))
        self.assertEqual(health.status_code, 200)
        self.assertIn("X-Response-Time-Ms", health)

        readiness = self.client.get(reverse("health-ready"))
        self.assertEqual(readiness.status_code, 200)
        self.assertEqual(readiness.data["checks"]["database"]["ok"], True)

        denied = self.client.get(reverse("platform-metrics"))
        self.assertIn(denied.status_code, {401, 403})

        platform_login = self.client.post(
            reverse("platform-login"),
            {"email": self.platform_user.email, "password": "ChangeMe123!"},
            format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {platform_login.data['access']}")
        metrics = self.client.get(reverse("platform-metrics"))
        self.assertEqual(metrics.status_code, 200)
        self.assertIn("requests", metrics.data)
        self.assertIn("notifications", metrics.data)
        self.assertGreaterEqual(metrics.data["requests"]["sample_size"], 1)

    def test_api_key_upload_and_domain_operational_endpoints(self):
        login = self.client.post(
            reverse("organization-login"),
            {"organization": "swift", "email": self.user.email, "password": "ChangeMe123!"},
            format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")

        key_response = self.client.post(
            "/api/api-keys/",
            {"name": "Integration", "scopes": ["view_orders"]},
            format="json",
        )
        self.assertEqual(key_response.status_code, 201)
        self.assertIn("key", key_response.data)

        self.client.credentials(HTTP_X_API_KEY=key_response.data["key"])
        orders = self.client.get("/api/deliveries/")
        self.assertEqual(orders.status_code, 200)

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
        domain = self.client.post("/api/domains/", {"domain": "dashboard.swiftcouriers.com"}, format="json")
        self.assertEqual(domain.status_code, 201)
        self.assertIn("txt_record_value", domain.data)

        upload = self.client.post(
            reverse("upload-intent"),
            {
                "type": "proof_of_delivery",
                "original_name": "proof.jpg",
                "mime_type": "image/jpeg",
                "size_bytes": 2048,
            },
            format="json",
        )
        self.assertEqual(upload.status_code, 201)
        self.assertEqual(upload.data["method"], "PUT")
        self.assertEqual(upload.data["storage_provider"], "local")
        self.assertIn("object_key", upload.data)
        self.assertIn("x-streak-upload-signature", upload.data["headers"])

        completed = self.client.post(
            reverse("upload-complete", kwargs={"upload_id": upload.data["upload_id"]}),
            {
                "checksum": "a" * 64,
                "size_bytes": 2048,
            },
            format="json",
        )
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.data["status"], Upload.Status.UPLOADED)
        self.assertEqual(completed.data["checksum"], "a" * 64)
        self.assertEqual(completed.data["metadata"]["malware_scan_status"], "pending")

        second_upload = self.client.post(
            reverse("upload-intent"),
            {
                "type": "delivery_image",
                "original_name": "../bad name.png",
                "mime_type": "image/png",
                "size_bytes": 2048,
            },
            format="json",
        )
        self.assertEqual(second_upload.status_code, 201)
        rejected = self.client.post(
            reverse("upload-complete", kwargs={"upload_id": second_upload.data["upload_id"]}),
            {"size_bytes": 999},
            format="json",
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(Upload.objects.get(id=second_upload.data["upload_id"]).status, Upload.Status.REJECTED)

        invalid_mime = self.client.post(
            reverse("upload-intent"),
            {
                "type": "delivery_image",
                "original_name": "script.html",
                "mime_type": "text/html",
                "size_bytes": 100,
            },
            format="json",
        )
        self.assertEqual(invalid_mime.status_code, 400)

    def test_public_delivery_request_creates_and_reuses_customer_record(self):
        PublicSite.objects.create(
            organization=self.org,
            enabled=True,
            headline="Book Swift",
            request_form_enabled=True,
            tracking_enabled=True,
        )

        first = self.client.post(
            reverse("public-delivery-request"),
            {
                "tenant": "swift",
                "customer_name": "Ada Okafor",
                "customer_phone": "+2348000000001",
                "pickup_address": "Warehouse",
                "delivery_address": "Customer address",
                "zone": "Island",
            },
            format="json",
        )
        self.assertEqual(first.status_code, 201)
        customer = Customer.objects.get(organization=self.org, phone="+2348000000001")
        delivery = Delivery.objects.get(id=first.data["id"])
        self.assertEqual(delivery.customer_id, customer.id)
        self.assertEqual(customer.name, "Ada Okafor")
        self.assertEqual(customer.zone, "Island")

        second = self.client.post(
            reverse("public-delivery-request"),
            {
                "tenant": "swift",
                "customer_name": "Ada Okafor",
                "customer_phone": "+2348000000001",
                "pickup_address": "Warehouse 2",
                "delivery_address": "Customer address 2",
                "zone": "Mainland",
            },
            format="json",
        )
        self.assertEqual(second.status_code, 201)
        self.assertEqual(Customer.objects.filter(organization=self.org, phone="+2348000000001").count(), 1)
        customer.refresh_from_db()
        self.assertEqual(customer.zone, "Mainland")
        self.assertEqual(Delivery.objects.get(id=second.data["id"]).customer_id, customer.id)

    def test_mfa_setup_verify_and_login_enforcement(self):
        login = self.client.post(
            reverse("organization-login"),
            {"organization": "swift", "email": self.user.email, "password": "ChangeMe123!"},
            format="json",
        )
        self.assertEqual(login.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")

        setup = self.client.post(reverse("mfa-setup"), {}, format="json")
        self.assertEqual(setup.status_code, 201)
        code = totp_code(setup.data["secret"])
        verify = self.client.post(reverse("mfa-verify"), {"code": code}, format="json")
        self.assertEqual(verify.status_code, 200)

        self.client.credentials()
        challenge = self.client.post(
            reverse("organization-login"),
            {"organization": "swift", "email": self.user.email, "password": "ChangeMe123!"},
            format="json",
        )
        self.assertEqual(challenge.status_code, 202)
        self.assertTrue(challenge.data["mfa_required"])

        invalid = self.client.post(
            reverse("organization-login"),
            {"organization": "swift", "email": self.user.email, "password": "ChangeMe123!", "mfa_code": "000000"},
            format="json",
        )
        self.assertEqual(invalid.status_code, 401)

        successful = self.client.post(
            reverse("organization-login"),
            {"organization": "swift", "email": self.user.email, "password": "ChangeMe123!", "mfa_code": totp_code(setup.data["secret"])},
            format="json",
        )
        self.assertEqual(successful.status_code, 200)
        self.assertIn("access", successful.data)

    @override_settings(LOGIN_MAX_FAILED_ATTEMPTS=2, LOGIN_LOCKOUT_MINUTES=15)
    def test_failed_login_attempts_lock_account_identity(self):
        for _ in range(2):
            response = self.client.post(
                reverse("organization-login"),
                {"organization": "swift", "email": self.user.email, "password": "wrong"},
                format="json",
            )
            self.assertEqual(response.status_code, 401)

        locked = self.client.post(
            reverse("organization-login"),
            {"organization": "swift", "email": self.user.email, "password": "ChangeMe123!"},
            format="json",
        )
        self.assertEqual(locked.status_code, 429)

    def test_platform_impersonation_is_time_limited_and_permission_scoped(self):
        delivery = create_delivery(
            organization=self.org,
            reference="DX-IMP",
            customer_name="Ada",
            pickup_address="Warehouse",
            delivery_address="Customer",
        )
        platform_login = self.client.post(
            reverse("platform-login"),
            {"email": self.platform_user.email, "password": "ChangeMe123!"},
            format="json",
        )
        self.assertEqual(platform_login.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {platform_login.data['access']}")

        start = self.client.post(
            reverse("impersonation-start"),
            {
                "organization_id": str(self.org.id),
                "reason": "Investigating support ticket #123",
                "duration_minutes": 15,
                "allowed_permissions": ["view_orders", "manage_settings"],
            },
            format="json",
        )
        self.assertEqual(start.status_code, 201)
        self.assertEqual(start.data["session"]["allowed_permissions"], ["view_orders"])

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {start.data['access']}")
        orders = self.client.get("/api/deliveries/")
        self.assertEqual(orders.status_code, 200)
        first_id = orders.data["results"][0]["id"] if isinstance(orders.data, dict) and "results" in orders.data else orders.data[0]["id"]
        self.assertEqual(first_id, str(delivery.id))

        denied = self.client.post("/api/api-keys/", {"name": "Should fail", "scopes": ["view_orders"]}, format="json")
        self.assertEqual(denied.status_code, 403)

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {platform_login.data['access']}")
        ended = self.client.post(reverse("impersonation-end", kwargs={"session_id": start.data["session"]["id"]}), {}, format="json")
        self.assertEqual(ended.status_code, 200)

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {start.data['access']}")
        expired = self.client.get("/api/deliveries/")
        self.assertIn(expired.status_code, {401, 403})

    def test_delivery_services_broadcast_and_dispatch_notifications(self):
        with patch("core.services.broadcast_organization_event") as broadcast:
            with self.captureOnCommitCallbacks(execute=True):
                delivery = create_delivery(
                    organization=self.org,
                    reference="DX-RT",
                    customer_name="Ada",
                    pickup_address="Warehouse",
                    delivery_address="Customer",
                )
        broadcast.assert_called_once()
        self.assertEqual(broadcast.call_args.args[1], "delivery.created")

        with patch("core.services.broadcast_organization_event") as broadcast:
            with self.captureOnCommitCallbacks(execute=True):
                assigned = assign_courier(organization=self.org, delivery=delivery, courier=self.courier)
        self.assertEqual(broadcast.call_args.args[1], "delivery.assigned")
        notification = Notification.objects.get(event="delivery.assigned", organization=self.org)
        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.SENT)
        self.assertEqual(NotificationAttempt.objects.filter(notification=notification, success=True).count(), 1)

        with patch("core.services.broadcast_organization_event") as broadcast:
            with self.captureOnCommitCallbacks(execute=True):
                picked_up = transition_delivery(organization=self.org, delivery=assigned, status=Delivery.Status.PICKED_UP)
        self.assertEqual(broadcast.call_args.args[1], "delivery.status_changed")

        with patch("core.services.broadcast_organization_event") as broadcast:
            with self.captureOnCommitCallbacks(execute=True):
                transition_delivery(organization=self.org, delivery=picked_up, status=Delivery.Status.IN_TRANSIT)
        self.assertEqual(broadcast.call_args.args[1], "delivery.status_changed")

    def test_tracking_updates_broadcast_location_payload(self):
        with patch("core.services.broadcast_organization_event") as broadcast:
            with self.captureOnCommitCallbacks(execute=True):
                log = record_tracking(
                    organization=self.org,
                    courier=self.courier,
                    latitude="6.524379",
                    longitude="3.379206",
                    accuracy="12.50",
                    battery_level=87,
                )
        self.assertIsNotNone(log)
        self.assertEqual(broadcast.call_args.args[1], "courier.location_updated")
        self.assertEqual(broadcast.call_args.args[2]["courier_id"], str(self.courier.id))

    def test_nearest_couriers_heatmap_and_scheduled_release(self):
        self.courier.current_latitude = "6.524379"
        self.courier.current_longitude = "3.379206"
        self.courier.location_updated_at = timezone.now()
        self.courier.save(update_fields=["current_latitude", "current_longitude", "location_updated_at", "updated_at"])
        create_delivery(
            organization=self.org,
            reference="DX-GEO",
            customer_name="Ada",
            pickup_address="Warehouse",
            delivery_address="Customer",
            zone="Island",
            scheduled_time=timezone.now() - timedelta(minutes=5),
        )
        login = self.client.post(
            reverse("organization-login"),
            {"organization": "swift", "email": self.user.email, "password": "ChangeMe123!"},
            format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")

        nearest = self.client.get("/api/couriers/nearest/?latitude=6.524000&longitude=3.379000&radius_km=2")
        self.assertEqual(nearest.status_code, 200)
        self.assertEqual(nearest.data[0]["courier"]["id"], str(self.courier.id))
        self.assertLess(nearest.data[0]["distance_km"], 1)

        heatmap = self.client.get("/api/analytics/heatmap/?days=30")
        self.assertEqual(heatmap.status_code, 200)
        self.assertEqual(heatmap.data["zones"][0]["zone"], "Island")

        with patch("core.services.broadcast_organization_event") as broadcast:
            with self.captureOnCommitCallbacks(execute=True):
                released = release_due_scheduled_deliveries()
        self.assertEqual(released["released_count"], 1)
        self.assertEqual(broadcast.call_args.args[1], "delivery.scheduled_ready")

    def test_analytics_snapshot_aggregates_tenant_metrics(self):
        today = timezone.localdate()
        delivered = create_delivery(
            organization=self.org,
            reference="DX-A1",
            customer_name="Ada",
            pickup_address="Warehouse",
            delivery_address="Customer",
            delivery_fee="1500.00",
        )
        assign_courier(organization=self.org, delivery=delivered, courier=self.courier)
        delivered = transition_delivery(organization=self.org, delivery=delivered, status=Delivery.Status.PICKED_UP)
        delivered = transition_delivery(organization=self.org, delivery=delivered, status=Delivery.Status.IN_TRANSIT)
        delivered = transition_delivery(organization=self.org, delivery=delivered, status=Delivery.Status.DELIVERED)
        Delivery.objects.filter(id=delivered.id).update(
            created_at=timezone.now() - timedelta(seconds=30),
            completed_at=timezone.now(),
        )
        failed = create_delivery(
            organization=self.org,
            reference="DX-A2",
            customer_name="Tola",
            pickup_address="Warehouse",
            delivery_address="Customer",
            delivery_fee="700.00",
        )
        Delivery.objects.filter(id=failed.id).update(status=Delivery.Status.FAILED)
        Delivery.objects.create(
            organization=self.other_org,
            reference="DX-OTHER-ANALYTICS",
            customer_name="Other",
            pickup_address="A",
            delivery_address="B",
            status=Delivery.Status.DELIVERED,
            delivery_fee="9999.00",
        )

        snapshot = aggregate_analytics_snapshot(self.org, period_start=today)

        self.assertEqual(snapshot.delivery_volume, 2)
        self.assertEqual(snapshot.completed_deliveries, 1)
        self.assertEqual(snapshot.failed_deliveries, 1)
        self.assertEqual(snapshot.revenue, Decimal("1500.00"))
        self.assertEqual(snapshot.completion_rate, Decimal("50.00"))
        self.assertGreater(snapshot.average_delivery_seconds, 0)
        self.assertEqual(snapshot.rider_efficiency["couriers"][0]["courier_id"], str(self.courier.id))

    def test_analytics_snapshot_api_aggregates_and_lists_recent_snapshots(self):
        login = self.client.post(
            reverse("organization-login"),
            {"organization": "swift", "email": self.user.email, "password": "ChangeMe123!"},
            format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
        create_delivery(
            organization=self.org,
            reference="DX-API-ANALYTICS",
            customer_name="Ada",
            pickup_address="Warehouse",
            delivery_address="Customer",
            delivery_fee="500.00",
        )

        aggregate = self.client.post("/api/analytics/aggregate/", {}, format="json")
        self.assertEqual(aggregate.status_code, 201)
        self.assertEqual(aggregate.data["delivery_volume"], 1)

        snapshots = self.client.get("/api/analytics/snapshots/?days=7")
        self.assertEqual(snapshots.status_code, 200)
        self.assertEqual(len(snapshots.data), 1)
        self.assertEqual(snapshots.data[0]["delivery_volume"], 1)
