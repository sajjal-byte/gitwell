import io
import zipfile

import streamlit as st

import gitwell as gw

st.set_page_config(page_title="GitWell", layout="wide")

CSS = """
<style>
#MainMenu, footer {visibility: hidden;}
.stApp {
    background: radial-gradient(circle at top left, rgba(122,162,255,0.12), transparent 26%),
                radial-gradient(circle at right top, rgba(71,214,170,0.10), transparent 20%),
                linear-gradient(180deg, rgba(13,17,23,1) 0%, rgba(10,14,20,1) 100%);
}
.block-container {
    padding-top: 1.6rem;
    max-width: 1280px;
    padding-bottom: 3rem;
}
h1 {
    font-weight: 700;
    letter-spacing: -0.04em;
    margin-bottom: 0.25rem;
}
h3 {
    letter-spacing: -0.01em;
}
[data-testid="stForm"] {
    border: 1px solid rgba(48, 54, 61, 0.9);
    background: rgba(22,27,34,0.75);
    border-radius: 18px;
    padding: 0.9rem 1rem 0.25rem;
    box-shadow: 0 8px 24px rgba(0,0,0,0.12);
}
[data-testid="stMetric"] {
    background: linear-gradient(180deg, rgba(22,27,34,0.96), rgba(17,24,39,0.98));
    border: 1px solid rgba(48,54,61,0.95);
    border-radius: 14px;
    padding: 14px 16px;
    box-shadow: 0 8px 16px rgba(0,0,0,0.08);
}
[data-testid="stMetricLabel"] {color: #8b949e;}
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, rgba(13,17,23,0.94), rgba(11,15,22,0.98));
    border-right: 1px solid rgba(48,54,61,0.75);
}
[data-testid="stSidebarNav"] {padding-top: 0.8rem;}
.stTextInput > div > div > input,
.stSelectbox > div > div,
.stTextArea > div > div {
    background: rgba(13,17,23,0.85);
    border-radius: 10px;
    border: 1px solid rgba(110,118,129,0.45);
}
.stButton > button {
    border-radius: 10px;
    font-weight: 600;
    padding: 0.55rem 1rem;
    background: linear-gradient(180deg, #2f81f7, #1f6feb);
    border: 1px solid rgba(88,166,255,0.85);
    box-shadow: 0 8px 18px rgba(31,111,235,0.25);
}
.stButton > button:hover {
    background: linear-gradient(180deg, #3b8ef7, #2b74ea);
    border-color: rgba(102,180,255,0.95);
}
.gw-banner {
    background: linear-gradient(135deg, rgba(31,111,235,0.20), rgba(154,92,255,0.12), rgba(19,50,87,0.18));
    border: 1px solid rgba(110,118,129,0.45);
    border-radius: 22px;
    padding: 1.1rem 1.2rem 1.3rem;
    margin-bottom: 1.1rem;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.04), 0 18px 48px rgba(31,111,235,0.08);
}
.gw-badge {
    display: inline-block;
    padding: 0.2rem 0.55rem;
    background: rgba(122,162,255,0.18);
    color: #b9d1ff;
    border: 1px solid rgba(122,162,255,0.35);
    border-radius: 999px;
    font-size: 0.72rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
}
.gw-banner-title {
    font-size: clamp(2rem, 3vw, 3rem);
    font-weight: 800;
    letter-spacing: -0.06em;
    line-height: 1.08;
    margin-top: 0.55rem;
}
.gw-banner-subtitle {
    color: rgba(230,237,243,0.8);
    font-size: 1rem;
    margin-top: 0.3rem;
}
.gw-card {
    background: rgba(22,27,34,0.9);
    border: 1px solid rgba(48,54,61,0.95);
    border-radius: 12px;
    padding: 12px 16px;
    margin-bottom: 10px;
    box-shadow: 0 6px 18px rgba(0,0,0,0.08);
}
.gw-muted {color: #8b949e; font-size: 0.88rem; margin-top: 2px;}
.gw-pill {
    display: inline-block; padding: 1px 9px; margin-left: 6px;
    border-radius: 999px; font-size: 0.74rem; font-weight: 600;
    border: 1px solid #30363d;
}
.gw-bad {color: #f0883e; border-color: #7d4a1f; background: rgba(240,136,62,.10);}
.gw-hero {
    background: linear-gradient(180deg, rgba(17,24,39,0.96), rgba(13,17,23,0.94));
    border: 1px solid rgba(48,54,61,0.9);
    border-radius: 18px;
    padding: 1rem 1.1rem 0.8rem;
    margin-bottom: 1rem;
    box-shadow: 0 10px 30px rgba(0,0,0,0.10);
}
.gw-hero-top {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    margin-bottom: 0.4rem;
}
.gw-hero-title {
    font-weight: 800;
    font-size: clamp(1.35rem, 2vw, 2rem);
    letter-spacing: -0.05em;
    margin: 0.2rem 0 0.25rem;
}
.gw-hero-sub {
    color: rgba(230,237,243,0.8);
    margin-bottom: 0.9rem;
}
.gw-meta-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
    gap: 0.75rem;
}
.gw-stat {
    background: rgba(31,36,45,0.8);
    border: 1px solid rgba(48,54,61,0.9);
    border-radius: 12px;
    padding: 0.7rem 0.8rem;
}
.gw-stat-label {
    display: block;
    color: #8b949e;
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 0.25rem;
}
.gw-stat strong {
    font-size: 1rem;
    font-weight: 700;
}
.stTabs [data-baseweb="tab-list"] {gap: 8px;} 
.stTabs [data-baseweb="tab"] {
    border-radius: 10px 10px 0 0;
    height: 42px;
    padding: 0 12px;
}
.stTabs [data-baseweb="tab-highlight"] {
    height: 42px;
    border-radius: 10px;
    background: rgba(31,111,235,0.2);
}
.stProgress > div > div {
    background: linear-gradient(90deg, #79c0ff, #58a6ff 50%, #7ee787);
}
div[data-testid="stCodeBlock"] {
    border-radius: 12px;
    border: 1px solid rgba(48,54,61,0.9);
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.03);
}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

st.markdown("""
<div class="gw-banner">
  <div class="gw-badge">GitHub repo intelligence</div>
  <div class="gw-banner-title">GitWell</div>
  <div class="gw-banner-subtitle">Understand any GitHub repo and generate the docs it is missing — built for first-time contributors.</div>
</div>
""", unsafe_allow_html=True)

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
status_text = gw.verdict(score)
st.markdown(f"""
<div class="gw-hero">
  <div class="gw-hero-top">
    <span class="gw-badge">Repository overview</span>
    <span class="gw-pill {'gw-bad' if score < 60 else ''}">{status_text}</span>
  </div>
  <div class="gw-hero-title"><a href="{info['url']}" style="color: inherit; text-decoration: none;">{info['full_name']}</a></div>
  <div class="gw-hero-sub">{info['description'] or 'No description provided for this repository.'}</div>
  <div class="gw-meta-grid">
    <div class="gw-stat"><span class="gw-stat-label">Health score</span><strong>{score}/100</strong></div>
    <div class="gw-stat"><span class="gw-stat-label">Main language</span><strong>{info['languages'][0] if info['languages'] else 'n/a'}</strong></div>
    <div class="gw-stat"><span class="gw-stat-label">Stars</span><strong>{info['stars']:,}</strong></div>
    <div class="gw-stat"><span class="gw-stat-label">Forks</span><strong>{info['forks']:,}</strong></div>
    <div class="gw-stat"><span class="gw-stat-label">Last push</span><strong>{'n/a' if days is None else f'{days}d ago'}</strong></div>
  </div>
</div>
""", unsafe_allow_html=True)

m = st.columns(5)
m[0].metric("Health score", f"{score}/100")
m[1].metric("Status", status_text)
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