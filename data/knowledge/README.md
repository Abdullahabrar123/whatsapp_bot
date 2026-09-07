# Knowledge base

Drop `.md` or `.txt` files in this folder to give the bot extra facts beyond
`config/business.yaml`.

## Rules

* One topic per file. Keep files short and factual.
* Separate topics with a blank line — the indexer chunks on blank lines.
* Start each file with a `# Heading`; it becomes the citation title.
* **Never** put secrets, tokens, or customer personal data in here.
* Files over 512 KB are skipped.

## Rebuilding

After adding or editing files, rebuild the index:

```bash
make reindex
# or
python scripts/rebuild_index.py
```

The index is rebuilt automatically at application startup.

## Citations

Each chunk gets a stable id (`file:aftercare:0`) that appears in retrieval
debug output, so you can trace exactly which text produced an answer.
