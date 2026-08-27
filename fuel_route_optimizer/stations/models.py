from django.db import models

# from django.contrib.gis.db import models as gis_models
from stations.mixins import TimeStampedModel


class GeocodeStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SUCCESS = "success", "Success"
    FAILED = "failed", "Failed"


class State(models.TextChoices):
    AL = "AL", "Alabama"
    AK = "AK", "Alaska"
    AS = "AS", "American Samoa"
    AZ = "AZ", "Arizona"
    AR = "AR", "Arkansas"
    CA = "CA", "California"
    CO = "CO", "Colorado"
    CT = "CT", "Connecticut"
    DE = "DE", "Delaware"
    DC = "DC", "District Of Columbia"
    FM = "FM", "Federated States Of Micronesia"
    FL = "FL", "Florida"
    GA = "GA", "Georgia"
    GU = "GU", "Guam"
    HI = "HI", "Hawaii"
    ID = "ID", "Idaho"
    IL = "IL", "Illinois"
    IN = "IN", "Indiana"
    IA = "IA", "Iowa"
    KS = "KS", "Kansas"
    KY = "KY", "Kentucky"
    LA = "LA", "Louisiana"
    ME = "ME", "Maine"
    MH = "MH", "Marshall Islands"
    MD = "MD", "Maryland"
    MA = "MA", "Massachusetts"
    MI = "MI", "Michigan"
    MN = "MN", "Minnesota"
    MS = "MS", "Mississippi"
    MO = "MO", "Missouri"
    MT = "MT", "Montana"
    NE = "NE", "Nebraska"
    NV = "NV", "Nevada"
    NH = "NH", "New Hampshire"
    NJ = "NJ", "New Jersey"
    NM = "NM", "New Mexico"
    NY = "NY", "New York"
    NC = "NC", "North Carolina"
    ND = "ND", "North Dakota"
    MP = "MP", "Northern Mariana Islands"
    OH = "OH", "Ohio"
    OK = "OK", "Oklahoma"
    OR = "OR", "Oregon"
    PW = "PW", "Palau"
    PA = "PA", "Pennsylvania"
    PR = "PR", "Puerto Rico"
    RI = "RI", "Rhode Island"
    SC = "SC", "South Carolina"
    SD = "SD", "South Dakota"
    TN = "TN", "Tennessee"
    TX = "TX", "Texas"
    UT = "UT", "Utah"
    VT = "VT", "Vermont"
    VI = "VI", "Virgin Islands"
    VA = "VA", "Virginia"
    WA = "WA", "Washington"
    WV = "WV", "West Virginia"
    WI = "WI", "Wisconsin"
    WY = "WY", "Wyoming"


class FuelStation(TimeStampedModel):
    opis_id = models.IntegerField(unique=True, db_index=True)
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255, blank=True, null=True)
    city = models.CharField(max_length=100)
    state = models.CharField(
        max_length=2,
        choices=State.choices,
        db_index=True,
    )
    rack_id = models.IntegerField(null=True, blank=True)

    latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
    )
    longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
    )

    geocode_status = models.CharField(
        max_length=10,
        choices=GeocodeStatus.choices,
        default=GeocodeStatus.PENDING,
        db_index=True,
    )
    geocoded_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.name} - {self.city}, {self.state}"


class FuelPrice(TimeStampedModel):
    station = models.ForeignKey(
        FuelStation,
        on_delete=models.CASCADE,
        related_name="prices",
    )
    price = models.DecimalField(
        max_digits=6,
        decimal_places=4,
    )
    observed_at = models.DateTimeField(db_index=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["station", "-observed_at"],
                name="station_latest_price_idx",
            ),
        ]

    def __str__(self):
        return f"{self.station} - ${self.price}"
