"""
Commander, 2026-09-11: "trace the Commander answers already recorded
through checkpoint state, eligibility, dispatch, and outcome. Repair the
first broken connection... Honor the latest answer, including 'skip';
do not ask me to resubmit identity information or consent already
provided."

Real production evidence found via a checkpoint-trace diagnostic: the
UCP-01 government-portal-search dispatch used commander_response
verbatim as the literal business_name — a real search was dispatched
with business_name="skip" (Commander's explicit decline, typed as free
text), and again with business_name="Approved — proceed." (an
approval-only reply with no business name in it). Neither is a business
name.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models.hunter_ledger import CanonicalOpportunity, Disposition
from app.services import execution_accounting as acct


def _make_session() -> Session:
    import app.models.alert  # noqa: F401
    import app.models.hunter_ledger  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _seed(session: Session, *, commander_response: str, checkpoint: str = "original ask") -> CanonicalOpportunity:
    opp = CanonicalOpportunity(
        canonical_opportunity_id="HUNTER-CAND-2026-09-08-01-UCP",
        lane="compliance_recovery",
        factual_mechanism="GA unclaimed property",
        source_provenance="seed",
        freshness_date=date(2026, 9, 8),
        disposition=Disposition.watchlist.value,
        commander_response=commander_response,
        required_commander_checkpoints=checkpoint,
    )
    session.add(opp)
    session.commit()
    session.refresh(opp)
    return opp


def test_a_real_business_name_answer_is_returned_for_dispatch():
    session = _make_session()
    opp = _seed(session, commander_response="Murphy Enterprises LLC  84-2065486")

    result = acct.resolve_ucp_business_name_checkpoint(session, opp)

    assert result == "Murphy Enterprises LLC  84-2065486"


def test_skip_is_never_used_as_a_business_name():
    session = _make_session()
    opp = _seed(session, commander_response="skip")

    result = acct.resolve_ucp_business_name_checkpoint(session, opp)

    assert result is None


def test_skip_does_not_reopen_the_checkpoint_or_change_disposition():
    """Honoring 'skip' means Hunter stops asking — it must not resurface
    the checkpoint in the decisions feed again."""
    session = _make_session()
    opp = _seed(session, commander_response="skip")

    acct.resolve_ucp_business_name_checkpoint(session, opp)

    session.refresh(opp)
    assert opp.disposition == Disposition.watchlist.value  # unchanged
    assert opp.commander_response == "skip"  # not cleared — no re-ask


def test_skip_is_honored_case_and_whitespace_insensitively():
    session = _make_session()
    opp = _seed(session, commander_response="  Skip  ")

    result = acct.resolve_ucp_business_name_checkpoint(session, opp)

    assert result is None


def test_approval_only_reply_reopens_with_the_specific_business_name_ask():
    session = _make_session()
    opp = _seed(session, commander_response="Approved — proceed.")

    result = acct.resolve_ucp_business_name_checkpoint(session, opp)

    assert result is None
    session.refresh(opp)
    assert opp.disposition == Disposition.pending_commander.value
    assert "business name or FEIN" in opp.required_commander_checkpoints
    # set_disposition's new_checkpoint path clears the stale answer so a
    # genuinely new one resurfaces in the decisions feed.
    assert opp.commander_response is None


def test_approval_only_reply_does_not_reopen_a_second_time():
    """Once reopened with the specific ask, a repeated approval-only
    reply (or the SAME stale one, on a re-run before Commander answers
    again) must not keep re-triggering the reopen — that would just
    thrash the checkpoint every boot."""
    session = _make_session()
    opp = _seed(
        session,
        commander_response="Approved — proceed.",
        checkpoint="Please supply the business name or FEIN to search for.",
    )

    result = acct.resolve_ucp_business_name_checkpoint(session, opp)

    assert result is None
    session.refresh(opp)
    # Already carries the marker from a prior reopen — must not reopen
    # again or clear commander_response a second time.
    assert opp.required_commander_checkpoints == "Please supply the business name or FEIN to search for."


def test_a_genuinely_new_business_name_after_reopen_is_honored_without_resubmitting_identity():
    """After the checkpoint was reopened once, Commander supplying the
    actual business name (not identity info, which was never asked for
    again) must be honored on the very next check."""
    session = _make_session()
    opp = _seed(
        session,
        commander_response="Murphy Enterprises LLC",
        checkpoint="Please supply the business name or FEIN to search for.",
    )

    result = acct.resolve_ucp_business_name_checkpoint(session, opp)

    assert result == "Murphy Enterprises LLC"


def test_empty_response_returns_none():
    session = _make_session()
    opp = _seed(session, commander_response="")

    assert acct.resolve_ucp_business_name_checkpoint(session, opp) is None
