"""Streamlit UI for the M1 slice.

Talks to FastAPI over HTTP rather than the database directly, so the API stays
the single write path. Later milestones add the grounding, audit, timeline and
copilot pages described in the brief.
"""

from __future__ import annotations

import os

import httpx
import streamlit as st

# 127.0.0.1, not localhost: localhost resolves to ::1 first and uvicorn
# binds IPv4 only, so every call paid ~2 seconds waiting out the failed
# IPv6 attempt before falling back. Nine times slower, from a hostname.
API_BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000")

TEAM_COLORS = {"commercial": "🔵", "technical": "🟣", "operations": "🟠"}
SOURCE_BADGES = {
    "explicit": ("✅", "Stated in the SOW"),
    "inferred": ("🔎", "Derived from the SOW"),
    "assumed": ("⚠️", "Not in the SOW — assumed"),
}

st.set_page_config(page_title="SOW → Project", page_icon="📄", layout="wide")


def api(method: str, path: str, **kwargs):
    try:
        response = httpx.request(method, f"{API_BASE}{path}", timeout=180, **kwargs)
    except httpx.RequestError as exc:
        st.error(f"Cannot reach the API at {API_BASE}. Is uvicorn running?\n\n{exc}")
        st.stop()
    if response.status_code >= 400:
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
    if task["regeneration_count"]:
        st.caption(f"Regenerated {task['regeneration_count']} time(s)")
    if task["review_note"]:
        st.caption(f"Note: {task['review_note']}")

    approve_col, reject_col = st.columns(2)
    if approve_col.button("Approve", key=f"ok-{task['id']}") and api(
        "POST", f"/projects/{project_id}/tasks/{task['id']}/approve"
    ):
        st.rerun()

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
                    st.markdown(
                        f"- {badge} **{role['title']}**"
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
                    if task["depends_on"]:
                        st.caption("Blocked by: " + "; ".join(task["depends_on"]))
                    if task["source_section"]:
                        st.caption(f"SOW section: {task['source_section']}")
                    for key in task["source_chunk_keys"]:
                        evidence = evidence_by_key.get(key)
                        if evidence:
                            page = f" · page {evidence['page']}" if evidence["page"] else ""
                            st.info(f"**{key}**{page}\n\n{evidence['text']}")
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
        st.caption(
            "Nothing reaches Trello until a PM approves. Review the tasks, their "
            "evidence, and the assumptions first."
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
        if project["status"] == "synced":
            st.success("Already pushed to Trello.")
        approved = project["status"] in {"approved", "synced"}
        if not approved:
            if st.button("Approve project", type="primary") and api(
                "POST", f"/projects/{project_id}/approve"
            ):
                st.success("Approved.")
                st.rerun()
        else:
            st.success("Approved by PM.")
            if st.button("Push to Trello", type="primary"):
                with st.spinner("Creating board and cards…"):
                    result = api("POST", f"/projects/{project_id}/push")
                if result:
                    st.success(f"Created {result['cards_created']} cards.")
                    st.link_button("Open Trello board", result["board_url"])


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
