"""
n8n Workflow Showcase
---------------------
Streamlit front-end that:
  1. Reads a catalog of workflows from a Notion database (title, description,
     tags, GitHub raw JSON URL).
  2. Shows them as a browsable grid.
  3. On click, fetches the workflow JSON from GitHub and renders a live,
     read-only visual preview using n8n's official <n8n-demo> web component.

Setup:
  1. pip install -r requirements.txt
  2. Create .streamlit/secrets.toml with:
        NOTION_TOKEN = "secret_xxx"
        NOTION_DATABASE_ID = "xxxxxxxx"
  3. Your Notion database needs these properties (case-sensitive):
        Title          -> Title property
        Description    -> Text property
        Tags           -> Multi-select property
        GitHub Raw URL -> URL property (raw.githubusercontent.com link to the .json)
        Difficulty     -> Select property (optional)
  4. streamlit run app.py
"""

import json
import html as html_lib

import requests
import streamlit as st
import streamlit.components.v1 as components
from notion_client import Client

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

st.set_page_config(
    page_title="n8n Workflow Library",
    page_icon="🔗",
    layout="wide",
)

NOTION_TOKEN = st.secrets.get("NOTION_TOKEN", "")
NOTION_DATABASE_ID = st.secrets.get("NOTION_DATABASE_ID", "")


# --------------------------------------------------------------------------
# Data layer: Notion
# --------------------------------------------------------------------------

@st.cache_resource
def get_notion_client() -> Client:
    return Client(auth=NOTION_TOKEN)


def _prop_text(prop: dict) -> str:
    """Extract plain text from a Notion 'title' or 'rich_text' property."""
    if not prop:
        return ""
    kind = prop.get("type")
    if kind in ("title", "rich_text"):
        parts = prop.get(kind, [])
        return "".join(p.get("plain_text", "") for p in parts)
    return ""


def _prop_url(prop: dict) -> str:
    if not prop:
        return ""
    return prop.get("url") or ""


def _prop_multiselect(prop: dict) -> list:
    if not prop:
        return []
    return [opt.get("name", "") for opt in prop.get("multi_select", [])]


def _prop_select(prop: dict) -> str:
    if not prop:
        return ""
    sel = prop.get("select")
    return sel.get("name", "") if sel else ""


@st.cache_data(ttl=300, show_spinner="Loading workflows from Notion...")
def fetch_workflows() -> list[dict]:
    """Pull every row from the Notion database and normalize it."""
    if not NOTION_TOKEN or not NOTION_DATABASE_ID:
        return []

    client = get_notion_client()
    workflows = []
    cursor = None

    while True:
        resp = client.databases.query(
            database_id=NOTION_DATABASE_ID,
            start_cursor=cursor,
        )
        for page in resp.get("results", []):
            props = page.get("properties", {})
            workflows.append(
                {
                    "id": page["id"],
                    "title": _prop_text(props.get("Title")),
                    "description": _prop_text(props.get("Description")),
                    "tags": _prop_multiselect(props.get("Tags")),
                    "difficulty": _prop_select(props.get("Difficulty")),
                    "github_url": _prop_url(props.get("GitHub Raw URL")),
                }
            )
        if resp.get("has_more"):
            cursor = resp.get("next_cursor")
        else:
            break

    # Drop rows that aren't usable yet (no title or no source url)
    return [w for w in workflows if w["title"] and w["github_url"]]


@st.cache_data(ttl=300, show_spinner=False)
def fetch_workflow_json(raw_url: str) -> tuple[dict | None, str | None]:
    """Fetch and parse the workflow JSON from GitHub. Returns (data, error)."""
    try:
        resp = requests.get(raw_url, timeout=10)
        resp.raise_for_status()
        return resp.json(), None
    except requests.exceptions.RequestException as e:
        return None, f"Could not reach GitHub: {e}"
    except json.JSONDecodeError as e:
        return None, f"File isn't valid JSON: {e}"


# --------------------------------------------------------------------------
# n8n-demo embed
# --------------------------------------------------------------------------

N8N_DEMO_SCRIPT = (
    "https://cdn.jsdelivr.net/npm/@n8n_io/n8n-demo-component/"
    "n8n-demo.bundled.js"
)


def render_n8n_preview(workflow_json: dict, height: int = 550):
    """Embed the official n8n-demo web component with the workflow JSON."""
    # The component wants a JSON string as an HTML attribute value, so we
    # escape it for safe embedding inside single quotes.
    workflow_str = html_lib.escape(json.dumps(workflow_json), quote=True)

    widget_html = f"""
    <script type="module" src="{N8N_DEMO_SCRIPT}"></script>
    <style>
      n8n-demo {{
        display: block;
        --n8n-workflow-min-height: {height - 20}px;
        --n8n-iframe-border-radius: 12px;
      }}
    </style>
    <n8n-demo
      workflow="{workflow_str}"
      frame="true"
      tidyup="true">
    </n8n-demo>
    """
    components.html(widget_html, height=height, scrolling=True)


# --------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------

def show_grid(workflows: list[dict]):
    st.title("🔗 n8n Workflow Library")
    st.caption("Click a workflow to see a live visual preview.")

    all_tags = sorted({t for w in workflows for t in w["tags"]})
    col_search, col_tag = st.columns([2, 1])
    with col_search:
        query = st.text_input("Search", placeholder="Search workflows...")
    with col_tag:
        tag_filter = st.selectbox("Filter by tag", ["All"] + all_tags)

    filtered = workflows
    if query:
        q = query.lower()
        filtered = [
            w for w in filtered
            if q in w["title"].lower() or q in w["description"].lower()
        ]
    if tag_filter != "All":
        filtered = [w for w in filtered if tag_filter in w["tags"]]

    if not filtered:
        st.info("No workflows match your filters yet.")
        return

    cols = st.columns(3)
    for i, wf in enumerate(filtered):
        with cols[i % 3]:
            with st.container(border=True):
                st.subheader(wf["title"])
                if wf["tags"]:
                    st.caption(" · ".join(wf["tags"]))
                st.write(
                    wf["description"][:140]
                    + ("..." if len(wf["description"]) > 140 else "")
                )
                if st.button("View workflow", key=f"open_{wf['id']}"):
                    st.session_state["selected_id"] = wf["id"]
                    st.rerun()


def show_detail(workflow: dict, all_workflows: list[dict]):
    if st.button("← Back to all workflows"):
        st.session_state["selected_id"] = None
        st.rerun()

    st.title(workflow["title"])
    if workflow["tags"]:
        st.caption(" · ".join(workflow["tags"]))
    if workflow["description"]:
        st.write(workflow["description"])

    data, error = fetch_workflow_json(workflow["github_url"])

    if data is not None:
        render_n8n_preview(data)
    else:
        st.warning(
            "Couldn't render the visual preview for this workflow "
            f"({error}). You can still view or copy the raw JSON below."
        )

    st.markdown(f"[View raw JSON on GitHub]({workflow['github_url']})")

    if data is not None:
        with st.expander("Raw workflow JSON"):
            st.code(json.dumps(data, indent=2), language="json")


def main():
    if not NOTION_TOKEN or not NOTION_DATABASE_ID:
        st.error(
            "Missing Notion credentials. Add NOTION_TOKEN and "
            "NOTION_DATABASE_ID to .streamlit/secrets.toml."
        )
        st.stop()

    workflows = fetch_workflows()

    if "selected_id" not in st.session_state:
        st.session_state["selected_id"] = None

    if not workflows:
        st.info(
            "No workflows found yet. Add rows to your Notion database "
            "(Title, Description, Tags, GitHub Raw URL) to get started."
        )
        return

    selected_id = st.session_state["selected_id"]
    if selected_id:
        selected = next((w for w in workflows if w["id"] == selected_id), None)
        if selected:
            show_detail(selected, workflows)
        else:
            st.session_state["selected_id"] = None
            show_grid(workflows)
    else:
        show_grid(workflows)


if __name__ == "__main__":
    main()
