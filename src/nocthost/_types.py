"""Response shapes. Every value returned by the client is a plain ``dict``;
these TypedDicts describe the documented keys for editors and type checkers.
The API may add keys at any time."""

from typing import Dict, List, Literal, Optional, TypedDict

ServerStatus = Literal[
    "PROVISIONING",
    "ACTIVE",
    "STOPPED",
    "SUSPENDED",
    "ARCHIVED",
    "DESTROYING",
    "DESTROYED",
    "ERROR",
]

BackupStatus = Literal["PENDING", "AVAILABLE", "EXPIRED"]


class User(TypedDict, total=False):
    id: str
    email: str
    balance: str


class Billing(TypedDict, total=False):
    balance: str
    hourlySpend: str


class Server(TypedDict, total=False):
    id: str
    label: str
    status: ServerStatus
    provider: str
    region: str
    plan: str
    image: str
    ipv4: Optional[str]
    ipv6: Optional[str]
    hourlyPrice: str
    accessType: str
    failureReason: Optional[str]
    billingCycle: str
    monthlyPrice: str
    paidUntil: Optional[str]
    createdAt: str


class Provider(TypedDict, total=False):
    id: str
    label: str
    available: bool


class ProvidersResponse(TypedDict, total=False):
    providers: List[Provider]
    outages: Dict[str, Dict[str, str]]


class RootPasswordResult(TypedDict, total=False):
    password: str
    matchesRequested: bool
    note: str


class Backup(TypedDict, total=False):
    id: str
    label: str
    status: BackupStatus
    region: str
    plan: str
    provider: str
    serverId: Optional[str]
    createdAt: str
    expiresAt: str


class OkResponse(TypedDict, total=False):
    ok: bool
