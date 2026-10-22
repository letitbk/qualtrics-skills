# qualtrics-skills

Two skills for building a Qualtrics survey with an agent and proving it works before real respondents see it.

## The problem

Qualtrics surveys fail quietly. A question never shows, a branch is always false, an answer is not stored, or JavaScript does not fire. Clicking through the preview by hand misses most of these, and an agent editing a survey through the API can break routing without noticing.

## What it does

`qualtrics-survey` builds and edits surveys through the Qualtrics v3 API and QuestionJS: questions and blocks, survey flow, embedded data, display logic, Loop and Merge, publishing, response export and test row cleanup. It snapshots the definition before every change so you can see and undo what changed.

`survey-persona-audit` tests a survey before fielding. Each scripted respondent (a persona) follows one route through the live survey in a browser. The exported data is then checked cell by cell against what each persona should have produced, and a second agent reviews every trace against a written rules file. The export is the evidence, not the page.

## Install

Claude Code, as a plugin:

```bash
claude plugin marketplace add letitbk/qualtrics-skills
claude plugin install qualtrics-skills@qualtrics-skills --scope user
```

Codex and other agents that read Agent Skills:

```bash
npx skills add letitbk/qualtrics-skills -g -a codex
```

Replace `codex` with `claude-code` to install the plain skill folders for Claude Code instead of the plugin. You can also copy the folders under `skills/` into `~/.claude/skills/` or `~/.codex/skills/` by hand.

This repository holds `qualtrics-survey` and `survey-persona-audit`. Start a new agent session after installing, then ask for the task in ordinary words or name the skill.

## Requirements

- A Qualtrics account with API access. Set `QUALTRICS_API_KEY` and `QUALTRICS_BASE_URL` in your environment or in `~/.Renviron`.
- Python 3 with `requests`. The browser walker also needs Playwright with Chromium (`pip install playwright` and `playwright install chromium`).

## Limits

- The skills change real surveys. Work on a copy until you trust the workflow.
- Test responses are tagged with a prefix you choose and deleted by the cleanup script. Check the export before you delete anything.
- Personas are scripts, not samples. They test routing and storage, not how real people answer.

## License

MIT. See `LICENSE`.
