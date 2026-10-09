# My Project — solo schedulati

Questo repository è riservato esclusivamente ad automazioni pianificate, workflow GitHub Actions, relativi script e stati di esecuzione. Non usarlo per il connettore MEGA o altri progetti.

## Go&Dance ogni ora
Il workflow `.github/workflows/godance-hourly.yml` parte al minuto tredici di ogni ora UTC e può essere eseguito manualmente dalla scheda Actions. Lo script `scripts/godance_live.py` richiede direttamente le pagine pubbliche del sito, non utilizza copie indicizzate e registra i progressi nella directory `data/godance/`.

Il cursore riprende la pagina ancora da completare. Dopo un giro completo, il giro successivo ricomincia dalla prima pagina. Il file `last_run.json` contiene pagina, stato, errori e copertura dichiarata. Una pagina inaccessibile non è considerata acquisita.

## Collegamento Google Sheets
In Settings → Secrets and variables → Actions, configurare i repository secrets:
- `GOOGLE_SERVICE_ACCOUNT_JSON`: JSON dell'account di servizio Google (non nel codice).
- `GODANCE_SHEET_ID`: ID del Google Sheet Go&Dance esistente.

Condividere il foglio con l'email dell'account di servizio (Editor). Se configurato, lo script aggiorna soltanto le schede `GITHUB_LIVE_EVENTS` e `GITHUB_LIVE_STATUS`; non sovrascrive i master esistenti. Senza credenziali i dati restano salvati su GitHub, ma non vengono sincronizzati in Sheets.

GitHub Actions è una schedulazione best-effort: può partire in ritardo o saltare un'ora. Inoltre alcune pagine possono richiedere JavaScript, sessioni o limitare gli accessi automatici. In quel caso occorre adattare lo script, senza segnare la scansione come completata.
