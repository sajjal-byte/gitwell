# GitWell

GitWell helps developers understand public GitHub repositories and find a good
place to start contributing. It provides a Streamlit interface and a command-line
tool for repository health checks, codebase exploration, contribution planning,
and AI-assisted documentation.

## Features

- Scan a public GitHub repository for its languages, project structure, setup
  commands, recent activity, contributors, and open starter issues.
- Review repository health and contributor-readiness scores, with prioritized
  suggestions for improvement.
- Explore likely entry points, tests, and relationships between source files.
- Ask questions about a repository or get a contribution plan for a task or
  GitHub issue.
- Generate and download repository documentation:
  - `README.md`
  - `CONTRIBUTING.md`
  - `REPO_GUIDE.md`
  - `ARCHITECTURE.md`
  - `GOOD_FIRST_ISSUES.md`
- Choose between a local Ollama model (Gemma) and Google Gemini.
- Use cached scans and generated documents to avoid repeating work.

## Requirements

- Python 3.10 or newer.
- A GitHub account is not required to scan public repositories. A GitHub token
  is recommended to increase the GitHub API rate limit.
- For AI features, configure at least one model backend:
  - **Gemma:** install and run [Ollama](https://ollama.com/), then pull the model
    configured by `OLLAMA_MODEL` (the default is `gemma4:e4b`).
  - **Gemini:** provide a Gemini API key in `GEMINI_API_KEY`.

## Setup on Windows

Open PowerShell in the project directory:

```powershell
cd path\to\GitWell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If PowerShell blocks virtual-environment activation, either allow scripts for
this terminal session:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Or use the virtual environment's Python directly without activating it:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Configure credentials and models

Create a `.env` file in the project directory if you need any of these optional
settings:

```dotenv
# Optional: helps avoid GitHub's unauthenticated API rate limit.
GITHUB_TOKEN=your_github_token

# Required only when using the Gemini backend.
GEMINI_API_KEY=your_gemini_api_key

# Optional model and local Ollama settings.
GEMINI_MODEL=gemini-2.5-flash
OLLAMA_MODEL=gemma4:e4b
OLLAMA_KEEP_ALIVE=30m
OLLAMA_NUM_CTX=8192

# Optional: choose where the local scan and document cache is stored.
# GITWELL_CACHE=.gitwell_cache
```

Keep `.env` private; it is excluded from Git. Do not commit API keys or tokens.
The default cache is stored in `.gitwell_cache/` and is also excluded from Git.

### Ollama / Gemma

Install Ollama, then pull the configured model in a separate terminal:

```powershell
ollama pull gemma4:e4b
```

Ollama must be running when you use Gemma. The app connects to the local Ollama
service; model generation speed depends on the selected model and available
hardware. If you use a different installed model, set `OLLAMA_MODEL` in `.env`
to its Ollama model name.

### Gemini

Set `GEMINI_API_KEY` in `.env` to use Gemini. The app sends repository context
to the Gemini service when you request AI-generated documents or answers.
Repository scanning itself reads public GitHub data.

## Run the Streamlit app

From the project directory, with the virtual environment active:

```powershell
streamlit run app.py
```

Streamlit prints a local URL in the terminal; open it in your browser. Paste a
public GitHub repository URL (for example,
`https://github.com/sajjal-byte/gitwell`) and select **Analyze**. Choose the AI
backend in the sidebar, then use the tabs to explore the repository, generate
docs, or ask questions.

To stop the app, return to the terminal running Streamlit and press **Ctrl+C**.
To start it again, run `streamlit run app.py` from the project directory.

## Command-line usage

The CLI scans a repository and writes a report and generated documentation to
`gitwell-output/` by default:

```powershell
python gitwell.py sajjal-byte/gitwell
```

The CLI uses Gemini by default. To use local Gemma instead:

```powershell
python gitwell.py sajjal-byte/gitwell --backend gemma
```

To generate a contribution plan for a task:

```powershell
python gitwell.py sajjal-byte/gitwell --plan "Add retries to the API client"
```

You can also pass a GitHub issue URL:

```powershell
python gitwell.py sajjal-byte/gitwell --plan https://github.com/owner/repo/issues/42
```

Useful options:

| Option | Description |
| --- | --- |
| `--backend gemini` or `--backend gemma` | Select the AI backend. |
| `--out DIR` | Choose the output directory; defaults to `gitwell-output`. |
| `--plan TASK_OR_ISSUE_URL` | Locate likely files to change and print a contribution plan. |
| `--write-to DIR` | Also write generated documentation into a local repository clone. |
| `--force` | Allow `--write-to` to overwrite existing documentation files. |
| `--no-cache` | Ignore cached scans and generated documents for this run. |

For example, to analyze and write generated docs into a local clone:

```powershell
python gitwell.py sajjal-byte/gitwell --backend gemma --write-to path\to\your\clone
```

The CLI writes a `REPORT.md` health report and the generated docs into the output
directory. With `--write-to`, docs are placed in the clone root or its `docs/`
directory according to the document type.

## GitHub API rate limits

Unauthenticated requests to the GitHub API have a lower rate limit. If scanning
fails with a rate-limit message, add `GITHUB_TOKEN` to `.env` and retry. For
public-repository access, use a token with only the permissions you need.

## Project layout

```text
GitWell/
├── app.py                    # Streamlit user interface
├── gitwell.py                # Repository analysis, AI, cache, and CLI logic
├── requirements.txt          # Python dependencies
└── .streamlit/
    └── config.toml           # Streamlit theme
```

## Troubleshooting

- **`streamlit` is not recognized:** activate `.venv` or run
  `.\.venv\Scripts\python.exe -m streamlit run app.py`.
- **Ollama is not reachable:** start Ollama and confirm the configured model is
  installed with `ollama list`.
- **The model is missing:** pull it with `ollama pull <model-name>`; the default
  is `gemma4:e4b`.
- **Gemini key error:** check that `.env` contains a valid `GEMINI_API_KEY`.
- **GitHub rate-limit error:** set `GITHUB_TOKEN` in `.env` and try again.
- **Unexpected cached results:** use the CLI's `--no-cache` option. Cache files
  are stored under `.gitwell_cache/` by default.

## Development

Install the dependencies as described above, then run the app with
`streamlit run app.py`. The repository does not currently include an automated
test suite.

## License

GitWell is distributed under the MIT License. See [LICENSE](LICENSE).
