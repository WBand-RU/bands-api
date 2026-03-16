# Running tests faster

Use xdist to run tests in parallel (CPU-bound mostly on DB setup):

```bash
pytest -n auto
```

If you want to keep strict asyncio mode (current config), leave as is; no extra flags required.
