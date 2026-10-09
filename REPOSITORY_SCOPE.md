# My Project — ambito esclusivo: automazioni schedulate

Questo repository è dedicato **solo** agli schedulati, ai workflow GitHub Actions che li avviano e ai file necessari per farli funzionare.

## Consentito
- `.github/workflows/`: workflow con trigger `schedule` e opzionale `workflow_dispatch`, più controlli tecnici di questi workflow.
- `scripts/`: script richiamati direttamente o indirettamente dai workflow schedulati.
- `data/`: cursori, risultati pubblici e stato di esecuzione delle automazioni.
- `config/`, `tests/`, `requirements.txt`, `pyproject.toml`: configurazione, dipendenze e test degli schedulati.
- `README.md` e documentazione delle automazioni.

## Escluso
- Progetti applicativi non schedulati, come il connettore MEGA, Academy, landing page, siti, app Android e relativi sorgenti.
- Credenziali e dati personali nei commit o negli artifact pubblici.
- Codici ambassador privati, DM, API key e token.

I progetti non schedulati rimangono nei loro repository dedicati. Ogni nuovo workflow deve indicare chiaramente frequenza, input, output, stato/cursore e criteri di errore.

**Nota:** il controllo automatico segnala file fuori ambito, ma da solo non impedisce a un amministratore di effettuare un push. Per bloccare i merge sono necessarie regole di protezione del branch GitHub.
