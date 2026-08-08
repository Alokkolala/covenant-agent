# Covenant compliance agent

## Start

Requires Python 3.10+ and an OpenRouter API key.

```powershell
python -m pip install -r requirements.txt
$env:OPENROUTER_API_KEY = '<your-key>'
python agent.py --data 'C:\path\to\dataset'
```

The completed file is written to `submission_agents.json`.

Useful options:

```text
--out PATH          output JSON path
--model MODEL       OpenRouter model slug
--scenario ID       solve one borrower; repeatable
--concurrency N     parallel borrowers (default: 4)
--force             rebuild and re-solve existing packets
--self-check        verify the agent loop without API calls
```
