# Covenant compliance agent

## Overview

Reads borrower documents and ledger data, evaluates covenant compliance, and writes the completed results to `submission_agents.json`.

## Start

Requires Python 3.10+ and an OpenRouter API key.

```powershell
python -m pip install -r requirements.txt
$env:OPENROUTER_API_KEY = '<your-key>'
python agent.py --data 'C:\path\to\dataset'
```
