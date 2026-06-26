import strawberry
from strawberry.types import Info
from strawberry.django.views import GraphQLView

from .models import Courier, Customer, Delivery, DeliveryEvent, Organization
from .services import (
    assign_courier,
    create_delivery,
    create_domain_verification,
    overview_metrics,
    record_tracking,
    transition_delivery,
)
from .tenant import organization_context, require_tenant


@strawberry.type
class OrganizationType:
    id: strawberry.ID
    name: str
    slug: str
    subdomain: str
    custom_domain: str | None
    subscription_plan: str
    status: str


@strawberry.type
class CourierType:
    id: strawberry.ID
    name: str
    initials: str
    status: str
    active_delivery_count: int
    battery_level: int
    zone: str
    vehicle: str
    phone: str
    rating: float
    completion_rate: int
    current_location: str


@strawberry.type
class CustomerType:
    id: strawberry.ID
    name: str
    phone: str
    email: str
    initials: str
    status: str
    zone: str
    total_orders: int
    total_spent: float


@strawberry.type
class DeliveryEventType:
    id: strawberry.ID
    label: str
    status: str
    event_at: str
    done: bool
    sort_order: int


@strawberry.type
class DeliveryType:
    id: strawberry.ID
    reference: str
    customer_name: str
    customer_phone: str
    pickup_address: str
    delivery_address: str
    status: str
    zone: str
    delivery_fee: float
    courier_id: strawberry.ID | None
    notes: str
    created_at: str
    events: list[DeliveryEventType]


@strawberry.type
class CustomDomainType:
    id: strawberry.ID
    domain: str
    status: str
    txt_record_name: str
    txt_record_value: str
    ssl_status: str


@strawberry.type
class OverviewMetricsType:
    total_deliveries: int
    active_deliveries: int
    pending_orders: int
    active_couriers: int
    total_couriers: int
    revenue: float
    average_delivery_fee: float
    success_rate: int


@strawberry.input
class DeliveryInput:
    customer_name: str
    customer_phone: str = ""
    pickup_address: str
    delivery_address: str
    zone: str = ""
    delivery_fee: float = 0
    reference: str | None = None
    notes: str = ""


@strawberry.input
class CourierInput:
    name: str
    phone: str = ""
    zone: str = ""
    vehicle: str = ""


@strawberry.input
class CustomerInput:
    name: str
    phone: str = ""
    email: str = ""
    zone: str = ""


@strawberry.input
class TrackingInput:
    courier_id: strawberry.ID
    latitude: float
    longitude: float
    accuracy: float | None = None
    battery_level: int | None = None
    delivery_id: strawberry.ID | None = None


def _organization(obj: Organization) -> OrganizationType:
    return OrganizationType(
        id=str(obj.id),
        name=obj.name,
        slug=obj.slug,
        subdomain=obj.subdomain,
        custom_domain=obj.custom_domain,
        subscription_plan=obj.subscription_plan,
        status=obj.status,
    )


def _courier(obj: Courier) -> CourierType:
    return CourierType(
        id=str(obj.id),
        name=obj.name,
        initials=obj.initials,
        status=obj.status,
        active_delivery_count=obj.active_delivery_count,
        battery_level=obj.battery_level,
        zone=obj.zone,
        vehicle=obj.vehicle,
        phone=obj.phone,
        rating=float(obj.rating),
        completion_rate=obj.completion_rate,
        current_location=obj.current_location,
    )


def _customer(obj: Customer) -> CustomerType:
    return CustomerType(
        id=str(obj.id),
        name=obj.name,
        phone=obj.phone,
        email=obj.email,
        initials=obj.initials,
        status=obj.status,
        zone=obj.zone,
        total_orders=obj.total_orders,
        total_spent=float(obj.total_spent),
    )


def _delivery_event(obj: DeliveryEvent) -> DeliveryEventType:
    return DeliveryEventType(
        id=str(obj.id),
        label=obj.label,
        status=obj.status,
        event_at=obj.event_at.isoformat(),
        done=obj.done,
        sort_order=obj.sort_order,
    )


def _delivery(obj: Delivery) -> DeliveryType:
    return DeliveryType(
        id=str(obj.id),
        reference=obj.reference,
        customer_name=obj.customer_name,
        customer_phone=obj.customer_phone,
        pickup_address=obj.pickup_address,
        delivery_address=obj.delivery_address,
        status=obj.status,
        zone=obj.zone,
        delivery_fee=float(obj.delivery_fee),
        courier_id=str(obj.courier_id) if obj.courier_id else None,
        notes=obj.notes,
        created_at=obj.created_at.isoformat(),
        events=[_delivery_event(event) for event in obj.events.all()],
    )


def _custom_domain(obj) -> CustomDomainType:
    return CustomDomainType(
        id=str(obj.id),
        domain=obj.domain,
        status=obj.status,
        txt_record_name=obj.txt_record_name,
        txt_record_value=obj.txt_record_value,
        ssl_status=obj.ssl_status,
    )


def _initials(name: str) -> str:
    return "".join(part[0] for part in name.split()[:2]).upper() or "?"


@strawberry.type
class Query:
    @strawberry.field
    def organizations(self, id: strawberry.ID | None = None, slug: str | None = None) -> list[OrganizationType]:
        qs = Organization.objects.filter(status=Organization.Status.ACTIVE).order_by("name")
        if id is not None:
            qs = qs.filter(id=id)
        if slug is not None:
            qs = qs.filter(slug=slug)
        return [_organization(org) for org in qs]

    @strawberry.field
    def couriers(self, info: Info, status: str | None = None, zone: str | None = None) -> list[CourierType]:
        organization = require_tenant(info.context.request)
        qs = Courier.objects.for_organization(organization).order_by("name")
        if status:
            qs = qs.filter(status=status)
        if zone:
            qs = qs.filter(zone=zone)
        return [_courier(c) for c in qs]

    @strawberry.field
    def courier(self, info: Info, id: strawberry.ID) -> CourierType | None:
        organization = require_tenant(info.context.request)
        obj = Courier.objects.for_organization(organization).filter(id=id).first()
        return _courier(obj) if obj else None

    @strawberry.field
    def customers(self, info: Info, status: str | None = None) -> list[CustomerType]:
        organization = require_tenant(info.context.request)
        qs = Customer.objects.for_organization(organization).order_by("name")
        if status:
            qs = qs.filter(status=status)
        return [_customer(c) for c in qs]

    @strawberry.field
    def customer(self, info: Info, id: strawberry.ID) -> CustomerType | None:
        organization = require_tenant(info.context.request)
        obj = Customer.objects.for_organization(organization).filter(id=id).first()
        return _customer(obj) if obj else None

    @strawberry.field
    def deliveries(self, info: Info, status: str | None = None) -> list[DeliveryType]:
        organization = require_tenant(info.context.request)
        qs = Delivery.objects.for_organization(organization).prefetch_related("events").order_by("-created_at")
        if status:
            qs = qs.filter(status=status)
        return [_delivery(d) for d in qs]

    @strawberry.field
    def delivery(self, info: Info, id: strawberry.ID) -> DeliveryType | None:
        organization = require_tenant(info.context.request)
        obj = Delivery.objects.for_organization(organization).prefetch_related("events").filter(id=id).first()
        return _delivery(obj) if obj else None

    @strawberry.field
    def overview(self, info: Info) -> OverviewMetricsType:
        metrics = overview_metrics(require_tenant(info.context.request))
        return OverviewMetricsType(
            total_deliveries=metrics["total_deliveries"],
            active_deliveries=metrics["active_deliveries"],
            pending_orders=metrics["pending_orders"],
            active_couriers=metrics["active_couriers"],
            total_couriers=metrics["total_couriers"],
            revenue=float(metrics["revenue"]),
            average_delivery_fee=float(metrics["average_delivery_fee"]),
            success_rate=metrics["success_rate"],
        )


@strawberry.type
class Mutation:
    @strawberry.mutation
    def create_delivery(self, info: Info, data: DeliveryInput) -> DeliveryType:
        request = info.context.request
        organization = require_tenant(request)
        delivery = create_delivery(
            organization=organization,
            actor=getattr(request, "actor", None),
            request=request,
            reference=data.reference,
            customer_name=data.customer_name,
            customer_phone=data.customer_phone,
            pickup_address=data.pickup_address,
            delivery_address=data.delivery_address,
            zone=data.zone,
            delivery_fee=data.delivery_fee,
            notes=data.notes,
        )
        return _delivery(delivery)

    @strawberry.mutation
    def transition_delivery(self, info: Info, delivery_id: strawberry.ID, status: str) -> DeliveryType:
        request = info.context.request
        organization = require_tenant(request)
        delivery = Delivery.objects.get(id=delivery_id, organization=organization)
        return _delivery(
            transition_delivery(
                organization=organization,
                delivery=delivery,
                status=status,
                actor=getattr(request, "actor", None),
                request=request,
            )
        )

    @strawberry.mutation
    def assign_courier(self, info: Info, delivery_id: strawberry.ID, courier_id: strawberry.ID) -> DeliveryType:
        request = info.context.request
        organization = require_tenant(request)
        delivery = Delivery.objects.get(id=delivery_id, organization=organization)
        courier = Courier.objects.get(id=courier_id, organization=organization)
        return _delivery(
            assign_courier(
                organization=organization,
                delivery=delivery,
                courier=courier,
                actor=getattr(request, "actor", None),
                request=request,
            )
        )

    @strawberry.mutation
    def create_courier(self, info: Info, data: CourierInput) -> CourierType:
        organization = require_tenant(info.context.request)
        with organization_context(organization):
            courier = Courier.objects.create(
                organization=organization,
                name=data.name,
                initials=_initials(data.name),
                phone=data.phone,
                zone=data.zone,
                vehicle=data.vehicle,
            )
        return _courier(courier)

    @strawberry.mutation
    def create_customer(self, info: Info, data: CustomerInput) -> CustomerType:
        organization = require_tenant(info.context.request)
        with organization_context(organization):
            customer = Customer.objects.create(
                organization=organization,
                name=data.name,
                initials=_initials(data.name),
                phone=data.phone,
                email=data.email,
                zone=data.zone,
            )
        return _customer(customer)

    @strawberry.mutation
    def record_tracking(self, info: Info, data: TrackingInput) -> CourierType:
        organization = require_tenant(info.context.request)
        courier = Courier.objects.get(id=data.courier_id, organization=organization)
        delivery = (
            Delivery.objects.get(id=data.delivery_id, organization=organization)
            if data.delivery_id
            else None
        )
        record_tracking(
            organization=organization,
            courier=courier,
            latitude=data.latitude,
            longitude=data.longitude,
            accuracy=data.accuracy,
            battery_level=data.battery_level,
            delivery=delivery,
        )
        courier.refresh_from_db()
        return _courier(courier)

    @strawberry.mutation
    def create_domain_verification(self, info: Info, domain: str) -> CustomDomainType:
        organization = require_tenant(info.context.request)
        return _custom_domain(create_domain_verification(organization, domain))


schema = strawberry.Schema(query=Query, mutation=Mutation)
graphql_view = GraphQLView.as_view(schema=schema)
