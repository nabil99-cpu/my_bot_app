from dataclasses import dataclass
from typing import Dict, List, Callable


# ============================================================================
# MODELS
# ============================================================================

@dataclass(frozen=True)
class Network:
    id: str
    name: str
    display_name: str
    payment_info: str
    enabled: bool = True


@dataclass(frozen=True)
class Package:
    id: str
    network_id: str
    name: str
    price: int
    data_gb: str
    duration_days: int
    enabled: bool = True


# ============================================================================
# NETWORKS
# ============================================================================

NETWORKS: Dict[str, Network] = {
    "net1": Network(
        id="net1",
        name="alpha_net",
        display_name="شبكة ألفا",
        payment_info=(
            "💳 <b>طرق الدفع - شبكة ألفا:</b>\n"
            "1. كريمي: <code>123456789</code>\n"
            "2. كاش: <code>777777777</code>\n\n"
            "📸 <b>بعد التحويل، قم بإرسال صورة الإشعار هنا مباشرة:</b>"
        )
    ),
    "net2": Network(
        id="net2",
        name="beta_net",
        display_name="شبكة بيتا",
        payment_info=(
            "💳 <b>طرق الدفع - شبكة بيتا:</b>\n"
            "1. الكريمي: <code>999999999</code>\n"
            "2. حوالة باسم محمد\n\n"
            "📸 <b>بعد التحويل، قم بإرسال صورة الإشعار هنا مباشرة:</b>"
        )
    ),
    "net3": Network(
        id="net3",
        name="gamma_net",
        display_name="شبكة جاما",
        payment_info=(
            "💳 <b>طرق الدفع - شبكة جاما:</b>\n"
            "1. كاش: <code>711111111</code>\n"
            "2. صرافة القطيبي\n\n"
            "📸 <b>بعد التحويل، قم بإرسال صورة الإشعار هنا مباشرة:</b>"
        )
    ),
}


# ============================================================================
# PACKAGES
# ============================================================================

PACKAGES: Dict[str, Package] = {
    # شبكة ألفا
    "p1": Package("p1", "net1", "1 جيجا - يوم", 300, "1", 1),
    "p2": Package("p2", "net1", "5 جيجا - 7 أيام", 1500, "5", 7),
    "p3": Package("p3", "net1", "15 جيجا - 30 يوم", 4000, "15", 30),

    # شبكة بيتا
    "p4": Package("p4", "net2", "2 جيجا - 3 أيام", 700, "2", 3),
    "p5": Package("p5", "net2", "10 جيجا - 15 يوم", 2500, "10", 15),
    "p6": Package("p6", "net2", "غير محدود - 30 يوم", 8000, "∞", 30),

    # شبكة جاما
    "p7": Package("p7", "net3", "3 جيجا - 5 أيام", 1000, "3", 5),
    "p8": Package("p8", "net3", "20 جيجا - 30 يوم", 5000, "20", 30),
}


# ============================================================================
# HELPERS
# ============================================================================

def get_enabled_networks() -> List[Network]:
    return [n for n in NETWORKS.values() if n.enabled]


def get_packages_by_network(network_id: str) -> List[Package]:
    return [
        pkg for pkg in PACKAGES.values()
        if pkg.network_id == network_id and pkg.enabled
    ]


def get_network(network_id: str) -> Network | None:
    return NETWORKS.get(network_id)


def get_package(package_id: str) -> Package | None:
    return PACKAGES.get(package_id)


def validate_package_belongs_to_network(network_id: str, package_id: str) -> bool:
    pkg = PACKAGES.get(package_id)
    return bool(pkg and pkg.network_id == network_id and pkg.enabled)


# ============================================================================
# CARD GENERATORS (PLACEHOLDERS)
# استبدل هذه الدوال لاحقًا بتكامل MikroTik / API الحقيقي لكل شبكة
# ============================================================================

def generate_alpha_card(package: Package) -> str:
    return f"ALPHA-{package.id}-12345"


def generate_beta_card(package: Package) -> str:
    return f"BETA-{package.id}-67890"


def generate_gamma_card(package: Package) -> str:
    return f"GAMMA-{package.id}-54321"


CARD_GENERATORS: Dict[str, Callable[[Package], str]] = {
    "net1": generate_alpha_card,
    "net2": generate_beta_card,
    "net3": generate_gamma_card,
}


def generate_network_card(network_id: str, package: Package) -> str:
    generator = CARD_GENERATORS.get(network_id)
    if not generator:
        raise ValueError(f"No card generator configured for network '{network_id}'")
    return generator(package)
