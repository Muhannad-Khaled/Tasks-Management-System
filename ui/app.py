"""Streamlit UI for the M1 slice.

Talks to FastAPI over HTTP rather than the database directly, so the API stays
the single write path. Later milestones add the grounding, audit, timeline and
copilot pages described in the brief.
"""

from __future__ import annotations

import os

import httpx
import streamlit as st

API_BASE = os.environ.get("API_BASE", "http://localhost:8000")

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
        st.error(f"{method} {path} failed ({response.status_code}): {detail}")
        return None
    return response.json()


def render_upload() -> None:
    st.header("Upload a Statement of Work")
    st.caption(
        "The SOW is the single source of truth. Everything generated below "
        "traces back to a specific chunk of this document."
    )
    uploaded = st.file_uploader("SOW file", type=["pdf", "docx", "txt"])
    if uploaded and st.button("Process SOW", type="primary"):
        with st.spinner("Parsing, validating, and extracting…"):
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
                f"Extracted {result['task_count']} tasks "
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
    assumptions = api("GET", f"/projects/{project_id}/assumptions") or []

    tasks_tab, timeline_tab, assumptions_tab, approve_tab = st.tabs(
        ["Tasks", "Timeline", f"Assumptions ({len(assumptions)})", "Approve & push"]
    )

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
                with st.expander(f"{icon} {task['title']}  ·  {task['priority'].upper()}"):
                    if task["description"]:
                        st.write(task["description"])
                    st.caption(f"{meaning} · {task['estimated_hours'] or '?'}h estimated")
                    if task["start_date"]:
                        st.caption(f"Scheduled: {task['start_date']} → {task['due_date']}")
                    if task["depends_on"]:
                        st.caption("Blocked by: " + "; ".join(task["depends_on"]))
                    if task["source_section"]:
                        st.caption(f"SOW section: {task['source_section']}")
                    for key in task["source_chunk_keys"]:
                        evidence = api("GET", f"/projects/{project_id}/evidence/{key}")
                        if evidence:
                            page = f" · page {evidence['page']}" if evidence["page"] else ""
                            st.info(f"**{key}**{page}\n\n{evidence['text']}")
                    if task["external_ref"]:
                        st.caption(f"Trello card: {task['external_ref']}")

    with timeline_tab:
        render_timeline(project_id)

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
