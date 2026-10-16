from __future__ import annotations

from dataclasses import dataclass

from app.claims.consent import ConsentSimulator
from app.claims.fixtures import Fixtures, load_fixtures
from app.claims.guidance import Guidance
from app.claims.repo import ClaimsRepo
from app.claims.summary import Outbox
from app.config import FIXTURES_DIR, Settings


@dataclass(frozen=True)
class Services:
    fixtures: Fixtures
    claims: ClaimsRepo
    guidance: Guidance
    consent: ConsentSimulator
    outbox: Outbox  # simulated; the only mutable service


def build_services(settings: Settings) -> Services:
    """Raises FixtureError / ValueError with a developer-readable message on bad data or config."""
    fixtures = load_fixtures(FIXTURES_DIR)
    return Services(
        fixtures=fixtures,
        claims=ClaimsRepo(fixtures),
        guidance=Guidance(fixtures),
        consent=ConsentSimulator(fixtures, settings.consent_scenario),
        outbox=Outbox(),
    )
