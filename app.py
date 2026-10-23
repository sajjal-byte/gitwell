import io
import zipfile

import streamlit as st

import gitwell as gw

st.set_page_config(page_title="GitWell", layout="wide")

CSS = """
<style>
#MainMenu, footer {visibility: hidden;}
.block-container {padding-top: 2.2rem; max-width: 1180px;}
h1 {font-weight: 650; letter-spacing: -0.02em;}
h3 {letter-spacing: -0.01em;}
[data-testid="stForm"] {border: none; padding: 0;}
[data-testid="stMetric"] {
    background: #161b22; border: 1px solid #30363d;
    border-radius: 10px; padding: 12px 16px;
}
[data-testid="stMetricLabel"] {color: #8b949e;}
.gw-card {
    background: #161b22; border: 1px solid #30363d; border-radius: 10px;
    padding: 12px 16px; margin-bottom: 10px;
}
.gw-muted {color: #8b949e; font-size: 0.88rem; margin-top: 2px;}
.gw-pill {
    display: inline-block; padding: 1px 9px; margin-left: 6px;
    border-radius: 999px; font-size: 0.74rem; font-weight: 600;
    border: 1px solid #30363d;
}
.gw-bad {color: #f0883e; border-color: #7d4a1f; background: rgba(240,136,62,.10);}
.stTabs [data-baseweb="tab-list"] {gap: 4px;}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

st.title("GitWell")
st.caption("Understand any GitHub repo and generate the docs it is missing. "
           "Built for first-time contributors. Runs on open-weight Gemma (local) or Gemini.")

with st.sidebar:
    st.header("AI engine")
    choice = st.radio("Model", ["Gemma (local, private)", "Gemini (cloud)"])
    backend = "gemma" if choice.startswith("Gemma") else "gemini"
    if backend == "gemma":
        st.caption("Runs locally through Ollama. Repo content is not sent to a cloud API.")
    else:
        st.caption("Uses GEMINI_API_KEY from your .env file.")
    st.divider()
    st.caption("Set GITHUB_TOKEN in .env to avoid the 60 requests/hour GitHub API limit.")

with st.form("scan"):
    c_in, c_btn = st.columns([6, 1])
    url = c_in.text_input("GitHub repo", placeholder="https://github.com/owner/repo",
                          label_visibility="collapsed")
    go = c_btn.form_submit_button("Analyze", type="primary")

if go and url:
    try:
        with st.spinner("Scanning repository..."):
            st.session_state.info = gw.scan_repo(url)
        st.session_state.docs = {}
        st.session_state.chat = []
    except Exception as e:
        st.session_state.pop("info", None)
        st.error(str(e))

info = st.session_state.get("info")
if not info:
    st.caption("Paste a public repo URL to get a codebase walkthrough, setup commands, "
               "open starter issues and generated docs.")
    st.stop()

docs = st.session_state.setdefault("docs", {})
score = info["score"]
days = info["days_since_push"]


def make_doc(name):
    """Generate one doc into session state. Returns True on success."""
    try:
        docs[name] = gw.generate(name, info, backend)
        return True
    except Exception as e:
        hint = ("Make sure Ollama is running and the model is pulled."
                if backend == "gemma" else "Check GEMINI_API_KEY in your .env file.")
        st.error(f"Could not generate {name}: {e}. {hint}")
        return False


# ---- header ---------------------------------------------------------------
st.markdown(f"### [{info['full_name']}]({info['url']})")
if info["description"]:
    st.caption(info["description"])

m = st.columns(5)
m[0].metric("Health score", f"{score}/100")
m[1].metric("Status", gw.verdict(score))
m[2].metric("Main language", info["languages"][0] if info["languages"] else "n/a")
m[3].metric("Stars", f"{info['stars']:,}")
m[4].metric("Last push", "n/a" if days is None else f"{days}d ago")
st.progress(score / 100)

tab_over, tab_contrib, tab_health, tab_docs, tab_ask = st.tabs(
    ["Overview", "Contribute", "Health", "Docs", "Ask"])

# ---- overview -------------------------------------------------------------
with tab_over:
    st.subheader("What this repo does")
    guide = "REPO_GUIDE.md"
    if guide not in docs:
        st.caption("Generates a walkthrough for new contributors: purpose, structure, "
                   "execution flow, where to start reading, where to make changes.")
        if st.button("Explain this repo", type="primary"):
            with st.spinner("Reading the repo..."):
                make_doc(guide)
    if guide in docs:
        st.markdown(docs[guide])
        st.download_button(f"Download {guide}", docs[guide], file_name=guide, key="dl_guide")

    st.divider()
    left, right = st.columns([1, 2])
    with left:
        st.subheader("Languages")
        for name, pct in info["language_pct"][:6]:
            st.progress(min(pct / 100, 1.0), text=f"{name}  {pct}%")
    with right:
        st.subheader("Project layout")
        st.dataframe(info["project_map"], hide_index=True)

    st.subheader("Start reading here")
    if info["key_files"]:
        st.markdown("\n".join(f"{i}. `{k['path']}`  \n   {k['why']}"
                              for i, k in enumerate(info["key_files"], 1)))
    else:
        st.caption("No obvious entry points detected.")

# ---- contribute -----------------------------------------------------------
with tab_contrib:
    st.subheader("Run it locally")
    if info["commands"]:
        st.code("\n".join(f"# {label}\n{cmd}" for label, cmd in info["commands"]),
                language="bash")
        st.caption("Detected from the repo's manifests. Verify against the README.")
    else:
        st.info("No standard manifest found. Check the README for setup steps.")

    st.subheader("Open issues to pick up")
    a, b = st.columns(2)
    for col, title, items, empty in (
            (a, "good first issue", info["good_first_issues"],
             "No open issues with this label."),
            (b, "help wanted", info["help_wanted"],
             "No open issues with this label.")):
        with col:
            st.markdown(f"**{title}**")
            if not items:
                st.caption(empty)
            for i in items:
                st.markdown(f"[#{i['number']} {i['title']}]({i['url']})")
                st.caption(f"{i['comments']} comments")

    st.subheader("Activity")
    a, b = st.columns(2)
    with a:
        st.markdown("**Recent commits**")
        if not info["commits"]:
            st.caption("Not available.")
        for c in info["commits"]:
            st.markdown(f"[`{c['sha']}`]({c['url']}) {c['message']}")
            st.caption(f"{c['author']}, {c['date']}")
    with b:
        st.markdown("**Top contributors**")
        if not info["contributors"]:
            st.caption("Not available.")
        for c in info["contributors"]:
            st.markdown(f"[{c['login']}]({c['url']}): {c['commits']} commits")
        st.caption(f"{info['open_issues']} open issues and PRs, {info['forks']:,} forks")

    st.divider()
    st.subheader("Starter task ideas")
    tasks = "GOOD_FIRST_ISSUES.md"
    if tasks not in docs:
        st.caption("Generated from gaps visible in the code: missing docs, tests, error handling.")
        if st.button("Suggest starter tasks"):
            with st.spinner("Looking for gaps..."):
                make_doc(tasks)
    if tasks in docs:
        st.markdown(docs[tasks])

# ---- health ---------------------------------------------------------------
with tab_health:
    failing = sorted((c for c in info["checks"] if not c["ok"]), key=lambda c: -c["weight"])
    passing = [c for c in info["checks"] if c["ok"]]
    left, right = st.columns(2)
    with left:
        st.subheader("Gaps")
        st.caption("Highest score impact first.")
        if not failing:
            st.success("All checks pass.")
        for c in failing:
            st.markdown(
                f"<div class='gw-card'><b>{c['name']}</b>"
                f"<span class='gw-pill gw-bad'>+{c['weight']} pts</span>"
                f"<div class='gw-muted'>{c['fix']}</div></div>",
                unsafe_allow_html=True)
    with right:
        st.subheader("Passing")
        if passing:
            st.markdown("\n".join(f"- {c['name']}" for c in passing))
        else:
            st.caption("Nothing passing yet.")
        st.subheader("README badge")
        st.code(gw.badge_markdown(info), language="markdown")

# ---- docs -----------------------------------------------------------------
with tab_docs:
    st.subheader("Generated docs")
    st.caption("Drafted from the repo's real files. Unknown details are marked TODO. "
               "Review before committing.")
    missing = [n for n in gw.DOCS if n not in docs]
    if missing and st.button(f"Generate {len(missing)} remaining doc(s)", type="primary"):
        bar = st.progress(0.0, text="Starting...")
        for i, name in enumerate(missing):
            bar.progress(i / len(missing), text=f"Writing {name}")
            if not make_doc(name):
                break
        bar.progress(1.0, text="Done")

    if docs:
        tabs = st.tabs(list(docs))
        for tab, (name, text) in zip(tabs, docs.items()):
            with tab:
                st.download_button(f"Download {name}", text, file_name=name, key=f"dl_{name}")
                st.markdown(text, unsafe_allow_html=False)
                with st.expander("Raw markdown"):
                    st.code(text, language="markdown")

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("REPORT.md", gw.diagnosis_markdown(info))
            for n, t in docs.items():
                z.writestr(n, t)
        st.download_button("Download all (.zip)", buf.getvalue(),
                           file_name="gitwell-docs.zip", mime="application/zip")

# ---- ask ------------------------------------------------------------------
with tab_ask:
    st.subheader("Ask about this repo")
    st.caption("Examples: where is the entry point, how does X work, where should I add Y. "
               "Mention a file path (for example app.py) to include its source in the answer.")
    chat = st.session_state.setdefault("chat", [])
    for msg in chat:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
    question = st.chat_input("Ask a question about the codebase")
    if question:
        history = list(chat)
        chat.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            try:
                with st.spinner("Reading the code..."):
                    answer = gw.ask(question, info, backend, history)
            except Exception as e:
                answer = (f"Request failed: {e}. "
                          + ("Make sure Ollama is running." if backend == "gemma"
                             else "Check GEMINI_API_KEY."))
            st.markdown(answer)
        chat.append({"role": "assistant", "content": answer})