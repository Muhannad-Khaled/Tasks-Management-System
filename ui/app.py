"""Streamlit UI for the M1 slice.

Talks to FastAPI over HTTP rather than the database directly, so the API stays
the single write path. Later milestones add the grounding, audit, timeline and
copilot pages described in the brief.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import httpx
import streamlit as st

# 127.0.0.1, not localhost: localhost resolves to ::1 first and uvicorn
# binds IPv4 only, so every call paid ~2 seconds waiting out the failed
# IPv6 attempt before falling back. Nine times slower, from a hostname.
API_BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000")
# A clean run takes about a minute. The headroom is for Gemini answering 503
# under load: the client retries with backoff and then tries the fallback
# models, and that path is far slower than the work itself.
API_TIMEOUT = float(os.environ.get("API_TIMEOUT", "180"))

TEAM_COLORS = {"commercial": "🔵", "technical": "🟣", "operations": "🟠"}
SOURCE_BADGES = {
    "explicit": ("✅", "Stated in the SOW"),
    "inferred": ("🔎", "Derived from the SOW"),
    "assumed": ("⚠️", "Not in the SOW — assumed"),
}

st.set_page_config(page_title="SOW → Project", page_icon="📄", layout="wide")


def api(method: str, path: str, quiet: bool = False, **kwargs):
    """Call the API. `quiet` swallows a 4xx/5xx for calls that may simply not apply.

    Only for optional extras — a board that does not exist yet, a Trello that
    is unreachable. Anything the page is actually about must still shout.
    """
    try:
        response = httpx.request(method, f"{API_BASE}{path}", timeout=API_TIMEOUT, **kwargs)
    except httpx.TimeoutException:
        # Caught before RequestError, which it subclasses. Running the two
        # together reported "is uvicorn running?" for a server that was running
        # perfectly well and simply had not answered yet — which sends whoever
        # reads it hunting for a dead process instead of reading the log.
        st.error(
            f"**No answer within {API_TIMEOUT}s** — `{method} {path}`\n\n"
            "The API is up; the request has just not finished. An upload retries "
            "Gemini whenever it returns 503 (high demand) and works through the "
            "fallback models, which can outlast this wait.\n\n"
            "The work carries on server-side. Check `uploads/api.log` for what it "
            "is doing, then reload: if it finished, the project is in the sidebar."
        )
        st.stop()
    except httpx.RequestError as exc:
        st.error(f"Cannot reach the API at {API_BASE}. Is uvicorn running?\n\n{exc}")
        st.stop()
    if response.status_code >= 400:
        if quiet:
            return None
        detail = response.json().get("detail", response.text) if response.content else response.text
        st.error(friendly_error(response.status_code, str(detail)))
        return None
    return response.json()


def friendly_error(status: int, detail: str) -> str:
    """Turn an API failure into something a PM can act on.

    The provider's errors arrive as raw JSON blobs. Showing those to someone
    reviewing a project plan tells them nothing about what to do next.
    """
    lowered = detail.lower()
    if "quota" in lowered or "resource_exhausted" in lowered:
        return (
            "**The daily Gemini quota is used up.** The free tier allows 20 requests "
            "per day per model, and a full run costs four. It resets on Google's "
            "daily schedule — everything already generated stays available in the "
            "meantime."
        )
    if "503" in detail or "unavailable" in lowered or "high demand" in lowered:
        return (
            "**Gemini is busy right now.** The model was overloaded and the "
            "fallback models were too. This clears on its own, usually within a "
            "few minutes — try again shortly."
        )
    if status == 409:
        return f"**Cannot do that yet.** {detail}"
    if status == 404:
        return f"**Not found.** {detail}"
    return f"{method_label(status)} {detail}"


def method_label(status: int) -> str:
    return "**Something went wrong.**" if status >= 500 else "**Request rejected.**"


def render_upload() -> None:
    # Collapsed by default and explicitly labelled: sitting open above the
    # selected project made it look like uploading would add to that project.
    # It never does — one SOW is one project.
    with st.expander("➕ Upload a SOW — starts a new project", expanded=False):
        st.caption(
            "Each upload creates its own project. It never merges into the "
            "project selected in the sidebar, and re-uploading the same SOW "
            "gives you a second, separate project rather than replacing the first."
        )
        st.caption(
            "The SOW is the single source of truth: every task generated from it "
            "traces back to a specific chunk of the document."
        )
        uploaded = st.file_uploader("SOW file", type=["pdf", "docx", "txt"])
        if uploaded and st.button("Process SOW", type="primary"):
            with st.spinner("Parsing, validating, extracting, and grounding…"):
                result = api(
                    "POST",
                    "/projects/upload",
                    files={"file": (uploaded.name, uploaded.getvalue())},
                )
            if not result:
                return
            if result.get("error"):
                st.error(f"Parsing status: {result['parsing_status']} — {result['error']}")
            else:
                st.success(
                    f"Created a new project with {result['task_count']} tasks "
                    f"(parsing status: {result['parsing_status']})."
                )
                st.session_state["project_id"] = result["project_id"]
            for warning in result.get("warnings", []):
                st.warning(warning)


DEPENDENCY_PROVENANCE = {
    "explicit": ("📄", "the SOW states this ordering"),
    "inferred": ("🔗", "derived from the SOW, not stated in it"),
    "assumed": ("⚠️", "not in the SOW — this ordering is the system's judgement"),
    "": ("❓", "recorded before dependencies carried a source"),
}


def render_blockers(blockers: list[dict], evidence_by_key: dict) -> None:
    """What this task waits on, and on whose authority.

    Shown per arrow rather than as one line of titles. An arrow is what moves
    every date after it, so the PM deciding whether a deadline is real needs
    to see which of them the SOW actually asked for.
    """
    if not blockers:
        return
    st.caption("Blocked by:")
    for blocker in blockers:
        icon, meaning = DEPENDENCY_PROVENANCE.get(
            blocker["source_status"], DEPENDENCY_PROVENANCE[""]
        )
        st.caption(f"  {icon} **{blocker['title']}** — {meaning}")
        if blocker["rationale"]:
            st.caption(f"    _{blocker['rationale']}_")
        for key in blocker["source_chunk_keys"]:
            evidence = evidence_by_key.get(key)
            if evidence:
                page = f" · page {evidence['page']}" if evidence["page"] else ""
                render_evidence(key, page, evidence["text"])


def render_evidence(key: str, page: str, text: str) -> None:
    """Show a cited chunk the way it reads in the SOW.

    Markdown collapses a single newline into a space, which turned an extracted
    table into one long line with every quantity next to the wrong item. The
    text is checked for the table's own separator rather than trusted to
    survive being rendered as prose.
    """
    st.caption(f"{key}{page}")
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) > 1 and all("|" in line for line in lines):
        # A real table: header, a rule, then the rows Streamlit will align.
        columns = len(lines[0].split("|"))
        rule = " | ".join(["---"] * columns)
        st.markdown("\n".join([lines[0], rule, *lines[1:]]))
        return
    st.info(text)


UNASSIGNED = "— nobody yet —"
NOT_ON_TRELLO = "— not on Trello —"


def _save_roles(person_id: str, name: str) -> None:
    """Persist a role change the moment somebody makes one, and only then."""
    api(
        "PATCH",
        f"/people/{person_id}",
        json={"name": name, "roles": st.session_state[f"caps-{person_id}"]},
    )


def render_trello_link(person: dict, members: list[dict]) -> None:
    """Pick which Trello account this person is, if any.

    Never matched on name. Two people called Ahmed Hassan would be one wrong
    assignment apart, and the board would look perfectly correct.
    """
    labels = {NOT_ON_TRELLO: ""}
    for member in members:
        labels[f"@{member['username']} · {member['full_name']}".strip(" ·")] = member["id"]
    by_id = {v: k for k, v in labels.items()}
    current = by_id.get(person.get("trello_member_id", ""), NOT_ON_TRELLO)

    choice = st.selectbox(
        "Trello",
        options=list(labels),
        index=list(labels).index(current),
        key=f"trello-{person['id']}",
        label_visibility="collapsed",
    )
    if labels[choice] != person.get("trello_member_id", ""):
        result = api(
            "PATCH", f"/people/{person['id']}/trello", json={"member_id": labels[choice]}
        )
        if result is not None:
            st.rerun()


def render_people_directory(board_members: list[dict]) -> list[dict]:
    """The company's people, shared by every project.

    Typed by a person, never extracted. The roster comes from the SOW and says
    which roles the work needs; this says who fills them, and the two are kept
    apart so a card can show a name without implying the SOW named anybody.
    """
    people = api("GET", "/people") or []
    active = [p for p in people if p["active"]]
    roster = api("GET", "/people/roles") or []
    titles = [r["title"] for r in roster]

    with st.expander(f"👥 People ({len(active)})", expanded=not active):
        st.caption(
            "Shared across every project. Say what someone does once, and every "
            "SOW you upload from then on staffs itself — a role with exactly one "
            "person who can do it is filled automatically."
        )
        # A form rather than a bare input: Streamlit holds a keyed widget's
        # value across reruns, so the name just added stayed in the box and the
        # next Add re-sent it. clear_on_submit empties it once the add lands.
        with st.form("add-person", clear_on_submit=True):
            name = st.text_input("Name", placeholder="e.g. Ahmed Fathy")
            chosen = st.multiselect("Can work as", options=titles)
            submitted = st.form_submit_button("Add")
        if (
            submitted
            and name.strip()
            and api("POST", "/people", json={"name": name, "roles": chosen})
        ):
            st.rerun()

        if board_members:
            st.caption(
                "Linking somebody to a Trello account assigns them on the card "
                "itself, not just in its text. Only accounts already on this "
                "board are offered — Trello refuses the rest."
            )

        for person in active:
            row, link, action = (
                st.columns([4, 2, 1]) if board_members else (*st.columns([5, 1]), None)
            )
            with row:
                # on_change, not a comparison after the fact. A keyed widget
                # reports its session value on every rerun, so writing whenever
                # that differed from the server let a stale browser tab
                # overwrite correct data without anybody touching anything.
                st.multiselect(
                    person["name"],
                    options=titles,
                    default=[r for r in person["roles"] if r in titles],
                    key=f"caps-{person['id']}",
                    on_change=_save_roles,
                    args=(person["id"], person["name"]),
                )
            if board_members:
                with link:
                    render_trello_link(person, board_members)
            target = action if action is not None else link
            if target.button("Remove", key=f"rm-{person['id']}") and api(
                "DELETE", f"/people/{person['id']}"
            ):
                st.rerun()

        unassignable = [p for p in active if not p["roles"]]
        if unassignable:
            # Not an error: somebody can sit in the directory unassignable and
            # still be picked by hand. But automatic staffing will skip them,
            # and silently skipping is what makes a feature look broken.
            st.caption(
                "No role set, so they will not be staffed automatically: "
                + ", ".join(p["name"] for p in unassignable)
            )

        retired = [p for p in people if not p["active"]]
        if retired:
            # Kept, not deleted: their name still explains who was assigned what
            # on plans that already ran.
            st.caption("No longer here: " + ", ".join(p["name"] for p in retired))
    return active


def render_role_assignment(project_id: str, roles: list[dict], people: list[dict]) -> None:
    """Put a person on each role the SOW asked for."""
    if not people:
        st.info("Add someone to the directory above to assign roles.")
        return

    by_name = {p["name"]: p["id"] for p in people}
    options = [UNASSIGNED, *by_name]

    for role in roles:
        current = role.get("person") or UNASSIGNED
        if current not in options:
            # Whoever held it has since been retired from the directory. Their
            # name stays on the plan; it just cannot be picked again.
            options = [*options, current]
        chosen = st.selectbox(
            f"{TEAM_COLORS.get(role['team'], '')} {role['role_title']}",
            options=options,
            index=options.index(current),
            key=f"role-{role['role_id']}",
        )
        if chosen != current:
            api(
                "PATCH",
                f"/projects/{project_id}/roles/{role['role_id']}",
                json={"person_id": by_name.get(chosen)},
            )
            st.rerun()


def render_timeline(project_id: str) -> None:
    timeline = api("GET", f"/projects/{project_id}/timeline")
    if not timeline:
        return

    if timeline["deadline_breach"]:
        st.error(f"⚠️ {timeline['deadline_breach']}")
    elif timeline["deadline"]:
        st.success(f"Schedule fits the SOW date of {timeline['deadline']}.")

    left, mid, right = st.columns(3)
    left.metric("Start", timeline["project_start"] or "—")
    mid.metric("End", timeline["project_end"] or "—")
    right.metric("Working days", timeline["duration_working_days"])

    for warning in timeline["warnings"]:
        if "dependency" in warning:
            st.warning(warning)

    # Who is on what, against when. Shown on the timeline because it is a
    # scheduling fact, not a staffing preference: the dates are what make it
    # a problem.
    for clash in timeline.get("staffing_clashes") or []:
        st.warning(f"👤 {clash}")

    coverage = timeline.get("estimate_coverage")
    assumed = timeline.get("assumed_durations") or []
    if coverage is not None and assumed:
        st.warning(
            f"Only {coverage:.0%} of these tasks were estimated. The other "
            f"{len(assumed)} use a one-day default, so the dates below show the "
            "right **order** of work but not a measured **duration**. Treat the "
            "end date as provisional until the estimates are filled in."
        )
        with st.expander(f"Tasks running on the default ({len(assumed)})"):
            for task in assumed:
                st.markdown(f"- {task['title']} — {task['team']}")
    elif coverage is not None:
        st.caption(f"Every task carries an effort estimate ({coverage:.0%} coverage).")

    milestones = timeline.get("milestones") or []
    if milestones:
        st.subheader("Against the SOW's own dates")
        st.caption(
            "The SOW commits to checkpoints between kickoff and go-live. Beating the "
            "final deadline says nothing about these."
        )
        icons = {"late": "🔴", "early": "🟠", "on_track": "🟢", "unscheduled": "⚪"}
        for m in milestones:
            st.markdown(f"{icons.get(m['status'], '⚪')} {m['detail']}")
        early = [m for m in milestones if m["status"] == "early"]
        if early:
            st.info(
                f"{len(early)} milestone(s) land well before the agreed date. That is "
                "usually a sign the work behind them was never estimated rather than "
                "that the project is ahead."
            )

    calibration = timeline.get("calibration") or {}
    steps = calibration.get("by_step") or []
    if steps:
        st.subheader("Estimates against the SOW's own windows")
        st.caption(
            "The SOW never states effort, but it does state the dates both parties "
            "agreed the work must fit between. That is the only thing an estimate "
            "here can be checked against."
        )
        rows = []
        for step in steps:
            fill = step["coverage"]
            if fill is None:
                verdict = "no window to compare"
            elif fill < 0.6:
                verdict = "⚠️ likely underestimated"
            elif fill > 1.0:
                verdict = "🔴 needs more time than agreed"
            else:
                verdict = "🟢 fits"
            rows.append(
                {
                    "Step": step["name"],
                    "SOW allows": step["allowed_days"],
                    "Plan uses": step["planned_days"],
                    "Fills": f"{fill:.0%}" if fill is not None else "-",
                    "Verdict": verdict,
                }
            )
        st.dataframe(rows, hide_index=True, width="stretch")
        cov = calibration.get("coverage")
        if cov is not None:
            st.caption(
                f"Across the whole timetable the plan fills {cov:.0%} of the agreed "
                f"time ({calibration['planned_days']} of {calibration['allowed_days']} "
                "working days). Read the steps rather than this number — a plan can "
                "be short overall while running tight on individual steps."
            )

    st.subheader("Critical path")
    st.caption(
        "These tasks have no slack. Any of them slipping pushes the whole "
        "project out, and the chain runs across teams rather than within one."
    )
    critical = timeline["critical_path"]
    if not critical:
        st.info("No tasks scheduled yet.")
    for step, task in enumerate(critical, start=1):
        icon = TEAM_COLORS.get(task["team"], "⚪")
        st.markdown(
            f"**{step}. {icon} {task['title']}**  \n"
            f"{task['start_date']} → {task['due_date']} · {task['duration_days']} day(s) · "
            f"{task['team']}"
        )

    if slack := timeline["slack"]:
        st.subheader("Tasks with slack")
        st.caption("These can start later without delaying go-live.")
        for task in slack:
            st.markdown(f"- {task['title']} — **{task['slack_days']} day(s)** of slack")


VERDICT_LABELS = {
    "unsupported": ("❌", "Nothing in the SOW establishes this"),
    "partial": ("🟡", "Related evidence, but it does not establish this"),
    "contradicted": ("🚫", "The SOW says otherwise"),
}


BOARD_BADGES = {
    (False, False): "⬜ not on the board",
    (True, False): "✅ on the board",
    (True, True): "🔄 on the board, out of date",
}


def board_badge(task: dict, drift_kind: str | None = None) -> str:
    """What the board says about this task, including anything done to it there.

    Drift wins over the plain state. The platform is the only place this is
    reported now, so a card that has been archived must not keep reading "on
    the board" — that is the exact false reassurance the check exists to stop.
    """
    if drift_kind:
        return {
            "archived": "📦 archived on the board — restorable",
            "deleted": "🗑️ deleted from the board",
            "moved": "↔️ moved to another list on the board",
            "edited": "✏️ edited on the board",
        }.get(drift_kind, f"⚠️ {drift_kind}")
    return BOARD_BADGES[(bool(task["external_ref"]), bool(task["board_dirty"]))]


def render_push_button(column, project_id: str, task: dict) -> None:
    """Send this one task to Trello, whenever the PM is happy with it.

    Waiting until all thirty tasks are reviewed means the team waits on the
    slowest item in the plan. An approved task can go now, and the board fills
    up at the pace the review actually runs at.
    """
    on_board = bool(task["external_ref"])
    if on_board and not task["board_dirty"]:
        column.button("✅ On board", key=f"pushed-{task['id']}", disabled=True)
        return

    ready = task["review_status"] in {"approved", "edited"}
    if not column.button(
        "🔄 Update card" if on_board else "Push to Trello",
        key=f"push-{task['id']}",
        disabled=not ready,
        help=None if ready else "Approve this task before it can go to the board.",
    ):
        return

    with st.spinner("Sending to Trello…"):
        result = api("POST", f"/projects/{project_id}/tasks/{task['id']}/push")
    if not result:
        return
    st.session_state[f"push-msg-{task['id']}"] = {
        "message": f"Card {result['action']} on the board.",
        "warnings": result["warnings"],
    }
    st.rerun()


DRIFT_ICONS = {"moved": "↔️", "edited": "✏️", "archived": "📦", "deleted": "🗑️"}


def since(timestamp: str | None) -> str:
    """How long ago, in words a glance can take in."""
    if not timestamp:
        return "never"
    seen = datetime.fromisoformat(timestamp)
    seconds = (datetime.now(UTC) - seen).total_seconds()
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return seen.strftime("%d %b %H:%M")


def render_board_drift(project_id: str, drift: list[dict], checked_at: str | None) -> None:
    """Differences between the board and the plan, with a way to look again.

    Nothing here is a button that fixes anything. Every one of these is
    somebody's deliberate change to the board, and the platform's job is to
    make sure it was not made invisibly — not to undo it.
    """
    header = "Board vs. plan" + (f" — {len(drift)} open" if drift else "")
    with st.expander(header, expanded=bool(drift)):
        if st.button("Check the board now", key="check-board"):
            with st.spinner("Reading the board…"):
                result = api("POST", f"/projects/{project_id}/check-board")
            if result is not None:
                found = len(result["new"])
                st.success(
                    f"{found} new difference(s)." if found else "Nothing new to report."
                )
                st.rerun()

        if not drift:
            # Deliberately not "the board matches the plan". This is a report
            # about the last look, and a look taken before the change was made
            # says nothing about the board now — but the old wording read as
            # an all-clear either way.
            st.caption(
                f"Nothing differed when the board was last read ({since(checked_at)})."
                if checked_at
                else "This board has not been read yet."
            )
            return

        for item in drift:
            icon = DRIFT_ICONS.get(item["kind"], "-")
            st.warning(f"{icon} {item['detail']}")
            # Only the archived case has a way back that keeps anything. A
            # deleted card can only be replaced, so offering "restore" there
            # would promise something the board cannot give.
            if (
                item["kind"] == "archived"
                and st.button("Restore this card", key=f"restore-{item['task_id']}")
                and api(
                    "POST",
                    f"/projects/{project_id}/tasks/{item['task_id']}/restore-card",
                )
            ):
                st.success("Put back, with its comments and ticks.")
                st.rerun()
        st.caption(f"Board last read {since(checked_at)}.")
        st.caption(
            "None of these were changed back. Push the task again to make the "
            "board match the plan, or edit the task if the board was right."
        )


REVIEW_BADGES = {
    "pending": "⏳ awaiting review",
    "approved": "✅ approved",
    "edited": "✏️ edited by PM",
    "rejected": "⛔ rejected",
}


def render_review_controls(project_id: str, task: dict) -> None:
    """Approve, edit, or reject one task.

    Rejection regenerates that task alone, so acting on a bad item does not cost
    the PM the review they already did on everything else.
    """
    st.divider()
    st.caption(f"Review: {REVIEW_BADGES.get(task['review_status'], task['review_status'])}")
    st.caption(f"Board: {board_badge(task, task.get('drift_kind'))}")
    if task["regeneration_count"]:
        st.caption(f"Regenerated {task['regeneration_count']} time(s)")
    if task["review_note"]:
        st.caption(f"Note: {task['review_note']}")

    # Written by the push below, read after the rerun that follows it. Showing
    # it before rerunning would put the message on screen and then throw it
    # away in the same breath.
    if flash := st.session_state.pop(f"push-msg-{task['id']}", None):
        st.success(flash["message"])
        for warning in flash["warnings"]:
            st.warning(warning)

    approve_col, push_col, reject_col = st.columns(3)
    if approve_col.button("Approve", key=f"ok-{task['id']}") and api(
        "POST", f"/projects/{project_id}/tasks/{task['id']}/approve"
    ):
        st.rerun()

    render_push_button(push_col, project_id, task)

    with reject_col.popover("Reject & regenerate"):
        reason = st.text_area(
            "What is wrong with it?",
            key=f"why-{task['id']}",
            placeholder="e.g. the SOW never mentions stress testing",
        )
        if st.button("Regenerate this task", key=f"regen-{task['id']}", type="primary"):
            with st.spinner("Regenerating just this task…"):
                result = api(
                    "POST",
                    f"/projects/{project_id}/tasks/{task['id']}/reject",
                    json={"reason": reason, "regenerate": True},
                )
            if result:
                st.success("Regenerated.")
                st.rerun()

    with st.popover("Edit"):
        new_title = st.text_input("Title", value=task["title"], key=f"t-{task['id']}")
        new_desc = st.text_area(
            "Description", value=task["description"], key=f"d-{task['id']}"
        )
        st.caption("Editing clears the grounding score: it described the generated wording.")
        if st.button("Save edit", key=f"save-{task['id']}") and api(
            "PATCH",
            f"/projects/{project_id}/tasks/{task['id']}",
            json={"title": new_title, "description": new_desc},
        ):
            st.rerun()


def render_audit(project_id: str) -> None:
    data = api("GET", f"/projects/{project_id}/audit")
    if not data:
        return
    st.caption(
        "Every model exchange behind this plan, with the prompt version that "
        "produced it, so a result can be reproduced or explained later."
    )
    st.metric("LLM requests", data["total_requests"])

    if requests := data["llm_requests"]:
        st.subheader("Requests")
        st.dataframe(
            [
                {
                    "node": r["graph_node"],
                    "model": r["model"],
                    "prompt": r["prompt_version"],
                    "input hash": r["input_hash"],
                    "latency (ms)": r["latency_ms"],
                    "output chars": r["output_chars"],
                }
                for r in requests
            ],
            use_container_width=True,
        )
    else:
        st.info("No model calls recorded for this project.")

    st.subheader("Validation")
    for stage in data["validation_stages"]:
        line = f"**{stage['stage']}** ({stage['validator_version']}) — {stage['detail']}"
        (st.success if stage["passed"] else st.error)(line)


def render_grounding(project_id: str) -> None:
    data = api("GET", f"/projects/{project_id}/grounding")
    if not data:
        return
    if data["overall_score"] is None:
        st.info("No claims have been checked yet.")
        return

    st.caption(
        "Every task is split into atomic claims and each is checked against the "
        "SOW on its own. A task can describe the right work and still assert a "
        "number the SOW never gave."
    )

    left, mid, right = st.columns(3)
    left.metric("Overall grounding", f"{data['overall_score']:.0%}")
    mid.metric("Claims checked", data["total_claims"])
    right.metric("Unsupported", data["total_claims"] - data["supported_claims"])

    counts = data["task_status_counts"]
    st.markdown(
        f"**Tasks:** {counts['accept']} accepted · {counts['review']} need review · "
        f"{counts['reject']} rejected"
    )

    st.subheader("By team")
    for team, stats in data["by_team"].items():
        if stats["score"] is not None:
            icon = TEAM_COLORS.get(team, "⚪")
            st.markdown(f"{icon} **{team.title()}** — {stats['score']:.0%} ({stats['claims']} claims)")

    st.subheader("Validation stages")
    for stage in data["validation_stages"]:
        line = f"**{stage['stage']}** — {stage['detail']}"
        (st.success if stage["passed"] else st.error)(line)

    if failures := data["failures"]:
        st.subheader(f"Claims the SOW does not support ({len(failures)})")
        st.caption("These should be removed, corrected, or converted into assumptions.")
        for failure in failures:
            icon, meaning = VERDICT_LABELS.get(failure["verdict"], ("❓", failure["verdict"]))
            with st.expander(f"{icon} {failure['claim'][:80]}"):
                st.caption(f"{meaning} · from task: {failure['task']}")
                st.write(failure["reasoning"])
    else:
        st.success("Every claim is supported by the SOW.")


def render_structure(project_id: str) -> None:
    """The plan as a tree: project, then what each team owns and must prove."""
    structure = api("GET", f"/projects/{project_id}/structure")
    if not structure:
        return

    # 502 when Trello is unreachable, 409 before a board exists. Neither is a
    # reason to withhold the directory, so the picker simply does not appear.
    board_members = api("GET", f"/projects/{project_id}/board-members", quiet=True) or []
    people = render_people_directory(board_members)
    roles = api("GET", f"/projects/{project_id}/roles") or []
    if roles:
        with st.expander("🧑‍💼 Who is on this project", expanded=False):
            st.caption(
                "The SOW says which roles the work needs. This says who fills "
                "them — and that name goes onto every card the role owns."
            )
            render_role_assignment(project_id, roles, people)

    totals = structure["totals"]
    row = st.columns(5)
    row[0].metric("Requirements", totals["requirements"])
    row[1].metric("User stories", totals["user_stories"])
    row[2].metric("Test cases", totals["test_cases"])
    row[3].metric("Detail items", totals["detail_items"])
    row[4].metric("Open questions", totals["open_questions"])

    resting = totals["test_cases_resting_on_assumptions"]
    if resting:
        st.warning(
            f"{resting} test case(s) expect values the SOW never stated. Running one "
            "and seeing it pass proves the system matches an assumption, not the SOW."
        )

    for team, node in structure["teams"].items():
        icon = TEAM_COLORS.get(team, "⚪")
        with st.expander(
            f"{icon} {team.title()} — {node['task_count']} task(s), "
            f"{len(node['requirements'])} requirement(s)"
        ):
            if node["roles"]:
                st.caption("**Roles**")
                for role in node["roles"]:
                    badge, _ = SOURCE_BADGES.get(role["source_status"], ("❓", ""))
                    holder = f" · 👤 {role['person']}" if role.get("person") else ""
                    st.markdown(
                        f"- {badge} **{role['title']}**{holder}"
                        + (f" — {role['responsibility']}" if role["responsibility"] else "")
                    )

            if node["details"]:
                st.caption("**What the SOW lists**")
                for category, items in sorted(node["details"].items()):
                    label = category.replace("_", " ").title()
                    st.markdown(f"*{label}* ({len(items)})")
                    for item in items:
                        badge, _ = SOURCE_BADGES.get(item["source_status"], ("❓", ""))
                        st.markdown(
                            f"&nbsp;&nbsp;&nbsp;{badge} {item['name']}"
                            + (f" — {item['description']}" if item["description"] else ""),
                            unsafe_allow_html=True,
                        )

            if node["unlinked_tasks"]:
                st.caption("**Tasks with no requirement behind them**")
                for task in node["unlinked_tasks"]:
                    st.markdown(
                        f"&nbsp;&nbsp;&nbsp;🔧 {task['title']} — "
                        f"{task['assignee_role'] or 'unassigned'}",
                        unsafe_allow_html=True,
                    )

            for requirement in node["requirements"]:
                badge, _ = SOURCE_BADGES.get(requirement["source_status"], ("❓", ""))
                st.markdown(
                    f"---\n**{badge} {requirement['requirement_id']} — {requirement['title']}**"
                )
                for task in requirement["tasks"]:
                    owner = task["assignee_role"] or "unassigned"
                    due = f" · due {task['due_date']}" if task["due_date"] else ""
                    st.markdown(
                        f"&nbsp;&nbsp;&nbsp;🔧 **{task['title']}** — "
                        f"{task['team'].title()} · {owner}{due}",
                        unsafe_allow_html=True,
                    )
                    if task["delivered_by_another_team"]:
                        st.caption(
                            f"↳ Delivered by the {task['team']} team, so its card is in the "
                            f"**{task['team'].title()}** list on the board — not under "
                            f"{team.title()}."
                        )
                if not requirement["tasks"]:
                    st.caption("⚠️ No task delivers this requirement.")

                if not requirement["user_stories"]:
                    st.caption("No user stories derived for this requirement.")
                for story in requirement["user_stories"]:
                    st.markdown(f"> *{story['story_id']}* — {story['sentence']}")
                    for criterion in story["acceptance_criteria"]:
                        st.markdown(
                            f"&nbsp;&nbsp;&nbsp;☐ {criterion['text']}", unsafe_allow_html=True
                        )
                    for case in story["test_cases"]:
                        header = f"🧪 {case['case_id']} — {case['title']}"
                        if case["rests_on_assumption"]:
                            header += "  ·  ⚠️ rests on an assumption"
                        with st.expander(header):
                            if case["rests_on_assumption"]:
                                st.warning(
                                    "The expected result below depends on "
                                    f"{', '.join(case['assumed_fields'])}, which the SOW "
                                    "does not state. Confirm the value before treating a "
                                    "pass as acceptance."
                                )
                            if case["preconditions"]:
                                st.markdown(f"**Given** {case['preconditions']}")
                            st.markdown(f"**When** {case['action']}")
                            st.markdown(f"**Then** {case['expected_result']}")
                            st.caption(case["kind"].replace("_", " "))


def render_questions(project_id: str) -> None:
    """What the SOW left unanswered, grouped by who can answer it."""
    data = api("GET", f"/projects/{project_id}/questions")
    if not data:
        return
    if not data["total"]:
        st.success("The SOW answered every planning question.")
        return

    st.caption(
        "Each of these is a field the SOW does not settle. The plan runs on the "
        "working assumption shown until someone answers."
    )
    titles = {
        "merchant": "Ask the merchant",
        "offer": "Ask about the offers",
        "project": "Settle internally",
    }
    for scope, questions in data["by_scope"].items():
        if not questions:
            continue
        st.subheader(f"{titles.get(scope, scope.title())} ({len(questions)})")
        for question in questions:
            with st.expander(f"{question['question_id']} — {question['text']}"):
                st.markdown(f"**Working assumption:** {question['working_assumption']}")
                st.caption(
                    f"{question['category']}"
                    + (f" · {question['team']} team" if question["team"] else "")
                )


def render_project(project_id: str, projects: list[dict]) -> None:
    project = next((p for p in projects if p["id"] == project_id), None)
    if not project:
        return

    st.header(project["name"])
    left, mid, right = st.columns(3)
    left.metric("Status", project["status"].replace("_", " ").title())
    mid.metric("Tasks", project["task_count"])
    right.metric("Assumptions", project["assumption_count"])

    tasks = api("GET", f"/projects/{project_id}/tasks") or []
    # One call for the page, not one per task: the worst thing this tab ever
    # did was a request inside every expander.
    drift_by_task = {
        row["task_id"]: row["kind"]
        for row in api("GET", f"/projects/{project_id}/drift") or []
    }
    for task in tasks:
        task["drift_kind"] = drift_by_task.get(task["id"])
    # Fetched once for the whole page rather than once per citation.
    evidence_by_key = api("GET", f"/projects/{project_id}/evidence") or {}
    assumptions = api("GET", f"/projects/{project_id}/assumptions") or []

    review = api("GET", f"/projects/{project_id}/review") or {"counts": {}, "ready": False}
    pending = review["counts"].get("pending", 0)

    questions = api("GET", f"/projects/{project_id}/questions") or {"total": 0}

    (
        tasks_tab,
        structure_tab,
        timeline_tab,
        grounding_tab,
        assumptions_tab,
        questions_tab,
        audit_tab,
        approve_tab,
    ) = st.tabs(
        [
            f"Tasks ({pending} to review)" if pending else "Tasks",
            "Structure",
            "Timeline",
            "Grounding",
            f"Assumptions ({len(assumptions)})",
            f"Questions ({questions['total']})" if questions["total"] else "Questions",
            "Audit",
            "Approve & push",
        ]
    )

    with structure_tab:
        render_structure(project_id)

    with questions_tab:
        render_questions(project_id)

    with tasks_tab:
        if not tasks:
            st.info("No tasks yet.")
        for team in ["commercial", "technical", "operations"]:
            team_tasks = [t for t in tasks if t["team"] == team]
            if not team_tasks:
                continue
            st.subheader(f"{TEAM_COLORS.get(team, '⚪')} {team.title()} ({len(team_tasks)})")
            for task in team_tasks:
                icon, meaning = SOURCE_BADGES.get(task["source_status"], ("❓", "Unknown"))
                grounding = (
                    f"  ·  {task['grounding_score']:.0%} grounded"
                    if task.get("grounding_score") is not None
                    else ""
                )
                with st.expander(
                    f"{icon} {task['title']}  ·  {task['priority'].upper()}{grounding}"
                ):
                    if task.get("validation_status") in {"review", "reject"}:
                        st.warning(
                            f"Grounding: {task['validation_status'].upper()} — "
                            "see the Grounding tab for the specific claims."
                        )
                    if task["description"]:
                        st.write(task["description"])
                    owner = task.get("assignee_role") or "unassigned"
                    st.caption(
                        f"{meaning} · {owner} · {task['estimated_hours'] or '?'}h estimated"
                    )
                    if task["start_date"]:
                        st.caption(f"Scheduled: {task['start_date']} → {task['due_date']}")
                    render_blockers(task["depends_on"], evidence_by_key)
                    if task["source_section"]:
                        st.caption(f"SOW section: {task['source_section']}")
                    for key in task["source_chunk_keys"]:
                        evidence = evidence_by_key.get(key)
                        if evidence:
                            page = f" · page {evidence['page']}" if evidence["page"] else ""
                            render_evidence(key, page, evidence["text"])
                    if task["external_ref"]:
                        st.caption(f"Trello card: {task['external_ref']}")
                    render_review_controls(project_id, task)

    with timeline_tab:
        render_timeline(project_id)

    with grounding_tab:
        render_grounding(project_id)

    with audit_tab:
        render_audit(project_id)

    with assumptions_tab:
        st.caption(
            "These values are not in the SOW. The system introduced them to fill a "
            "gap and flags them rather than presenting them as fact."
        )
        for assumption in assumptions:
            confidence = assumption["confidence"]
            st.warning(
                f"**{assumption['assumption_id']} · {assumption['category']}** — "
                f"{assumption['value']}\n\n"
                f"_{assumption['reason']}_"
                + (f" (confidence {confidence:.0%})" if confidence else "")
            )
        if not assumptions:
            st.success("No assumptions were needed — the SOW covered everything extracted.")

    with approve_tab:
        render_board_drift(
            project_id,
            api("GET", f"/projects/{project_id}/drift") or [],
            project.get("board_checked_at"),
        )
        st.caption(
            "Nothing reaches Trello until a PM approves. Push tasks one at a time "
            "from the Tasks tab as you finish reviewing them, or approve the plan "
            "as a whole and send whatever is still outstanding from here."
        )
        counts = review["counts"]
        st.markdown(
            f"**Task review:** {counts.get('approved', 0)} approved · "
            f"{counts.get('edited', 0)} edited · {counts.get('pending', 0)} pending · "
            f"{counts.get('rejected', 0)} rejected"
        )
        if counts.get("rejected"):
            st.error(
                f"{counts['rejected']} task(s) are still rejected. Regenerate or edit "
                "them before pushing — rejected work must not reach the board."
            )
        elif counts.get("pending"):
            st.warning(
                f"{counts['pending']} task(s) have not been reviewed yet. You can still "
                "approve the project, but they will go to the board unreviewed."
            )
        on_board = [t for t in tasks if t["external_ref"]]
        stale = [t for t in on_board if t["board_dirty"]]
        outstanding = len(tasks) - len(on_board) + len(stale)
        st.markdown(
            f"**On the board:** {len(on_board)} of {len(tasks)} tasks"
            + (f" · {len(stale)} out of date" if stale else "")
        )

        if project["status"] == "synced":
            st.success("Every task is on the board and up to date.")

        # partially_synced counts as approved: the PM cannot lose the ability to
        # push the rest just because they pushed one task first.
        approved = project["status"] in {"approved", "partially_synced", "synced"}
        if not approved:
            if st.button("Approve project", type="primary") and api(
                "POST", f"/projects/{project_id}/approve"
            ):
                st.success("Approved.")
                st.rerun()
        else:
            st.success("Approved by PM.")
            if st.button(
                f"Push remaining to Trello ({outstanding})",
                type="primary",
                disabled=not outstanding,
                help=None if outstanding else "Nothing is waiting to be sent.",
            ):
                with st.spinner("Sending to Trello…"):
                    result = api("POST", f"/projects/{project_id}/push")
                if result:
                    st.success(
                        f"{result['cards_created']} card(s) created, "
                        f"{result['cards_updated']} updated."
                    )
                    for warning in result["warnings"]:
                        st.warning(warning)
            if project.get("board_url"):
                st.link_button("Open Trello board", project["board_url"])


def main() -> None:
    st.title("📄 SOW → Project Execution Platform")

    health = api("GET", "/health")
    if health and health.get("database") != "up":
        st.warning("API is reachable but the database is down. Run: docker compose up -d")

    projects = api("GET", "/projects") or []
    with st.sidebar:
        st.subheader("Projects")
        if projects:
            labels = {p["id"]: f"{p['name']} ({p['task_count']} tasks)" for p in projects}
            selected = st.radio(
                "Select a project",
                options=list(labels),
                format_func=lambda pid: labels[pid],
                index=0,
                label_visibility="collapsed",
            )
            st.session_state.setdefault("project_id", selected)
            if selected:
                st.session_state["project_id"] = selected
        else:
            st.caption("No projects yet — upload a SOW.")

    render_upload()
    st.divider()
    if project_id := st.session_state.get("project_id"):
        render_project(project_id, projects)


main()
