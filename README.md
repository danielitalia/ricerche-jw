# Ricerche JW

App locale per organizzare ricerche da jw.org e wol.jw.org per categoria, sottolineare i passaggi importanti e salvare come PDF.

## Avvio

```bash
cd ~/Desktop/ricerche-jw
python3 app.py
```

Poi apri nel browser: http://127.0.0.1:5000

## Uso

1. Crea una categoria (es. "Fede", "Famiglia")
2. Apri la categoria e incolla un link da `jw.org` o `wol.jw.org`
3. L'articolo viene scaricato e salvato in locale
4. Aprilo, seleziona col mouse il testo da sottolineare, premi **Sottolinea selezione** (o tasto `S`)
5. **Salva come PDF** apre la vista stampabile → `Cmd+P` → "Salva come PDF"

## Scorciatoie da tastiera

- `S` — sottolinea selezione
- `U` — rimuovi sottolineatura dalla selezione
- `Cmd+S` — salva manualmente (le modifiche vengono comunque salvate in automatico)

## Dati

- Database SQLite: `ricerche.db` nella stessa cartella
- Solo link da `jw.org` e `wol.jw.org` sono accettati
